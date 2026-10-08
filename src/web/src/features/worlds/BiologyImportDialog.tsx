import { useQuery } from '@tanstack/react-query';
import { resourceQueries } from '../resources/resourceQueries';
import { useEffect, useState } from "react";
import { Dialog } from "radix-ui";
import { worldsClient as api } from './worldsClient';
import type { World } from "../../types";
import { planBiologyImport, type BiologyImportRow, type BiologyImportStatus } from "./biologyImportPlan";

const STATUS_LABEL: Record<BiologyImportStatus, string> = {
  new: "",
  "same-title": "同名",
  imported: "已导入",
  updated: "源已更新",
};

export function BiologyImportDialog({ world, onImported }: {
  world: World;
  onImported: (next: World) => void;
}) {
  const [open, setOpen] = useState(false);
  const worldsQuery = useQuery({ ...resourceQueries.worlds(), enabled: open });
  const worlds = worldsQuery.data?.filter(item => item.id !== world.id) ?? null;
  const [sourceId, setSourceId] = useState("");
  const source = useQuery({ ...resourceQueries.world(sourceId), enabled: open && !!sourceId });
  const sourceWorld = source.data ?? null;
  const [rows, setRows] = useState<BiologyImportRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setSourceId(''); setError('');
  }, [open, world.id]);

  useEffect(() => {
    setRows(sourceWorld ? planBiologyImport(sourceWorld, world) : []);
  }, [sourceWorld, world]);

  useEffect(() => { if (source.error) setError(source.error instanceof Error ? source.error.message : '来源世界读取失败'); }, [source.error]);

  const selectable = rows.some((row) => row.status !== "imported");
  const chosen = rows.filter((row) => row.checked && row.status !== "imported");

  const toggle = (id: string) => {
    setRows((current) => current.map((row) =>
      row.id === id && row.status !== "imported" ? { ...row, checked: !row.checked } : row,
    ));
  };

  const submit = async () => {
    if (!sourceWorld || busy) return;
    if (!chosen.length) {
      setError("请选择要导入的生物设定");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const next = await api.importBiology(world.id, world.revision, sourceWorld.id, chosen.map((row) => ({
        id: row.id,
        mode: row.mode,
      })));
      onImported(next);
      setOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "导入失败");
    } finally {
      setBusy(false);
    }
  };

  return <>
    <button
      type="button"
      className="worlds-import-button"
      title={world.archived ? "归档世界不能修改" : "从其他世界导入"}
      aria-label="从其他世界导入"
      disabled={world.archived}
      onClick={() => setOpen(true)}
    >
      导入
    </button>
    <Dialog.Root open={open} onOpenChange={(next) => { if (!busy) setOpen(next); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="v7-dialog-overlay" />
        <Dialog.Content className="v7-dialog-content biology-import-dialog" aria-describedby="biology-import-description">
          <Dialog.Title>从其他世界导入</Dialog.Title>
          <Dialog.Description id="biology-import-description">
            把所选生物设定复制到当前世界。来源世界不会被改动，同名也不会覆盖已有资料。
          </Dialog.Description>
          <label>
            来源世界
            <select
              value={sourceId}
              aria-label="来源世界"
              disabled={!worlds || busy}
              onChange={(event) => setSourceId(event.target.value)}
            >
              <option value="">{worlds ? "选择世界…" : "正在读取世界…"}</option>
              {worlds?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}{item.archived ? "（已归档）" : ""} · 生物 {item.biology_count}
                </option>
              ))}
            </select>
          </label>
          {worlds && !worlds.length && <p>没有其他世界可以导入。</p>}
          {sourceId && !sourceWorld && !error && <p>正在读取生物设定…</p>}
          {sourceWorld && !rows.length && <p>这个世界没有生物设定。</p>}
          {!!rows.length && <div className="biology-import-list">
            {rows.map((row) => (
              <label className="biology-import-row" key={row.id}>
                <input
                  type="checkbox"
                  checked={row.checked}
                  disabled={row.status === "imported" || busy}
                  onChange={() => toggle(row.id)}
                />
                <span>
                  <strong>{row.title}</strong>
                  {STATUS_LABEL[row.status] && (
                    <em className={`biology-import-chip is-${row.status}`}>{STATUS_LABEL[row.status]}</em>
                  )}
                  <small>{row.classification} · {row.visibilityLabel}</small>
                </span>
              </label>
            ))}
          </div>}
          {sourceWorld && !!rows.length && (
            <p className="biology-import-hint">
              {selectable
                ? "公开资料默认勾选。同名默认不选，勾选会另存一份。源已更新时，勾选只覆盖正文和分类资料。"
                : "这些生物设定都已经导入，来源也没有更新。"}
            </p>
          )}
          {error && <div className="v7-error" role="alert">{error}</div>}
          <div className="worlds-modal-actions">
            <button type="button" className="v7-btn v7-btn-soft" disabled={busy} onClick={() => setOpen(false)}>取消</button>
            <button
              type="button"
              className="v7-btn v7-btn-primary"
              disabled={busy || !sourceWorld || !selectable}
              onClick={() => void submit()}
            >
              {busy ? "导入中…" : "导入所选"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  </>;
}
