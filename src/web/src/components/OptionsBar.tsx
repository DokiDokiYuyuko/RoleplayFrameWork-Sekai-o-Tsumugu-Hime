import { Button } from '../design-system/Button';
import { storyRuntime } from '../features/stories/storyRuntime';
import { useState } from 'react';
import { storiesClient as api } from '../features/stories/storiesClient';
import { useChatStore } from '../store/chatStore';
import { useActiveCharacters } from '../store/useActiveCharacters';
import type { OptionChoice } from '../types';
import { Switch } from './common';

/** R27.4 选项风格（后端 options_style 契约值） */
type OptionsStyle = 'action' | 'dialogue' | 'mixed';

const STYLE_LABELS: Record<OptionsStyle, string> = {
  action: '行动向',
  dialogue: '对话向',
  mixed: '混合',
};

/**
 * R27：消息列表与输入框之间的当前回合行动选项条。
 *
 * 设置读写（读回修复）：enabled/style 以 store 里的 session（后端持久化字段）为
 * 唯一真相——切换会话自然跟随，刷新后从 list_sessions 读回，不再用模块级变量。
 * 切换时乐观更新 store 里的 session + PATCH 后端，失败回滚。
 * - options 为空且启用中：零占位（chips 将随 options.ready 到达）
 * - options 为空且已禁用：仍渲染设置行（否则开关消失成死角）
 */
export function OptionsBar() {
  const options = useChatStore((s) => s.options);
  const sending = useChatStore((s) => s.sending);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const session = useChatStore((s) => s.sessions.find((x) => x.id === s.currentSessionId));
  const characters = useActiveCharacters();

  // 后端持久化值（undefined = 旧数据，按默认 开+mixed）
  const enabled = session?.options_enabled ?? true;
  const style = (session?.options_style as OptionsStyle | undefined) ?? 'mixed';
  // 本地乐观态（PATCH 进行中先显示目标值）
  const [pending, setPending] = useState<{ enabled?: boolean; style?: string }>({});
  const shownEnabled = pending.enabled ?? enabled;
  const shownStyle = (pending.style as OptionsStyle | undefined) ?? style;

  /** 乐观更新 store session + PATCH 后端；失败回滚（刷新 store 即还原） */
  const patchOptions = async (
    patch: { options_enabled?: boolean; options_style?: string },
  ) => {
    if (!currentSessionId) return;
    setPending({
      enabled: patch.options_enabled,
      style: patch.options_style,
    });
    const rollbackPatch = storyRuntime.optimisticSessionPatch(currentSessionId, patch);
    try {
      await api.patchSessionOptions(currentSessionId, patch);
    } catch (err) {
      console.error('更新选项生成设置失败', err);
      rollbackPatch();
    } finally {
      setPending({});
    }
  };

  /** 点击 chip：立即作为玩家消息发送，mention 走 mentions 通道 */
  const send = (opt: OptionChoice) => {
    void useChatStore.getState().sendMessage(
      opt.text,
      opt.mention_character_id ? [opt.mention_character_id] : undefined,
    ).catch(() => undefined);
  };

  const nameOf = (cid: string) => characters.find((c) => c.id === cid)?.card.name;

  const showChips = options.length > 0;
  if (!showChips && shownEnabled) return null;

  return (
    <div className="story-options">
      <div
        className={`story-options__choices ${sending ? 'pointer-events-none opacity-40' : ''}`}
      >
        {showChips &&
          options.map((opt, i) => (
            <Button variant="ghost"
              key={i}
              type="button"
              onClick={() => send(opt)}
              disabled={sending}
              title={
                opt.mention_character_id
                  ? `对 @${nameOf(opt.mention_character_id) ?? opt.mention_character_id} 发送此行动`
                  : '发送此行动'
              }
              className="story-options__choice"
            >
              {opt.mention_character_id && (
                <span className="mr-1 text-indigo-400">
                  @{nameOf(opt.mention_character_id) ?? opt.mention_character_id}
                </span>
              )}
              {opt.text}
            </Button>
          ))}

        {/* R27.4 紧凑设置：选项生成开关 + 风格（值来自会话持久化字段） */}
        <div className="story-options__settings">
          <span className="text-[10px] text-slate-400" title="回合选项生成开关">
            选项
          </span>
          <Switch on={shownEnabled} onToggle={() => patchOptions({ options_enabled: !shownEnabled })} />
          <select
            value={shownStyle}
            onChange={(e) => patchOptions({ options_style: e.target.value })}
            className="story-options__select"
            title="选项生成风格"
          >
            {(Object.keys(STYLE_LABELS) as OptionsStyle[]).map((s) => (
              <option key={s} value={s}>
                {STYLE_LABELS[s]}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
}
