import { storyRuntime } from '../features/stories/storyRuntime';
import { useState } from "react";
import { storiesClient as api } from '../features/stories/storiesClient';
import { useChatStore } from "../store/chatStore";
import { ResponseStylePicker } from "./ResponseStylePicker";
import { Switch } from "./common";

/**
 * 叙事区（M12-R41 右栏化）：视角 / 描写密度 / 短输入垫场。
 *
 * 性能约定（2026-09-25 修复）：点击**乐观高亮**（本地 pending），PATCH 返回后用
 * 响应值回填 store 里的会话——不再调用 loadSessions 整表刷新
 * （列表接口要逐文件 stat，cephfs 上单次 0.2-1.5s，点一下等两秒）。
 * 失败：清掉 pending 即回退到 store 旧值（不做乐观写入，无需回滚）。
 */
export function AssistNarrative() {
  const session = useChatStore((s) =>
    s.sessions.find((x) => x.id === s.currentSessionId),
  );
  const [pending, setPending] = useState<{
    pov?: string;
    density?: string;
    padding?: boolean;
    limit?: string;
  }>({});

  if (!session) return null;
  const padding = pending.padding ?? session.short_input_padding ?? true;
  const limit = pending.limit ?? (session.reply_max_tokens == null ? 'inherit' : String(session.reply_max_tokens));

  const patch = async (p: {
    narrative_pov?: string;
    narrative_density?: string;
    short_input_padding?: boolean;
    reply_max_tokens?: number | null;
  }) => {
    setPending((prev) => ({
      pov: p.narrative_pov ?? prev.pov,
      density: p.narrative_density ?? prev.density,
      padding: p.short_input_padding ?? prev.padding,
      limit: 'reply_max_tokens' in p ? (p.reply_max_tokens == null ? 'inherit' : String(p.reply_max_tokens)) : prev.limit,
    }));
    try {
      const ownsBranch = storyRuntime.capture(session.id);
      const fresh = await api.patchNarrative(session.id, p);
      if (ownsBranch()) storyRuntime.applySessionPatch(session.id, fresh);
    } catch (err) {
      console.error("更新叙事设置失败", err); // 清 pending → 回退旧值
    } finally {
      setPending({});
    }
  };

  return (
    <section className="v7-panel-section" data-testid="assist-narrative">
      <div className="v7-panel-section-head">
        <h3>回应与生成</h3>
      </div>
      <ResponseStylePicker />

      <div className="v7-panel-setting v7-narrative-padding">
        <div>
          <strong>单条回复上限</strong>
          <small>自动不设人为上限；已有角色卡的旧上限可在这里覆盖</small>
        </div>
        <select
          aria-label="单条回复上限"
          value={limit}
          onChange={(e) => void patch({ reply_max_tokens: e.target.value === 'inherit' ? null : Number(e.target.value) })}
          className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm"
        >
          <option value="inherit">沿用角色卡</option>
          <option value="0">自动</option>
          <option value="2048">2048 tokens</option>
          <option value="4096">4096 tokens</option>
          <option value="8192">8192 tokens</option>
        </select>
      </div>

      <div className="v7-panel-setting v7-narrative-padding">
        <div>
          <strong>短输入垫场</strong>
          <small>“嗯”等不超过 4 字时，补上环境氛围</small>
        </div>
        <Switch
          on={padding}
          onToggle={() => void patch({ short_input_padding: !padding })}
          label="短输入垫场"
        />
      </div>
    </section>
  );
}
