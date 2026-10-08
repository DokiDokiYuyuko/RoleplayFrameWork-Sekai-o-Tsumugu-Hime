import { resourceQueries } from '../resources/resourceQueries';
import { SurfaceDialog } from '../../design-system/SurfaceDialog';
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";
import { ExternalLink as Link2, Plus } from '../../design-system/Icon';
import LorebookPage from "../../pages/LorebookPage";
import { worldsClient as api } from './worldsClient';
import { useLorebookStore } from "../../store/lorebookStore";
import type { World } from "../../types";

export function WorldBooks({
  world,
  onChange,
  onError,
}: {
  world: World;
  onChange: (next: World) => void;
  onError: (message: string) => void;
}) {
  const navigate = useNavigate();
  const { bookId } = useParams();
  const books = useLorebookStore((s) => s.books);
  const createBook = useLorebookStore((s) => s.createBook);
  const { data: owners = {} } = useQuery({
    ...resourceQueries.owners(),
  });
  const [showPicker, setShowPicker] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [pickId, setPickId] = useState("");
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState("");
  const link = async (id: string) => {
    setBusy(true);
    onError("");
    setLocalError("");
    try {
      const next = await api.linkWorldLorebook(world.id, id, world.revision);
      onChange(next);
      setShowPicker(false);
      navigate(`/worlds/${world.id}/lorebooks/${id}`);
    } catch (cause) {
      const error = cause instanceof Error ? cause.message : "关联失败";
      onError(error);
      setLocalError(error);
    } finally {
      setBusy(false);
    }
  };
  const unlink = async () => {
    if (!bookId || !world.lorebook_ids.includes(bookId)) return;
    if (!window.confirm("从此世界解除这本世界书？书和条目仍会留在素材库。"))
      return;
    setBusy(true);
    onError("");
    try {
      const next = await api.unlinkWorldLorebook(
        world.id,
        bookId,
        world.revision,
      );
      onChange(next);
      navigate(`/worlds/${world.id}/lorebooks`, { replace: true });
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : "解除失败");
    } finally {
      setBusy(false);
    }
  };
  const create = async () => {
    if (!newName.trim()) return;
    setBusy(true);
    onError("");
    setLocalError("");
    try {
      const book = await createBook({ name: newName.trim() });
      const next = await api.linkWorldLorebook(
        world.id,
        book.id,
        world.revision,
      );
      onChange(next);
      setShowCreate(false);
      setNewName("");
      navigate(`/worlds/${world.id}/lorebooks/${book.id}`);
    } catch (cause) {
      const error = cause instanceof Error ? cause.message : "新建失败";
      onError(error);
      setLocalError(error);
    } finally {
      setBusy(false);
    }
  };
  const ownedElsewhere = new Set(
    Object.entries(owners)
      .filter(([, owner]) => owner.world_id !== world.id)
      .map(([id]) => id),
  );
  const available = books.filter(
    (item) =>
      !world.lorebook_ids.includes(item.id) && !ownedElsewhere.has(item.id),
  );
  return (
    <div className="worlds-books worlds-books--workbench">
      <div className="worlds-books-intro workbench-bar">
        <div>
          <h2>这个世界的世界书</h2>
          <p>短词条按关键词或常驻规则进入对话。完整原稿请放在世界档案。</p>
        </div>
        <div className="worlds-books-actions">
          <Link className="v7-btn v7-btn-soft" to={`/worlds/${world.id}/organize?category=lorebook${bookId ? `&book=${encodeURIComponent(bookId)}` : ''}`}>AI 整理世界书</Link>
          <button
            className="v7-btn v7-btn-soft"
            onClick={() => {
              setLocalError("");
              setShowPicker(true);
            }}
          >
            <Link2 size={15} /> 关联现有书
          </button>
          {!world.lorebook_ids.length && <button
            className="v7-btn v7-btn-soft"
            onClick={() => {
              setLocalError("");
              setShowCreate(true);
            }}
          >
            <Plus size={15} /> 新建世界书
          </button>}
          {bookId && (
            <button
              className="v7-btn v7-btn-soft"
              disabled={busy}
              onClick={() => void unlink()}
            >
              解除关联
            </button>
          )}
          {!world.lorebook_ids.length && <Link className="v7-btn v7-btn-soft" to="/library/lorebooks">查看独立世界书库</Link>}
        </div>
      </div>
        <div className="worlds-books-editor">
          <LorebookPage
            scope={{
              worldId: world.id,
              bookIds: world.lorebook_ids,
              archiveRecords: world.archive_records,
              onCreated: link,
            }}
          />
        </div>
      {showCreate && (
        <SurfaceDialog title={"世界书"} className="worlds-modal-shade" onClose={() => setShowCreate(false)} busy={busy} nested={false}>
          <div
            className="worlds-modal"


            aria-label="新建世界书"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <h2>新建世界书</h2>
            <p>
              创建后会关联到「{world.title}」。接着可添加按关键词触发的短条目。
            </p>
            <label>
              世界书名称
              <input
                autoFocus
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="例如：示例世界书"
              />
            </label>
            {localError && (
              <div className="v7-error" role="alert">
                {localError}
              </div>
            )}
            <div className="worlds-modal-actions">
              <button
                className="v7-btn v7-btn-soft"
                onClick={() => setShowCreate(false)}
              >
                取消
              </button>
              <button
                className="v7-btn v7-btn-primary"
                disabled={busy || !newName.trim()}
                onClick={() => void create()}
              >
                {busy ? "创建中…" : "创建并关联"}
              </button>
            </div>
          </div>
        </SurfaceDialog>
      )}
      {showPicker && (
        <SurfaceDialog title={"关联世界书"} className="worlds-modal-shade" onClose={() => setShowPicker(false)} busy={busy} nested={false}>
          <div
            className="worlds-modal"


            aria-label="关联世界书"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <h2>关联现有世界书</h2>
            <p>只显示尚未归属其他世界的书；解除关联不会删书。</p>
            <select value={pickId} onChange={(e) => setPickId(e.target.value)}>
              <option value="">选择一本世界书</option>
              {available.map((item) => (
                <option value={item.id} key={item.id}>
                  {item.name} · {item.entries.length} 条
                </option>
              ))}
            </select>
            {available.length === 0 && (
              <p>暂无可关联的旧书。可以先在独立世界书库新建。</p>
            )}
            {localError && (
              <div className="v7-error" role="alert">
                {localError}
              </div>
            )}
            <div className="worlds-modal-actions">
              <button
                className="v7-btn v7-btn-soft"
                onClick={() => setShowPicker(false)}
              >
                取消
              </button>
              <button
                className="v7-btn v7-btn-primary"
                disabled={!pickId || busy}
                onClick={() => void link(pickId)}
              >
                {busy ? "关联中…" : "关联到世界"}
              </button>
            </div>
          </div>
        </SurfaceDialog>
      )}
    </div>
  );
}
