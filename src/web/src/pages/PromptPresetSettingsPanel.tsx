import { Checkbox } from "../design-system";
import { Button, TextInput, Textarea } from "../design-system";
import { SettingsSelect } from "../components/SettingsSelect";
import { queryClient } from "../queryClient";
import { resourceQueries } from "../features/resources/resourceQueries";
import { useQuery } from "@tanstack/react-query";
import { createClientId } from "../utils/clientId.js";
import { useEffect, useState } from "react";
import { Download, Plus, Upload, Trash2 } from "lucide-react";
import { settingsClient as api } from "../features/settings/settingsClient";
import { useSettingsStore } from "../store/settingsStore";
import type {
  PromptImportPreview,
  PromptPreset,
  PromptSegment,
  PromptTransform,
} from "../types";
import {
  ImportCompatibilityReport,
  type ImportCompatibility,
} from "../components/ImportCompatibilityReport";

const uid = (prefix: string) => `${prefix}-${createClientId()}`;
const blank = (): PromptPreset => ({
  format: "mrp.prompt_preset",
  id: uid("preset"),
  name: "新提示词方案",
  description: "",
  source: "local",
  revision: 1,
  segments: [],
  transforms: [],
  extensions: {},
});
const segment = (order: number): PromptSegment => ({
  id: uid("seg"),
  name: "新片段",
  content: "",
  enabled: true,
  anchor: "system",
  depth: 0,
  order,
});

