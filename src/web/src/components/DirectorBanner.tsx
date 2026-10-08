import { useChatStore } from '../store/chatStore';
import { useActiveCharacters } from '../store/useActiveCharacters';

/**
 * 主列顶部：R35.3 确认档横幅（仅待确认时渲染）。
 *
 * M12-R41 右栏化后：导演决策/场景切换等信息态移入右侧「导演与场景」区，
 * 这里只保留**卡住回合的操作**——待确认的导演建议（执行 / 规则回退）。
 */
export function DirectorBanner() {
  const pending = useChatStore((s) => s.pendingDirector);
  const characters = useActiveCharacters();
  const confirmDirector = useChatStore((s) => s.confirmDirector);
  const rejectDirector = useChatStore((s) => s.rejectDirector);

  if (!pending) return null;

  const name = (id: string) =>
    id === 'player' ? '玩家' : characters.find((c) => c.id === id)?.card.name ?? id;
  const actionText =
    pending.action === 'switch_scene'
      ? `场景切换 → ${pending.scene?.title ?? '?'}`
      : pending.action === 'end_scene'
        ? '话题收束'
        : `${pending.chosen.map(name).join(' → ') || '（无人）'} 接话`;

  return (
    <div className="border-b border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800">
      <span className="mr-1 font-medium">导演建议（待确认）</span>
      {actionText}
      {pending.rationale && <span className="ml-2 text-amber-600">理由：{pending.rationale}</span>}
      <span className="ml-3 inline-flex gap-2">
        <button
          type="button"
          onClick={() => void confirmDirector()}
          className="rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-emerald-700 hover:bg-emerald-100"
        >
          ✓ 执行
        </button>
        <button
          type="button"
          onClick={() => void rejectDirector()}
          className="rounded border border-rose-300 bg-rose-50 px-2 py-0.5 text-rose-700 hover:bg-rose-100"
        >
          ✗ 规则回退
        </button>
      </span>
    </div>
  );
}
