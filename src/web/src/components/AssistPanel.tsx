import { PanelRightClose, PanelRightOpen, X } from "lucide-react";
import { useStoryUiStore } from '../features/stories/storyUiStore';
import { STORY_PANELS } from '../features/stories/storyPanels';

export function AssistPanel() {
  const toggle = useStoryUiStore((s) => s.toggleAssistPanel);
  const tab = useStoryUiStore((s) => s.assistTab);
  const setTab = useStoryUiStore((s) => s.setAssistTab);
  const wide = useStoryUiStore((s) => s.assistPanelWide);
  const toggleWidth = useStoryUiStore((s) => s.toggleAssistWidth);

  return (
    <aside
      className="v7-tool-deck"
      data-testid="assist-panel"
      aria-label="创作工具"
    >
      <header className="v7-tool-head">
        <div>
          <h2>创作工具</h2>
          <p>当前故事的写作与管理</p>
        </div>
        <div className="v7-tool-head-actions">
          <button
            type="button"
            onClick={toggleWidth}
            title={wide ? "收窄面板" : "加宽面板"}
            aria-label={wide ? "收窄面板" : "加宽面板"}
            data-testid="assist-width"
          >
            {wide ? (
              <PanelRightClose size={18} />
            ) : (
              <PanelRightOpen size={18} />
            )}
          </button>
          <button
            type="button"
            onClick={toggle}
            title="收起创作工具"
            aria-label="收起创作工具"
          >
            <X size={18} />
          </button>
        </div>
      </header>

      <nav className="v7-tool-tabs" aria-label="创作工具分类">
        {STORY_PANELS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            aria-current={tab === id ? "page" : undefined}
            data-testid={`assist-tab-${id}`}
            className={tab === id ? "is-active" : ""}
          >
            {label}
          </button>
        ))}
      </nav>

      <div className="v7-tool-content" key={tab}>
        {STORY_PANELS.find(panel => panel.id === tab)?.sections.map((Section, index) => <Section key={index} />)}
      </div>
    </aside>
  );
}
