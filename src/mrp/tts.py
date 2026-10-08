"""Local Fish Speech runtime, voice profiles, and generated-audio cache."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from mrp.settings import TTSSettings

logger = logging.getLogger("mrp.tts")


class VoiceProfile(BaseModel):
    id: str
    name: str
    filename: str
    transcript: str
    sample_sha256: str
    content_type: str
    created_at: str


class VoiceProfileStore:
    def __init__(self, data_root: Path):
        self.root = Path(data_root) / "tts"
        self.voices_dir = self.root / "voices"
        self.cache_dir = self.root / "cache"
        self.index_file = self.root / "voices.json"
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[VoiceProfile]:
        try:
            raw = json.loads(self.index_file.read_text(encoding="utf-8"))
            return [VoiceProfile.model_validate(row) for row in raw]
        except FileNotFoundError:
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("无法读取音色档案索引：%s", exc)
            return []

    def get(self, voice_id: str) -> VoiceProfile | None:
        return next((row for row in self.list() if row.id == voice_id), None)

    def add(self, name: str, transcript: str, raw: bytes, filename: str) -> VoiceProfile:
        if not name.strip():
            raise ValueError("请填写音色名称")
        if not transcript.strip():
            raise ValueError("参考音频必须填写对应原文")
        if len(raw) > 30 * 1024 * 1024:
            raise ValueError("参考音频不能超过 30 MB")
        if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
            suffix, content_type = ".wav", "audio/wav"
        elif raw[:3] == b"ID3" or (len(raw) > 1 and raw[0] == 0xFF and raw[1] & 0xE0 == 0xE0):
            suffix, content_type = ".mp3", "audio/mpeg"
        else:
            raise ValueError("只支持有效的 WAV 或 MP3 音频")
        sample_hash = hashlib.sha256(raw).hexdigest()
        voice_id = "voice-" + sample_hash[:16]
        target = self.voices_dir / f"{voice_id}{suffix}"
        target.write_bytes(raw)
        profile = VoiceProfile(
            id=voice_id,
            name=name.strip()[:80],
            filename=target.name,
            transcript=transcript.strip()[:12000],
            sample_sha256=sample_hash,
            content_type=content_type,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        rows = [row for row in self.list() if row.id != voice_id]
        rows.append(profile)
        self._save(rows)
        return profile

    def delete(self, voice_id: str) -> bool:
        rows = self.list()
        profile = next((row for row in rows if row.id == voice_id), None)
        if profile is None:
            return False
        path = self.voices_dir / profile.filename
        if path.is_file():
            path.unlink()
        self._save([row for row in rows if row.id != voice_id])
        return True

    def sample_bytes(self, profile: VoiceProfile) -> bytes:
        path = (self.voices_dir / profile.filename).resolve()
        if not path.is_relative_to(self.voices_dir.resolve()):
            raise ValueError("音色文件路径无效")
        return path.read_bytes()

    def cache_path(self, text: str, profile: VoiceProfile, emotion: str) -> Path:
        key = hashlib.sha256(
            f"{text}\0{profile.sample_sha256}\0{profile.transcript}\0{emotion}".encode("utf-8")
        ).hexdigest()
        return self.cache_dir / f"{key}.wav"

    def _save(self, rows: list[VoiceProfile]) -> None:
        tmp = self.index_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([row.model_dump(mode="json") for row in rows], ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.index_file)


class FishSpeechManager:
    """Owns a child process when possible and reports progress emitted by the runtime."""

    def __init__(self, settings: TTSSettings):
        self.settings = settings
        self._lock = asyncio.Lock()
        self._synthesis_lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._load_task: asyncio.Task | None = None
        self._status: dict[str, Any] = {
            "state": "disabled", "progress": 0, "stage": "语音模型未启用",
            "error": None, "managed": False,
        }

    async def start_background(self) -> dict[str, Any]:
        async with self._lock:
            if self._load_task and not self._load_task.done():
                return self.status()
            if self._status["state"] == "ready":
                return self.status()
            self._status = {"state": "starting", "progress": 0, "stage": "正在启动本地语音服务", "error": None, "managed": False}
            self._load_task = asyncio.create_task(self._load())
            return self.status()

    async def _load(self) -> None:
        cfg = self.settings
        try:
            if await self._healthy():
                self._status = {"state": "ready", "progress": 100, "stage": "本地语音服务已就绪", "error": None, "managed": False}
                return
            executable = Path(cfg.executable_path)
            model = Path(cfg.model_path)
            tokenizer = Path(cfg.tokenizer_path)
            missing = [str(path) for path in (executable, model, tokenizer) if not path.is_file()]
            if missing:
                raise FileNotFoundError("找不到本地 Fish Speech 文件：" + "；".join(missing))
            if await self._port_open():
                raise RuntimeError(f"端口 {cfg.port} 已被其他服务占用；请关闭该服务或更改语音服务端口")
            env = os.environ.copy()
            runtime_dir = str(executable.parent.parent / "bin" / "Release")
            search_paths = [runtime_dir]
            if env.get("CUDA_PATH"):
                search_paths.append(str(Path(env["CUDA_PATH"]) / "bin"))
            env["PATH"] = os.pathsep.join((*search_paths, env.get("PATH", "")))
            args = [str(executable), "--model", str(model), "--tokenizer", str(tokenizer), "--cuda", "0", "--gpu-layers", "-1", "--server", "--host", cfg.host, "--port", str(cfg.port), "--log-level", "info"]
            self._process = await asyncio.create_subprocess_exec(
                *args, cwd=str(executable.parent.parent.parent), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            self._status.update(state="loading", stage="加载语音模型", managed=True)
            self._reader = asyncio.create_task(self._read_progress())
            deadline = asyncio.get_running_loop().time() + 360
            while asyncio.get_running_loop().time() < deadline:
                if self._process.returncode is not None:
                    raise RuntimeError("Fish Speech 启动失败，请检查模型文件和显卡状态")
                if await self._healthy():
                    self._status.update(state="ready", progress=100, stage="模型已就绪", error=None)
                    return
                await asyncio.sleep(0.4)
            raise TimeoutError("Fish Speech 模型加载超时")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fish Speech 启动失败")
            self._status.update(state="error", error=str(exc), stage="语音服务启动失败")

    async def _read_progress(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        pattern = re.compile(rb"MRP_TTS_PROGRESS\s+(\d+)\s+(.+)")
        while line := await process.stdout.readline():
            match = pattern.search(line)
            if match:
                self._status.update(
                    state="loading",
                    progress=max(0, min(99, int(match.group(1)))),
                    stage=match.group(2).decode("utf-8", "replace").strip(),
                )
            else:
                logger.debug("Fish Speech: %s", line.decode("utf-8", "replace").rstrip())

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            process = self._process
            self._process = None
            if self._load_task and not self._load_task.done():
                self._load_task.cancel()
                await asyncio.gather(self._load_task, return_exceptions=True)
            if process and process.returncode is None:
                self._status.update(state="stopping", progress=0, stage="正在关闭语音模型")
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=12)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            if self._reader and not self._reader.done():
                self._reader.cancel()
            self._status = {"state": "disabled", "progress": 0, "stage": "语音模型已关闭", "error": None, "managed": False}
            return self.status()

    async def _healthy(self) -> bool:
        url = f"http://{self.settings.host}:{self.settings.port}/health"
        try:
            async with httpx.AsyncClient(timeout=0.8) as client:
                response = await client.get(url)
                return response.status_code == 200 and response.json().get("ok") is True
        except (httpx.HTTPError, ValueError):
            return False

    async def _port_open(self) -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.settings.host, self.settings.port), timeout=0.4
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    def status(self) -> dict[str, Any]:
        if self._process and self._process.returncode is not None and self._status["state"] not in ("disabled", "error"):
            self._status.update(state="error", progress=0, stage="语音服务已退出", error="模型进程意外结束")
        return {**self._status}

    async def synthesize(self, text: str, profile: VoiceProfile, store: VoiceProfileStore, emotion: str = "") -> bytes:
        if self.status()["state"] != "ready":
            raise RuntimeError("语音模型尚未就绪，请先在设置中启用")
        async with self._synthesis_lock:
            cache_path = store.cache_path(text, profile, emotion)
            if cache_path.is_file():
                return cache_path.read_bytes()
            rendered_text = f"[{emotion}] {text}" if emotion else text
            files = {"reference": (profile.filename, store.sample_bytes(profile), profile.content_type)}
            data = {
                "text": rendered_text,
                "reference_text": profile.transcript,
                "params": json.dumps({"max_new_tokens": 512, "temperature": 0.58, "top_p": 0.88}),
            }
            url = f"http://{self.settings.host}:{self.settings.port}/generate"
            async with httpx.AsyncClient(timeout=300) as client:
                response = await client.post(url, data=data, files=files)
            if response.status_code != 200:
                raise RuntimeError("语音生成失败：" + response.text[:500])
            audio = response.content
            if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
                raise RuntimeError("语音服务返回的内容不是有效 WAV 音频")
            tmp = cache_path.with_suffix(".wav.tmp")
            tmp.write_bytes(audio)
            tmp.replace(cache_path)
            return audio
