import { SceneImage, SceneImagePicker } from '../features/stories/SceneImagePicker';
import { useChatStore } from "../store/chatStore";
import { useActiveCharacters } from "../store/useActiveCharacters";
import type { DirectorTrigger } from "../types";

const TRIGGER_LABEL: Partial<Record<DirectorTrigger, string>> = {
  mention: "提及触发",
  explicit_mention: "@定向指定",
  talkativeness: "主动性抽选",
  manual_force: "玩家点名",
  open_round: "开场白",
  llm_route: "导演选角",
  llm_switch_scene: "导演切场景",
  llm_end_scene: "导演收束",
  player_scene: "玩家切场景",
};

/**
 * 导演与场景区（M12-R41 右栏化）：最近决策 + 当前场景卡 + 待确认高亮。
 *
 * 信息态从主列横幅搬入（主列只保留"待确认"横幅——那是卡回合的操作）；
 * 这里也带执行/回退按钮，两处等价。
 */
export function AssistDirector() {
  const decision = useChatStore((s) => s.lastDecision);
  const pending = useChatStore((s) => s.pendingDirector);
  const scene = useChatStore((s) => s.activeScene);
  const session = useChatStore(s => s.sessions.find(item => item.id === s.currentSessionId));
  const loaded = useChatStore(s => s.snapshotLoadedSessionId === s.currentSessionId);
  const groups = useChatStore((s) => s.sessionGroups);
  const characters = useActiveCharacters();
  const confirmDirector = useChatStore((s) => s.confirmDirector);
  const rejectDirector = useChatStore((s) => s.rejectDirector);
  const requestInspect = useChatStore((s) => s.requestInspect);

  const name = (id: string) =>
    id === "player"
      ? "玩家"
      : (characters.find((c) => c.id === id)?.card.name ??
        groups.find((group) => group.id === id)?.label ?? id);

  const pendingText = pending
    ? pending.action === "switch_scene"
      ? `场景切换 → ${pending.scene?.title ?? "?"}`
      : pending.action === "end_scene"
        ? "话题收束"
        : `${pending.chosen.map(name).join(" → ") || "（无人）"} 接话`
    : "";

  const members = scene ? scene.member_ids.map(name) : [];

  return (
    <section className="v7-panel-section" data-testid="assist-director">
      <div className="v7-panel-section-head">
        <h3>当前场景</h3>
      </div>
      {scene ? (
        <div className="v7-panel-scene">
          <SceneImage key={scene.id} scene={scene} />
          <strong>{scene.title}</strong>
          {scene.description && <p>{scene.description}</p>}
          {session && loaded && <SceneImagePicker key={`${session.id}:${scene.id}`} scene={scene} branchId={session.id} revision={session.branch_revision ?? 0} />}
          {members.length > 0 && (
            <p className="v7-panel-members">在场：{members.join("、")}</p>
          )}
        </div>
      ) : (
        <p className="v7-panel-muted">暂未设置场景</p>
      )}
      {pending && (
        <div className="v7-director-pending">
          <strong>导演建议待确认</strong>
          <p>{pendingText}</p>
          {pending.rationale && <p>理由：{pending.rationale}</p>}
          <div>
            <button type="button" onClick={() => void confirmDirector()}>
              执行建议
            </button>
            <button type="button" onClick={() => void rejectDirector()}>
              规则回退
            </button>
          </div>
        </div>
      )}
      {decision ? (
        <details className="v7-panel-disclosure v7-director-log">
          <summary>导演记录 · 第 {decision.turn} 回合</summary>
          <div>
            <strong>
              {decision.chosen.length > 0
                ? decision.chosen.map(name).join(" → ")
                : "无人接话"}
            </strong>
            <p>
              {TRIGGER_LABEL[decision.trigger] ?? decision.trigger}
              {decision.rationale ? ` · ${decision.rationale}` : ""}
            </p>
            {decision.chosen.length > 0 && (
              <button
                type="button"
                onClick={() =>
                  requestInspect(decision.chosen[0], decision.turn)
                }
                data-testid="inspect-this-turn"
              >
                查看本轮上下文
              </button>
            )}
          </div>
        </details>
      ) : (
        <p className="v7-panel-muted">暂无导演记录</p>
      )}
    </section>
  );
}
