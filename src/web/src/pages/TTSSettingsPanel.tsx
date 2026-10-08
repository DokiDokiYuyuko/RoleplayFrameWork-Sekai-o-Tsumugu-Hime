import { Button, TextInput, Textarea } from "../design-system";
import { PageArt } from "../appearance/PageArt";
import { useQuery } from "@tanstack/react-query";
import { resourceQueries } from "../features/resources/resourceQueries";
import { queryClient } from "../queryClient";
import { useCallback, useEffect, useState } from "react";
import {
  AudioLines,
  Check,
  LoaderCircle,
  Mic2,
  Plus,
  Radio,
  Trash2,
  Volume2,
} from "lucide-react";
import { settingsClient as api } from "../features/settings/settingsClient";
import type { TTSStatus } from "../types";

export default function TTSSettingsPanel() {
  const { data: status = null } = useQuery(resourceQueries.tts());
  const setStatus = (value: TTSStatus) =>
    queryClient.setQueryData(resourceQueries.tts().queryKey, value);
  const voiceQuery = useQuery(resourceQueries.voices());
  const voices = voiceQuery.data?.voices ?? [];
  const [defaultId, setDefaultId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [transcript, setTranscript] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refreshVoices = useCallback(async () => {
    const result = await queryClient.fetchQuery({
      ...resourceQueries.voices(),
      staleTime: 0,
    });
    setDefaultId(result.default_voice_profile_id);
  }, []);
  const refreshStatus = useCallback(
    async () =>
      setStatus(
        await queryClient.fetchQuery({
          ...resourceQueries.tts(),
          staleTime: 0,
        }),
      ),
    [],
  );

  useEffect(() => {
    void Promise.all([refreshStatus(), refreshVoices()]).catch((cause) =>
      setError(cause instanceof Error ? cause.message : "语音设置读取失败"),
    );
  }, [refreshStatus, refreshVoices]);
  useEffect(() => {
    if (!status || !["starting", "loading", "stopping"].includes(status.state))
      return;
    const timer = window.setInterval(
      () => void refreshStatus().catch(() => undefined),
      700,
    );
    return () => window.clearInterval(timer);
  }, [status?.state, refreshStatus]);

  const toggleModel = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      setStatus(
        status?.enabled ? await api.disableTTS() : await api.enableTTS(),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "语音模型操作失败");
    } finally {
      setBusy(false);
    }
  };
  const addVoice = async () => {
    if (!file || !name.trim() || !transcript.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const voice = await api.uploadVoiceProfile(name, transcript, file);
      await refreshVoices();
      setName("");
      setTranscript("");
      setFile(null);
      const input = document.getElementById(
        "tts-reference-file",
      ) as HTMLInputElement | null;
      if (input) input.value = "";
      if (!defaultId) setDefaultId(voice.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "音色保存失败");
    } finally {
      setBusy(false);
    }
  };
  const chooseDefault = async (id: string) => {
    setBusy(true);
    setError("");
    try {
      await api.setDefaultVoiceProfile(id);
      setDefaultId(id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "默认音色设置失败");
    } finally {
      setBusy(false);
    }
  };
  const removeVoice = async (id: string) => {
    setBusy(true);
    setError("");
    try {
      await api.deleteVoiceProfile(id);
      await refreshVoices();
      if (defaultId === id) setDefaultId(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "音色删除失败");
    } finally {
      setBusy(false);
    }
  };
  const loading = status?.state === "loading" || status?.state === "starting";
  const ready = status?.state === "ready";
  return (
    <>
      <div className="v7-settings-title">
        <span className="settings-section-emblem">
          <PageArt kind="voice" className="page-art--header" />
          <AudioLines size={22} />
        </span>
        <div>
          <h2>本地语音</h2>
          <p>使用本机 Fish Speech 合成角色语音；参考音色只保存在本机。</p>
        </div>
      </div>
      <section className="v7-tts-card">
        <div className="v7-tts-runtime-head">
          <div className="v7-tts-runtime-icon">
            <Radio size={19} />
          </div>
          <div className="v7-tts-runtime-copy">
            <strong>Fish Speech S2 Pro · Q8</strong>
            <span>{status?.stage ?? "正在读取模型状态"}</span>
          </div>
          <span className={`v7-tts-state is-${status?.state ?? "disabled"}`}>
            {ready
              ? "已就绪"
              : loading
                ? "加载中"
                : status?.state === "error"
                  ? "启动失败"
                  : "已关闭"}
          </span>
        </div>
        {loading && (
          <div
            className="v7-tts-progress-wrap"
            aria-label={`模型加载进度 ${status?.progress ?? 0}%`}
          >
            <div className="v7-tts-progress">
              <span
                style={{ width: `${Math.max(2, status?.progress ?? 0)}%` }}
              />
            </div>
            <div className="v7-tts-progress-label">
              <span>{status?.stage}</span>
              <strong>{status?.progress ?? 0}%</strong>
            </div>
          </div>
        )}
        {status?.state === "error" && (
          <p className="v7-tts-error">
            {status.error ?? "模型启动失败，请检查本地模型文件。"}
          </p>
        )}
        <div className="v7-tts-runtime-foot">
          <p>
            {ready
              ? "模型驻留在显卡中，对话消息下方的朗读按钮现在可用。"
              : "首次启用需要加载模型，加载期间页面会显示真实进度。"}
          </p>
          <Button
            variant="tonal"
            type="button"
            disabled={busy || loading || status?.state === "stopping"}
            onClick={() => void toggleModel()}
          >
            {busy || loading ? (
              <LoaderCircle className="animate-spin" size={15} />
            ) : status?.enabled ? (
              <Volume2 size={15} />
            ) : (
              <Mic2 size={15} />
            )}
            {status?.enabled ? "关闭语音模型" : "启用并加载模型"}
          </Button>
        </div>
      </section>

      <section className="v7-tts-card v7-tts-voices">
        <div className="v7-tts-section-head">
          <div>
            <span className="v7-eyebrow">VOICE LIBRARY</span>
            <h3>参考音色</h3>
            <p>上传短而清晰的参考音频，并填写音频中实际说出的文字。</p>
          </div>
          <span className="v7-tts-count">{voices.length} 个音色</span>
        </div>
        <div className="v7-tts-voice-list">
          {voices.length === 0 ? (
            <div className="v7-tts-empty">
              <Mic2 size={19} />
              <span>还没有音色档案</span>
              <small>先添加一段参考音频，之后每条消息都能选择朗读。</small>
            </div>
          ) : (
            voices.map((voice) => (
              <article
                className={`v7-tts-voice-row ${defaultId === voice.id ? "is-default" : ""}`}
                key={voice.id}
              >
                <div className="v7-tts-voice-mark">
                  <AudioLines size={17} />
                </div>
                <div className="v7-tts-voice-info">
                  <strong>
                    {voice.name}
                    {defaultId === voice.id && (
                      <em>
                        <Check size={11} /> 默认
                      </em>
                    )}
                  </strong>
                  <span>
                    {voice.filename} · {voice.transcript.slice(0, 56)}
                    {voice.transcript.length > 56 ? "…" : ""}
                  </span>
                </div>
                <div className="v7-tts-voice-actions">
                  <Button
                    variant="secondary"
                    type="button"
                    title="设为默认音色"
                    disabled={busy || defaultId === voice.id}
                    onClick={() => void chooseDefault(voice.id)}
                  >
                    <Check size={15} />
                  </Button>
                  <Button
                    variant="secondary"
                    type="button"
                    title="删除音色"
                    disabled={busy}
                    onClick={() => void removeVoice(voice.id)}
                  >
                    <Trash2 size={15} />
                  </Button>
                </div>
              </article>
            ))
          )}
        </div>
        <div className="v7-tts-add-form">
          <div className="v7-tts-add-title">
            <Plus size={15} />
            <strong>添加音色</strong>
            <span>WAV / MP3，最大 30 MB</span>
          </div>
          <div className="v7-tts-form-grid">
            <label className="v7-field">
              音色名称
              <TextInput
                value={name}
                maxLength={80}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：阿梓"
              />
            </label>
            <label className="v7-field">
              参考音频
              <input
                id="tts-reference-file"
                type="file"
                accept="audio/wav,audio/mpeg,.wav,.mp3"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
          </div>
          <label className="v7-field">
            参考音频原文
            <Textarea
              value={transcript}
              maxLength={12000}
              rows={3}
              onChange={(event) => setTranscript(event.target.value)}
              placeholder="准确填写音频里说出的内容，帮助模型还原音色"
            />
          </label>
          <div className="v7-tts-submit-row">
            <span>
              {file
                ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB`
                : "文件只存放在当前项目的数据目录中"}
            </span>
            <Button
              variant="primary"
              type="button"
              disabled={busy || !file || !name.trim() || !transcript.trim()}
              onClick={() => void addVoice()}
            >
              {busy ? "处理中…" : "保存音色"}
            </Button>
          </div>
        </div>
      </section>
      {error && (
        <p className="v7-error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}
