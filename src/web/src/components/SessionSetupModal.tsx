import { useEffect, useState } from 'react';
import { WorkflowDialog } from '../design-system/WorkflowDialog';
import { ApiError } from '../api/request';
import { storiesClient as api } from '../features/stories/storiesClient';
import { toView } from '../api/adapters';
import { useCharacterStore } from '../store/characterStore';
import { useChatStore } from '../store/chatStore';
import { useLorebookStore } from '../store/lorebookStore';
import type { Session } from '../types';
import type { CharacterView, Lorebook } from '../types';
import { Avatar, Switch } from './common';
import { playerPersonaFromCard } from '../utils/playerPersona';

/**
 * 会话设置弹层（对齐酒馆模式，新建 + 编辑两用）：
 * - 卡片式角色多选（头像+名字+描述，支持全选/清空）
 * - 世界书多选（书名+条目数+来源，创建后可随时改——配置跟随会话）
 * - Persona 多行输入
 * 编辑模式：PATCH title/persona/lorebook_ids（角色集合暂不改动——涉及运行中引擎管理）
 */
export interface SessionSetupResult {
  title: string;
  character_ids: string[];
  persona: string;
  lorebook_ids: string[];
}

export function SessionSetupModal({
  mode,
  session,
  onClose,
  returnFocus,
}: {
  mode: 'create' | 'edit';
  session?: Session | null;
  onClose: () => void;
  returnFocus?: HTMLElement | null;
}) {
  const libraryCharacters = useCharacterStore((s) => s.characters);
  const libraryBooks = useLorebookStore((s) => s.books);
  const loadSessions = useChatStore((s) => s.loadSessions);
  const selectSession = useChatStore((s) => s.selectSession);

  const defaultTitle =
    mode === 'edit' && session ? session.title : '';
  const [title, setTitle] = useState(defaultTitle);
  const [selectedChars, setSelectedChars] = useState<string[]>(
    mode === 'edit' && session ? session.character_ids : [],
  );
  const [selectedBooks, setSelectedBooks] = useState<string[]>(
    mode === 'edit' && session ? [] : [],
  );
  const [persona, setPersona] = useState(
    mode === 'edit' && session ? session.persona : '我，玩家。',
  );
  const [playerCharacterId, setPlayerCharacterId] = useState(
    mode === 'edit' && session ? (session.player_character_id ?? '') : '',
  );
  // R33.4 流式开关（编辑模式；默认开）
  const [streaming, setStreaming] = useState(
    mode === 'edit' && session ? (session.streaming_enabled ?? true) : true,
  );
  // R34.2 输出卫生开关（编辑模式；默认开）
  const [hygiene, setHygiene] = useState(
    mode === 'edit' && session ? (session.hygiene_enabled ?? true) : true,
  );
  // R35.3 导演模式（编辑模式；默认 auto）
  const [directorMode, setDirectorMode] = useState<'auto' | 'confirm' | 'rules'>(
    mode === 'edit' && session ? (session.director_mode ?? 'auto') : 'auto',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);

  const [loadStatus, setLoadStatus] = useState<'loading' | 'ready' | 'error'>(mode === 'create' ? 'ready' : 'loading');
  const [retry, setRetry] = useState(0);
  const [loadedRevision, setLoadedRevision] = useState<number | null>(null);
  const [editCharacters, setEditCharacters] = useState<CharacterView[]>([]);
  const [editBooks, setEditBooks] = useState<Lorebook[]>([]);
  const characters = mode === 'edit' ? editCharacters : libraryCharacters;
  const books = mode === 'edit' ? [...libraryBooks, ...editBooks.filter(book => !libraryBooks.some(globalBook => globalBook.id === book.id))] : libraryBooks;
  useEffect(() => {
    if (mode === 'create') { setLoadStatus('ready'); return; }
    if (!session?.id) { setLoadStatus('error'); setError('没有选择会话。'); return; }
    const controller = new AbortController();
    setLoadStatus('loading'); setLoadedRevision(null); setError(''); setConflict(false);
    void api.getSessionSetup(session.id, { signal: controller.signal }).then(setup => {
      if (controller.signal.aborted) return;
      setSelectedBooks(setup.meta.lorebook_ids);
      setSelectedChars(setup.meta.character_ids);
      setTitle(setup.meta.title);
      setPersona(setup.meta.player_persona ?? '');
      setPlayerCharacterId(setup.meta.player_character_id ?? '');
      setStreaming(setup.meta.streaming_enabled ?? true);
      setHygiene(setup.meta.hygiene_enabled ?? true);
      setDirectorMode(setup.meta.director_mode ?? 'auto');
      setEditCharacters(setup.characters.map(toView));
      setEditBooks(setup.lorebooks ?? []);
      setLoadedRevision(setup.meta.branch_revision); setLoadStatus('ready');
    }).catch(cause => {
      if (controller.signal.aborted) return;
      setLoadStatus('error'); setError(cause instanceof Error ? cause.message : '读取失败');
    });
    return () => controller.abort();
  }, [mode, session?.id, retry]);

  const toggleChar = (cid: string) => {
    setSelectedChars((prev) =>
      prev.includes(cid) ? prev.filter((x) => x !== cid) : [...prev, cid],
    );
  };
  const toggleBook = (bid: string) => {
    setSelectedBooks((prev) =>
      prev.includes(bid) ? prev.filter((x) => x !== bid) : [...prev, bid],
    );
  };

  const canSubmit =
    !busy && !conflict && loadStatus === 'ready' && (mode === 'edit' || selectedChars.length > 0) && title.trim().length > 0;

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setError('');
    try {
      if (mode === 'create') {
        const created = await api.createSession({ title: title.trim(), character_ids: selectedChars,
          persona, player_character_id: playerCharacterId || null, lorebook_ids: selectedBooks });
        await Promise.allSettled([loadSessions(), selectSession(created.id)]);
      } else if (session && loadedRevision !== null) {
        await api.patchSessionSetup(session.id, { expected_branch_revision: loadedRevision,
          title: title.trim(), lorebook_ids: selectedBooks, streaming_enabled: streaming,
          hygiene_enabled: hygiene, director_mode: directorMode });
        // A successful write must never be reissued because a following read failed.
        await Promise.allSettled([loadSessions(), useChatStore.getState().refreshSession(session.id)]);
      }
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setConflict(true);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const label = 'mb-1 block text-xs font-medium text-slate-500';

  return (
    <WorkflowDialog centered returnFocus={returnFocus} title={mode === "create" ? "新建会话" : "会话设置"} onClose={() => { if (!busy) onClose(); }}>
      <div className="max-h-[85vh] w-full max-w-2xl space-y-5 overflow-y-auto rounded-xl bg-white p-5 shadow-xl">
        <h2 className="text-lg font-semibold">
          {mode === 'create' ? '新建会话' : `会话设置 · ${session?.title ?? ''}`}
        </h2>

        {loadStatus === 'loading' && <p role="status">正在读取会话设置…</p>}
        {loadStatus === 'error' && <button type="button" onClick={() => setRetry(value => value + 1)}>重新读取设置</button>}
        {conflict && <div role="alert"><p>会话已发生变化。当前修改仍保留，请重新读取最新设置后检查并保存。</p><button type="button" onClick={() => setRetry(value => value + 1)}>重新读取最新设置</button></div>}
        <fieldset disabled={busy || loadStatus !== 'ready'} className="space-y-5">
        {/* 标题 */}
        <div>
          <label className={label}>会话标题</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="例：话剧社的第一晚"
            className="w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm focus:border-indigo-400 focus:outline-none"
          />
        </div>

        {/* 角色选择：卡片网格（编辑模式只读展示） */}
        <div>
          <div className="mb-1 flex items-center justify-between">
            <label className={label + ' mb-0'}>
              参与角色
              <span className="ml-1 text-slate-400">
                （{selectedChars.length}/{characters.length} 已选）
              </span>
            </label>
            {mode === 'create' && (
              <div className="flex gap-2 text-xs">
                <button
                  type="button"
                  onClick={() => setSelectedChars(characters.map((c) => c.id))}
                  className="text-indigo-500 hover:underline"
                >
                  全选
                </button>
                <button
                  type="button"
                  onClick={() => setSelectedChars([])}
                  className="text-slate-400 hover:underline"
                >
                  清空
                </button>
              </div>
            )}
          </div>
          {characters.length === 0 ? (
            <p className="rounded-lg border border-dashed border-slate-300 py-6 text-center text-sm text-slate-400">
              还没有角色——去「角色」页导入或「工坊」生成
            </p>
          ) : (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
              {characters.map((c) => {
                const selected = selectedChars.includes(c.id);
                return (
                  <button
                    key={c.id}
                    type="button"
                    disabled={mode === 'edit'}
                    onClick={() => toggleChar(c.id)}
                    className={`flex flex-col items-center gap-1.5 rounded-xl border p-3 transition-colors ${
                      selected
                        ? 'border-indigo-300 bg-indigo-50'
                        : 'border-slate-200 bg-white hover:border-slate-300'
                    } ${mode === 'edit' ? 'cursor-default opacity-70' : ''}`}
                  >
                    <Avatar name={c.card.name} color={c.color} characterId={c.id} imageVersion={c.updated_at ?? c.revision} />
                    <span
                      className={`w-full truncate text-center text-xs font-medium ${
                        selected ? 'text-indigo-600' : 'text-slate-600'
                      }`}
                    >
                      {c.card.name}
                    </span>
                    <span className="w-full truncate text-center text-[10px] text-slate-400">
                      {c.card.description || '（无描述）'}
                    </span>
                    {!c.present && (
                      <span className="rounded bg-amber-50 px-1 text-[10px] text-amber-600">
                        离席
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
          {mode === 'edit' && (
            <p className="mt-1 text-[10px] text-slate-400">
              编辑模式暂不改角色集合（运行中引擎管理）；如需换人请新建会话
            </p>
          )}
        </div>

        {/* 世界书选择 */}
        <div>
          <label className={label}>
            世界书
            <span className="ml-1 text-slate-400">
              （{selectedBooks.length} 本已绑定——角色卡内嵌书已自动绑定，这里可补充）
            </span>
          </label>
          {books.length === 0 ? (
            <p className="rounded-lg border border-dashed border-slate-300 py-4 text-center text-xs text-slate-400">
              还没有世界书——去「世界书」页导入或「工坊」生成
            </p>
          ) : (
            <div className="space-y-1.5">
              {books.map((b) => {
                const selected = selectedBooks.includes(b.id);
                return (
                  <button
                    key={b.id}
                    type="button"
                    onClick={() => toggleBook(b.id)}
                    className={`flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left transition-colors ${
                      selected
                        ? 'border-indigo-300 bg-indigo-50'
                        : 'border-slate-200 hover:border-slate-300'
                    }`}
                  >
                    <div className="min-w-0">
                      <div
                        className={`truncate text-sm font-medium ${
                          selected ? 'text-indigo-600' : 'text-slate-700'
                        }`}
                      >
                        {b.name}
                      </div>
                      <div className="truncate text-xs text-slate-400">
                        {b.entry_count ?? b.entries.length} 条 ·{' '}
                        {b.source_format === 'embedded'
                          ? '角色内嵌书'
                          : b.source_format === 'risu'
                            ? 'RisuAI'
                            : 'SillyTavern'}
                      </div>
                    </div>
                    <span
                      className={`ml-3 h-4 w-4 shrink-0 rounded border ${
                        selected
                          ? 'border-indigo-500 bg-indigo-500'
                          : 'border-slate-300 bg-white'
                      }`}
                    >
                      {selected && (
                        <svg viewBox="0 0 24 24" className="h-full w-full fill-none stroke-white stroke-3">
                          <path d="m5 13 4 4L19 7" />
                        </svg>
                      )}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Persona */}
        <div>
          <label className={label}>玩家角色卡（可选）</label>
          <select
            value={playerCharacterId}
            disabled={mode === 'edit'}
            onChange={(e) => {
              const id = e.target.value;
              setPlayerCharacterId(id);
              const card = libraryCharacters.find((item) => item.id === id)?.card;
              if (card) setPersona(playerPersonaFromCard(card));
            }}
            className="mb-2 w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm"
          >
            <option value="">手动填写身份</option>
            {libraryCharacters.filter((item) => !selectedChars.includes(item.id)).map((item) => (
              <option key={item.id} value={item.id}>{item.card.name}</option>
            ))}
          </select>
          <label className={label}>我的人设（Persona）——角色将如何认识和称呼你</label>
          <textarea
            value={persona}
            readOnly={mode === 'edit'}
            onChange={(e) => setPersona(e.target.value)}
            rows={5}
            placeholder="例：叶清让，大一新生，刚加入话剧社，性格温和有点社恐"
            className="w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm focus:border-indigo-400 focus:outline-none"
          />
          {mode === 'edit' && <p className="mt-2 text-xs text-slate-500">请在聊天输入区使用“切换角色”，历史身份会保留。</p>}
        </div>

        {/* R33.4 流式输出开关（编辑模式） */}
        {mode === 'edit' && (
          <>
            <div className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2.5">
              <div>
                <div className="text-sm font-medium text-slate-700">流式输出</div>
                <div className="text-xs text-slate-400">
                  回复逐字浮现（关闭则整段显示，下回合生效）
                </div>
              </div>
              <Switch on={streaming} onToggle={() => setStreaming((v) => !v)} />
            </div>
            <div className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2.5">
              <div>
                <div className="text-sm font-medium text-slate-700">输出卫生校验</div>
                <div className="text-xs text-slate-400">
                  此故事允许回复审查；还需在设置 → 高级选项开启全局开关，才会调用辅助模型
                </div>
              </div>
              <Switch on={hygiene} onToggle={() => setHygiene((v) => !v)} />
            </div>
            <div className="rounded-lg border border-slate-200 px-3 py-2.5">
              <div className="text-sm font-medium text-slate-700">导演模式</div>
              <div className="text-xs text-slate-400">
                LLM 导演只在无人被提及时介入（@/点名永远直通）
              </div>
              <div className="mt-2 flex gap-2">
                {([
                  ['auto', '自动', 'LLM 决策直接执行'],
                  ['confirm', '每次确认', 'LLM 决策挂起，等你确认'],
                  ['rules', '纯规则', '完全不调 LLM（v1 行为）'],
                ] as const).map(([value, label, title]) => (
                  <button
                    key={value}
                    type="button"
                    title={title}
                    onClick={() => setDirectorMode(value)}
                    className={`flex-1 rounded-lg border px-2 py-1.5 text-xs ${
                      directorMode === value
                        ? 'border-indigo-300 bg-indigo-50 text-indigo-600'
                        : 'border-slate-200 text-slate-600 hover:border-slate-300'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}

        </fieldset>
        {error && (
          <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600">{error}</p>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-lg border border-slate-300 px-4 py-1.5 text-sm"
          >
            取消
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="rounded-lg bg-indigo-500 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-600 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? '保存中…' : mode === 'create' ? '开始对话' : '保存设置'}
          </button>
        </div>
      </div>
    </WorkflowDialog>
  );
}
