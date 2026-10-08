import { useEffect, useState, type ReactNode } from "react";
import { Button } from "../../design-system";
import { selectionCounts, toggleVisibleSelection } from "./assetList";
import "./library-tools.css";
export function useAssetSelection(allIds: string[]) {
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const key = allIds.join("\u0000");
  useEffect(() => {
    const valid = new Set(allIds);
    setSelected((current) =>
      current.every((id) => valid.has(id))
        ? current
        : current.filter((id) => valid.has(id)),
    );
  }, [key]);
  return {
    selecting,
    selected,
    setSelected,
    start: () => setSelecting(true),
    exit: () => {
      setSelecting(false);
      setSelected([]);
    },
    clear: () => setSelected([]),
    toggle: (id: string) => {
      setSelecting(true);
      setSelected((current) =>
        current.includes(id)
          ? current.filter((value) => value !== id)
          : [...current, id],
      );
    },
    toggleVisible: (ids: string[]) =>
      setSelected((current) => toggleVisibleSelection(current, ids)),
  };
}
export function AssetSelectionBar({
  selecting,
  selected,
  visible,
  onStart,
  onExit,
  onClear,
  onToggleVisible,
  busy,
  children,
}: {
  selecting: boolean;
  selected: string[];
  visible: string[];
  onStart: () => void;
  onExit: () => void;
  onClear: () => void;
  onToggleVisible: () => void;
  busy?: boolean;
  children?: ReactNode;
}) {
  const counts = selectionCounts(selected, visible);
  return (
    <div
      className="asset-selection-bar"
      data-selecting={selecting || undefined}
      aria-label="素材选择"
    >
      {!selecting ? (
        <Button type="button" onClick={onStart}>
          选择素材
        </Button>
      ) : (
        <>
          <span role="status">
            已选 {counts.total} 项
            {counts.hidden > 0 ? `（${counts.hidden} 项在当前结果之外）` : ""}
          </span>
          <Button
            type="button"
            disabled={busy || visible.length === 0}
            onClick={onToggleVisible}
          >
            {visible.length > 0 && visible.every((id) => selected.includes(id))
              ? "取消当前结果"
              : "选择当前结果"}
          </Button>
          <Button
            type="button"
            disabled={busy || selected.length === 0}
            onClick={onClear}
          >
            清空选择
          </Button>
          {children}
          <Button type="button" disabled={busy} onClick={onExit}>
            退出选择
          </Button>
        </>
      )}
    </div>
  );
}
