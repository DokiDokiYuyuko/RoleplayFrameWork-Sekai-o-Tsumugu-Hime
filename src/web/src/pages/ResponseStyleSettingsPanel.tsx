import { Checkbox } from "../design-system";
import { Button, TextInput, Textarea } from "../design-system";
import { SettingsSelect } from "../components/SettingsSelect";
import { createClientId } from "../utils/clientId.js";
import { useEffect, useState } from "react";
import { Plus, Copy, Download, Upload, Trash2 } from "lucide-react";
import { useSettingsStore } from "../store/settingsStore";
import type { ResponseStyle } from "../types";

const blank = (): ResponseStyle => ({
  id: `style-${createClientId()}`,
  schema_version: 1,
  revision: 1,
  name: "",
  description: "",
  content: "",
  enabled: true,
});

export default function ResponseStyleSettingsPanel() {
  const { settings, load, patch } = useSettingsStore();
  const rows = settings?.response_styles ?? [];
  const [draft, setDraft] = useState<ResponseStyle | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    void load().catch((e) => setError(String(e)));
  }, [load]);
  const modify = (value: Partial<ResponseStyle>) =>
    setDraft((s) => (s ? { ...s, ...value } : s));
  const commit = async (next: ResponseStyle[], selected?: string) => {
    if (busy || !settings) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await patch({
        response_styles: next,
        expected_response_styles_revision: settings.response_styles_revision,
      });
      const saved = useSettingsStore.getState().settings?.response_styles ?? [];
      setDraft(saved.find((s) => s.id === selected) ?? null);
      setNotice("已保存，下次生成使用新设置。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };
  const save = () => {
    if (!draft) return;
    if (!draft.name.trim() || !draft.content.trim()) {
      setError("请填写名称和风格正文。");
      return;
    }
    const next = {
      ...draft,
      name: draft.name.trim(),
      content: draft.content.trim(),
    };
    void commit([...rows.filter((s) => s.id !== draft.id), next], draft.id);
  };
  const exportFile = () => {
    if (!draft) return;
    const link = document.createElement("a");
    const url = URL.createObjectURL(
      new Blob(
        [JSON.stringify({ format: "mrp.response_style", ...draft }, null, 2)],
        { type: "application/json" },
      ),
    );
    link.href = url;
    link.download = `${draft.name.replace(/[\\/:*?"<>|]/g, "_") || "回应风格"}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const importFile = async (file?: File) => {
    if (!file) return;
    try {
      if (file.size > 200_000)
        throw new Error("文件过大，请选择单个风格 JSON。");
      const data = JSON.parse(await file.text());
      if (
        data.format !== "mrp.response_style" ||
        typeof data.name !== "string" ||
        typeof data.content !== "string"
      )
        throw new Error("不是有效的回应风格文件。");
      if ((data.schema_version ?? 1) !== 1)
        throw new Error("此风格文件格式暂不支持。");
      setDraft({
        ...blank(),
        name: data.name,
        content: data.content,
        description:
          typeof data.description === "string" ? data.description : "",
        enabled: data.enabled !== false,
      });
      setError("");
      setNotice("已读入草稿，确认后保存。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    }
  };
  const choose = (next: ResponseStyle | null) => {
    const original = rows.find((s) => s.id === draft?.id);
    if (
      draft &&
      JSON.stringify(draft) !== JSON.stringify(original) &&
      !window.confirm("放弃尚未保存的修改？")
    )
      return;
    setDraft(next);
    setError("");
    setNotice("");
  };
  return (
    <section className="v7-settings-card">
      <h2>回应风格</h2>
      <p className="v7-muted">
        管理普通剧情中的情感表达、语气和回应主动性。在故事输入区选择，人物设定与剧情事实保持独立。
      </p>
      <div className="v7-settings-actions">
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() => choose(blank())}
        >
          <Plus size={15} /> 新建风格
        </Button>
        <label className="ui-btn ui-btn--secondary">
          <Upload size={15} /> 导入 JSON
          <input
            type="file"
            accept=".json,application/json"
            hidden
            disabled={busy}
            onChange={(e) => {
              void importFile(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </label>
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() =>
            void load()
              .then(() => setNotice("已刷新风格库。"))
              .catch((e) => setError(String(e)))
          }
        >
          刷新列表
        </Button>
      </div>
      <label className="v7-field">
        已保存的风格
        <SettingsSelect
          value={rows.some((s) => s.id === draft?.id) ? draft!.id : ""}
          disabled={busy}
          onValueChange={(e) => choose(rows.find((s) => s.id === e) ?? null)}
        >
          <option value="">选择风格进行编辑</option>
          {rows.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
              {s.enabled ? "" : "（已停用）"}
            </option>
          ))}
        </SettingsSelect>
      </label>
      {!draft && (
        <p className="v7-muted">
          可以从“温柔坦率”“谨慎克制”等普通交流风格开始，自行填写正文。
        </p>
      )}
      {draft && (
        <fieldset
          disabled={busy}
          style={{ border: 0, padding: 0, minWidth: 0 }}
        >
          <label className="v7-field">
            名称
            <TextInput
              maxLength={80}
              value={draft.name}
              onChange={(e) => modify({ name: e.target.value })}
              placeholder="例如：坦率直接"
            />
          </label>
          <label className="v7-field">
            简介
            <TextInput
              maxLength={500}
              value={draft.description}
              onChange={(e) => modify({ description: e.target.value })}
              placeholder="可选，帮助区分风格"
            />
          </label>
          <label className="v7-field">
            风格正文
            <Textarea
              rows={9}
              maxLength={20000}
              value={draft.content}
              onChange={(e) => modify({ content: e.target.value })}
              placeholder="描述情感态度、说话习惯和主动性，例如：回应坦率，先回答问题，减少迂回暗示。"
            />
          </label>
          <Checkbox
            checked={draft.enabled}
            onChange={(e) => modify({ enabled: e.target.checked })}
            label={<>出现在故事候选列表中</>}
          />
          <div className="v7-settings-actions">
            <Button variant="primary" onClick={save}>
              保存风格
            </Button>
            <Button
              variant="secondary"
              onClick={() =>
                setDraft({
                  ...draft,
                  id: blank().id,
                  revision: 1,
                  name: `${draft.name} 副本`,
                })
              }
            >
              <Copy size={15} /> 复制
            </Button>
            <Button variant="secondary" onClick={exportFile}>
              <Download size={15} /> 导出草稿
            </Button>
            {rows.some((s) => s.id === draft.id) && (
              <Button
                variant="secondary"
                onClick={() => {
                  if (
                    window.confirm(
                      `删除「${draft.name}」？使用它的分支与角色将跟随角色自身设定。`,
                    )
                  )
                    void commit(rows.filter((s) => s.id !== draft.id));
                }}
              >
                <Trash2 size={15} /> 删除
              </Button>
            )}
          </div>
        </fieldset>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && (
        <p role="status" className="v7-muted">
          {notice}
        </p>
      )}
    </section>
  );
}
