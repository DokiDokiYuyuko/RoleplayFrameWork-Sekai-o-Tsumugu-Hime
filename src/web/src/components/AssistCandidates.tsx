import { storyRuntime } from '../features/stories/storyRuntime';
import { useState } from "react";
import { storiesClient as api } from '../features/stories/storiesClient';
import { useChatStore } from "../store/chatStore";
import { useComposerStore } from "../store/composerStore";
import { useActiveCharacters } from "../store/useActiveCharacters";
import type { OptionChoice } from "../types";
import { Switch } from "./common";

/** R27.4 选项风格（后端 options_style 契约值，作用于对话接话候选） */
type AssistStyle = "action" | "dialogue" | "mixed";

const STYLE_LABELS: Record<AssistStyle, string> = {
  action: "行动向",
  dialogue: "对话向",
  mixed: "混合",
};

/**
 * M12-R41 候选区（2026-09-24 修订：**只由点击触发**）。
 *
 * - 「生成候选」：冷启动点（开场/切场景后）→ 开场/新场景候选；对话中 → 接话候选；
 *   每次点击都是一次全新生成（=换一批，无额度）
 * - 「✕ 关闭本批」：清空当前显示，不点生成不会再出现（零打扰）
 * - 点击候选：默认填入输入框待确认；会话级「直发」偏好开启时点击即发送
 */
export function AssistCandidates() {
  const options = useChatStore((s) => s.options);
  const source = useChatStore((s) => s.optionsSource);
  const kind = useChatStore((s) => s.optionsKind);
  const anchor = useChatStore((s) => s.optionsAnchor);
  const notice = useChatStore((s) => s.optionsNotice);
  const generating = useChatStore((s) => s.generatingCandidates);
  const generate = useChatStore((s) => s.generateCandidates);
  const dismiss = useChatStore((s) => s.dismissOptions);
  const sending = useChatStore((s) => s.sending);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const session = useChatStore((s) =>
    s.sessions.find((x) => x.id === s.currentSessionId),
  );
  const characters = useActiveCharacters();

  const enabled = session?.options_enabled ?? true;
  const style = (session?.options_style as AssistStyle | undefined) ?? "mixed";
  const directSend = session?.options_direct_send ?? false;
  const [pending, setPending] = useState<{
    enabled?: boolean;
    style?: string;
    directSend?: boolean;
  }>({});
  const shownEnabled = pending.enabled ?? enabled;
  const shownStyle = (pending.style as AssistStyle | undefined) ?? style;
  const shownDirectSend = pending.directSend ?? directSend;

  /** 乐观更新 store session + PATCH 后端；失败回滚到点击前的值 */
  const patchOptions = async (patch: {
    options_enabled?: boolean;
    options_style?: string;
    options_direct_send?: boolean;
  }) => {
    if (!currentSessionId) return;
    setPending({
      enabled: patch.options_enabled,
      style: patch.options_style,
      directSend: patch.options_direct_send,
    });
    const rollbackPatch = storyRuntime.optimisticSessionPatch(currentSessionId, patch);
    try {
      await api.patchSessionOptions(currentSessionId, patch);
    } catch (err) {
      console.error("更新候选设置失败", err);
      rollbackPatch();
    } finally {
      setPending({});
    }
  };

  /** 点击候选：默认预填待确认；直发偏好开启时直接发送（定向随 @ 对象） */
  const pick = (opt: OptionChoice) => {
    const mentions = opt.mention_character_id ? [opt.mention_character_id] : [];
    if (shownDirectSend) {
      void useChatStore
        .getState()
        .sendMessage(opt.text, mentions.length ? mentions : undefined)
        .catch(() => undefined);
    } else {
      useComposerStore.getState().requestPrefill(opt.text, mentions);
    }
  };

  const nameOf = (cid: string) =>
    characters.find((c) => c.id === cid)?.card.name;
  const sourceLabel =
    source === "kickoff"
      ? kind === "scene"
        ? "新场景候选"
        : "开场候选"
      : source === "turn"
        ? "接话候选"
        : source === "draft"
          ? "代笔候选"
          : "";
  const anchorLabel = anchor
    ? anchor.actor === "player" ? "基于你的最新输入" : `基于 ${anchor.label} 的最新发言`
    : sourceLabel;

  return (
    <section className="v7-panel-section" data-testid="assist-candidates">
      <div className="v7-panel-section-head">
        <div>
          <h3>接话候选</h3>
          <p>
            {shownEnabled
              ? "在需要灵感时生成，不会自动打断对话。"
              : "当前已关闭，开启后可生成接话建议。"}
          </p>
        </div>
      </div>
      {options.length > 0 && (
        <div className="v7-candidate-list">
          <span className="v7-panel-list-label">{anchorLabel}</span>
          {anchor?.excerpt && (
            <p className="mb-2 line-clamp-3 rounded-lg bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-500" title={anchor.excerpt}>
              “{anchor.excerpt}”
            </p>
          )}
          {options.map((opt, i) => (
            <button
              key={i}
              type="button"
              onClick={() => pick(opt)}
              disabled={sending}
              title={
                shownDirectSend
                  ? "点击直接发送"
                  : "点击填入输入框，可修改后发送"
              }
              data-testid="assist-candidate"
            >
              {opt.mention_character_id && (
                <span>
                  @
                  {nameOf(opt.mention_character_id) ?? opt.mention_character_id}
                  　
                </span>
              )}
              {opt.text}
            </button>
          ))}
        </div>
      )}
      {notice && <p role="status" className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">{notice}</p>}
      <div className="v7-panel-actions">
        {shownEnabled ? (
          <button
            type="button"
            onClick={() => void generate()}
            disabled={generating || sending}
            title="每次点击生成一批新候选"
            data-testid="assist-generate"
            className="v7-panel-button-primary"
          >
            {generating
              ? "正在生成…"
              : options.length > 0
                ? "换一批"
                : "生成候选"}
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void patchOptions({ options_enabled: true })}
            className="v7-panel-button-primary"
          >
            开启候选
          </button>
        )}
        {options.length > 0 && (
          <button
            type="button"
            onClick={dismiss}
            title="关闭本批候选"
            data-testid="assist-dismiss"
            className="v7-panel-button-text"
          >
            清空本批
          </button>
        )}
      </div>
      <details className="v7-panel-disclosure">
        <summary>候选设置</summary>
        <div className="v7-panel-setting">
          <strong>启用候选</strong>
          <Switch
            on={shownEnabled}
            onToggle={() => patchOptions({ options_enabled: !shownEnabled })}
            label="启用候选"
          />
        </div>
        <div className="v7-panel-setting">
          <label htmlFor="assist-style">候选风格</label>
          <select
            id="assist-style"
            value={shownStyle}
            onChange={(e) => patchOptions({ options_style: e.target.value })}
          >
            {(Object.keys(STYLE_LABELS) as AssistStyle[]).map((s) => (
              <option key={s} value={s}>
                {STYLE_LABELS[s]}
              </option>
            ))}
          </select>
        </div>
        <div className="v7-panel-setting">
          <div>
            <strong>点击直发</strong>
            <small>关闭时先填入输入框确认</small>
          </div>
          <Switch
            on={shownDirectSend}
            onToggle={() =>
              patchOptions({ options_direct_send: !shownDirectSend })
            }
            label="点击直发"
          />
        </div>
      </details>
    </section>
  );
}