export default function PromptPresetSettingsPanel() {
  const settings = useSettingsStore((s) => s.settings);
  const patchSettings = useSettingsStore((s) => s.patch);
  const { data: rows = [] } = useQuery(resourceQueries.promptPresets());
  const [draft, setDraft] = useState<PromptPreset | null>(null);
  const [preview, setPreview] = useState<PromptImportPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const refresh = async () => {
    await queryClient.invalidateQueries({
      queryKey: resourceQueries.promptPresets().queryKey,
    });
    await queryClient.fetchQuery(resourceQueries.promptPresets());
  };
  useEffect(() => {
    void refresh().catch((e) => setError(String(e)));
  }, []);
  const modify = (value: Partial<PromptPreset>) =>
    setDraft((current) => (current ? { ...current, ...value } : current));
  const updateSegment = (id: string, value: Partial<PromptSegment>) =>
    modify({
      segments: (draft?.segments ?? []).map((item) =>
        item.id === id ? { ...item, ...value } : item,
      ),
    });
  const updateTransform = (id: string, value: Partial<PromptTransform>) =>
    modify({
      transforms: (draft?.transforms ?? []).map((item) =>
        item.id === id ? { ...item, ...value } : item,
      ),
    });
  const save = async () => {
    if (!draft || busy) return;
    if (!draft.name.trim()) {
      setError("请填写方案名称");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await api.savePromptPreset(draft);
      setDraft(saved);
      setPreview(null);
      await refresh();
      setNotice("方案已保存；启用后用于下一轮生成。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!draft || busy || !window.confirm(`删除提示词方案「${draft.name}」？`))
      return;
    setBusy(true);
    setError("");
    try {
      await api.deletePromptPreset(draft.id);
      setDraft(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      setBusy(false);
    }
  };
  const importFile = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.previewPromptPresetImport(file);
      setPreview(result);
      setDraft(result.draft);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入解析失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="v7-settings-title">
        <div>
          <h2>提示词方案</h2>
          <p>
            组合、排序和启用文本片段。角色卡、世界书与破甲词仍由各自设置控制。
          </p>
        </div>
      </div>
      <div className="v7-settings-card">
        <div className="v7-settings-actions">
          <SettingsSelect
            aria-label="选择提示词方案"
            value={draft?.id ?? ""}
            onValueChange={(e) =>
              setDraft(rows.find((item) => item.id === e) ?? null)
            }
          >
            <option value="">选择已保存方案</option>
            {rows.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </SettingsSelect>
          <Button
            variant="secondary"
            type="button"
            onClick={() => {
              setDraft(blank());
              setPreview(null);
            }}
          >
            <Plus size={15} /> 新建
          </Button>
          <Button
            variant="secondary"
            type="button"
            disabled={!draft}
            onClick={() => {
              if (draft)
                setDraft({
                  ...draft,
                  id: uid("preset"),
                  name: `${draft.name} · 副本`,
                  revision: 1,
                  source: "local",
                  segments: draft.segments.map((item) => ({
                    ...item,
                    id: uid("seg"),
                  })),
                  transforms: draft.transforms.map((item) => ({
                    ...item,
                    id: uid("transform"),
                  })),
                });
              setPreview(null);
            }}
          >
            复制当前方案
          </Button>
          <label className="ui-btn ui-btn--secondary">
            <Upload size={15} /> 导入预设 JSON
            <input
              type="file"
              accept=".json,application/json"
              hidden
              onChange={(e) => void importFile(e.target.files?.[0])}
            />
          </label>
        </div>
        {preview && (
          <div className="v7-notice" role="status">
            <strong>导入草稿尚未保存，也未启用。</strong>
            <ImportCompatibilityReport report={preview} />
          </div>
        )}
        {!preview && draft?.extensions["mrp.import_report"] != null && (
          <ImportCompatibilityReport
            report={
              draft.extensions["mrp.import_report"] as ImportCompatibility
            }
          />
        )}
        {draft && (
          <>
            <div className="v7-ai-two-column">
              <label className="v7-field">
                名称
                <TextInput
                  value={draft.name}
                  onChange={(e) => modify({ name: e.target.value })}
                />
              </label>
              <label className="v7-field">
                说明
                <TextInput
                  value={draft.description}
                  onChange={(e) => modify({ description: e.target.value })}
                />
              </label>
            </div>
            <div className="v7-ai-section-head">
              <span>片段 · 按顺序发送</span>
              <Button
                variant="secondary"
                type="button"
                className="v7-provider-refresh"
                onClick={() =>
                  modify({
                    segments: [
                      ...draft.segments,
                      segment(draft.segments.length * 10 + 100),
                    ],
                  })
                }
              >
                <Plus size={14} /> 添加片段
              </Button>
            </div>
            {[...draft.segments]
              .sort((a, b) => a.order - b.order)
              .map((item) => (
                <section key={item.id} className="v7-ai-section">
                  <div className="v7-ai-two-column">
                    <label className="v7-field">
                      片段名称
                      <TextInput
                        value={item.name}
                        onChange={(e) =>
                          updateSegment(item.id, { name: e.target.value })
                        }
                      />
                    </label>
                    <label className="v7-field">
                      插入位置
                      <SettingsSelect
                        value={item.anchor}
                        onValueChange={(e) =>
                          updateSegment(item.id, {
                            anchor: e as PromptSegment["anchor"],
                          })
                        }
                      >
                        <option value="system">系统上下文</option>
                        <option value="at_depth">历史消息间</option>
                        <option value="near">回复前</option>
                      </SettingsSelect>
                    </label>
                  </div>
                  <label className="v7-field">
                    指令内容
                    <Textarea
                      rows={5}
                      value={item.content}
                      onChange={(e) =>
                        updateSegment(item.id, { content: e.target.value })
                      }
                      placeholder="可用 {{char}} / {{character}}、{{user}}、{{story}}、{{original}}"
                    />
                  </label>
                  <div className="v7-settings-actions">
                    <Checkbox
                      checked={item.enabled}
                      onChange={(e) =>
                        updateSegment(item.id, { enabled: e.target.checked })
                      }
                      label={<>启用</>}
                    />
                    <label className="v7-field">
                      顺序
                      <TextInput
                        type="number"
                        value={item.order}
                        onChange={(e) =>
                          updateSegment(item.id, {
                            order: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                    {item.anchor === "at_depth" && (
                      <label className="v7-field">
                        深度
                        <TextInput
                          type="number"
                          min={0}
                          value={item.depth}
                          onChange={(e) =>
                            updateSegment(item.id, {
                              depth: Number(e.target.value),
                            })
                          }
                        />
                      </label>
                    )}
                    <Button
                      variant="secondary"
                      type="button"
                      onClick={() =>
                        modify({
                          segments: draft.segments.filter(
                            (part) => part.id !== item.id,
                          ),
                        })
                      }
                    >
                      <Trash2 size={14} /> 删除片段
                    </Button>
                  </div>
                </section>
              ))}
            <div className="v7-ai-section-head">
              <span>发送前正则替换</span>
              <Button
                variant="secondary"
                type="button"
                className="v7-provider-refresh"
                onClick={() =>
                  modify({
                    transforms: [
                      ...(draft.transforms ?? []),
                      {
                        id: uid("transform"),
                        name: "新替换",
                        pattern: "示例文本",
                        replacement: "",
                        enabled: false,
                        phase: "before_send",
                      },
                    ],
                  })
                }
              >
                <Plus size={14} /> 添加替换
              </Button>
            </div>
            <p className="v7-muted">
              只作用于将发送的提示词。每项有执行时间和长度限制；外部脚本不会运行。
            </p>
            {(draft.transforms ?? []).map((item) => (
              <section key={item.id} className="v7-ai-section">
                <div className="v7-ai-two-column">
                  <label className="v7-field">
                    名称
                    <TextInput
                      value={item.name}
                      onChange={(e) =>
                        updateTransform(item.id, { name: e.target.value })
                      }
                    />
                  </label>
                  <label className="v7-field">
                    正则表达式
                    <TextInput
                      value={item.pattern}
                      onChange={(e) =>
                        updateTransform(item.id, { pattern: e.target.value })
                      }
                    />
                  </label>
                </div>
                <label className="v7-field">
                  替换文本
                  <TextInput
                    value={item.replacement}
                    onChange={(e) =>
                      updateTransform(item.id, { replacement: e.target.value })
                    }
                  />
                </label>
                <div className="v7-settings-actions">
                  <Checkbox
                    checked={item.enabled}
                    onChange={(e) =>
                      updateTransform(item.id, { enabled: e.target.checked })
                    }
                    label={<>启用</>}
                  />
                  <Button
                    variant="secondary"
                    type="button"
                    onClick={() =>
                      modify({
                        transforms: draft.transforms.filter(
                          (part) => part.id !== item.id,
                        ),
                      })
                    }
                  >
                    <Trash2 size={14} /> 删除替换
                  </Button>
                </div>
              </section>
            ))}
            <div className="v7-settings-actions">
              <Button
                variant="primary"
                type="button"
                disabled={busy}
                onClick={() => void save()}
              >
                {busy ? "保存中…" : "保存方案"}
              </Button>
              <Button
                variant="secondary"
                type="button"
                disabled={busy || !rows.some((item) => item.id === draft.id)}
                onClick={() =>
                  void patchSettings({
                    active_prompt_preset_id:
                      settings?.active_prompt_preset_id === draft.id
                        ? null
                        : draft.id,
                  })
                }
              >
                {settings?.active_prompt_preset_id === draft.id
                  ? "取消全局启用"
                  : "全局启用"}
              </Button>
              {rows.some((item) => item.id === draft.id) && (
                <a
                  className="ui-btn ui-btn--secondary"
                  href={`/api/v1/prompt-presets/${encodeURIComponent(draft.id)}/export`}
                >
                  <Download size={14} /> 导出
                </a>
              )}
              {rows.some((item) => item.id === draft.id) && (
                <Button
                  variant="secondary"
                  type="button"
                  disabled={busy}
                  onClick={() => void remove()}
                >
                  <Trash2 size={14} /> 删除方案
                </Button>
              )}
            </div>
          </>
        )}
        {error && (
          <p className="v7-error" role="alert">
            {error}
          </p>
        )}
        {notice && (
          <p className="v7-success" role="status">
            {notice}
          </p>
        )}
      </div>
    </>
  );
}
