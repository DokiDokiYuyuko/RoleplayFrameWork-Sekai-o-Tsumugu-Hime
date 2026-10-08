import { ArrowLeft, Save, MoreHorizontal, Eye, ArrowUp, ArrowDown, Pin, Trash2, Plus, Sparkles, CircleHelp, X } from "../design-system/Icon";
import { Popover, Collapsible } from 'radix-ui';
import { WorkbenchDesk } from '../design-system/Workbench';
import { Tabs, TabsList, TabsTrigger } from '../design-system/Tabs';
import { Menu } from '../design-system/Menu';
import { Tag } from '../design-system/Tag';
import { IconButton } from '../design-system/IconButton';
import { Ornament } from '../appearance/Ornament';
import { revealCharacterChapter } from "../features/library/characterEditorNavigation";
import { queryClient } from '../queryClient';
import { resourceQueries } from '../features/resources/resourceQueries';
import { useQuery } from '@tanstack/react-query';
import { SurfaceDialog } from '../design-system/SurfaceDialog';
// R31 完整角色编辑器：受控组件，左主（表单分组）右辅（Persona 预览 + 试聊）。
// 运行时模型配置（model/base_url/max_tokens）不在 CharacterCard 契约上，
// 编辑器单独维护，保存时随 payload.runtime 传出，由调用方决定落到哪（PATCH 等）。

import { Children, isValidElement, useEffect, useRef, useState } from "react";
import type { SelectHTMLAttributes, ChangeEvent } from "react";
import { useNavigate } from "react-router";
import type { ReactNode } from "react";
import type { CharacterCard } from "../types";
import { libraryClient as api } from '../features/library/libraryClient';
import { useCharacterStore } from "../store/characterStore";
import { exportCharacterUrl } from "../api/Api";
import { Avatar } from "../design-system/Avatar";
import { Select } from "../design-system/Select";
import { Checkbox } from "../design-system/Checkbox";
import { moonweaveAsset } from "../appearance/moonweaveAssets";
import { ConfirmDialog } from "../design-system/ConfirmDialog";
import { characterCreation } from "../api/characterCreation";
import { appendVoiceExample, applyAiCandidate, characterDraftKey, formatVoiceExample, personaSignature, trialHistoryError, TRIAL_ROUNDS } from "../utils/characterCreation.js";
import type { AiCandidate, TrialMessage } from "../utils/characterCreation.js";
import { createClientId } from "../utils/clientId.js";
import "./character-creation.css";
import "./character-editor.css";
import "./character-editor-chapters.css";
import { Button } from "../design-system/Button";
import { TextInput } from "../design-system/TextInput";
import { Textarea } from "../design-system/Textarea";
import { CharacterCropDialog } from "./CharacterCropDialog";

/** 每角色独立运行时配置（不写入角色卡，单独随 payload 传出） */
export interface CharacterRuntimeConfig {
  model: string;
  base_url: string;
  max_tokens: number;
}

export interface CharacterEditorPayload {
  card: CharacterCard;
  aliases: string[];
  runtime?: CharacterRuntimeConfig;
  /** 另存副本时为 true：父级应对新卡走创建而非更新 */
  asCopy?: boolean;
  source_world_id?: string | null;
}

export interface CharacterEditorProps {
  presentation?: "dialog" | "page";
  initialRuntime?: CharacterRuntimeConfig;
  initialCard: CharacterCard;
  initialAliases: string[];
  /** 存在 = 编辑已有角色（显示头像上传 / 导出 / 另存副本）；缺省 = 新建角色 */
  characterId?: string;
  onSave: (payload: CharacterEditorPayload) => Promise<void>;
  onSaveSucceeded?: (asCopy: boolean, hasLaterEdits?: boolean) => void;
  onClose: () => void;
  busy?: boolean;
  /** 新建模式（无 characterId）下的头像选择：本地预览并上报文件（父层在创建成功后上传） */
  onAvatarPicked?: (file: File | null) => void;
  onFullBodyPicked?: (file: File | null) => void;
  /** 智能导入审阅时保存未确认草稿；普通编辑器不显示该动作。 */
  onSaveDraft?: (payload: CharacterEditorPayload) => Promise<void>;
  onRegenerate?: (card: CharacterCard) => Promise<void>;
  reviewEvidence?: string;
  reviewTarget?: ReactNode;
  hideRuntime?: boolean;
  saveLabel?: string;
  initialSourceWorldId?: string | null;
  initialAuthoringSource?: { text: string; captured_at: string; import_job_id: string | null } | null;
  initialAdvancedOpen?: boolean;
  hideSourceWorld?: boolean;
  draftScope?: string;
  onMediaRevision?: (revision: number) => void;
  getCharacterRevision?: () => number | undefined;
}

const AI_FIELDS = [
  "description",
  "appearance",
  "traits",
  "personality",
  "scenario",
  "first_mes",
  "mes_example",
] as const;
type AiField = (typeof AI_FIELDS)[number];

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

const labelCls = "character-field-label";
const btnGhost = "character-action";
const btnPrimary = "character-action-primary";

export default function CharacterEditor({
  presentation = "dialog",
  initialRuntime,
  initialCard,
  initialAliases,
  characterId,
  onSave,
  onSaveSucceeded,
  onClose,
  busy = false,
  onAvatarPicked,
  onFullBodyPicked,
  onSaveDraft,
  onRegenerate,
  reviewEvidence,
  reviewTarget,
  hideRuntime = false,
  saveLabel,
  initialSourceWorldId = null,
  initialAuthoringSource = null,
  initialAdvancedOpen = false,
  hideSourceWorld = false,
  draftScope,
  onMediaRevision,
  getCharacterRevision,
}: CharacterEditorProps) {
  const navigate = useNavigate();
  const [card, setCard] = useState<CharacterCard>({ ...initialCard });
  const [aliases, setAliases] = useState<string[]>(initialAliases);
  const [sourceWorldId, setSourceWorldId] = useState<string | null>(initialSourceWorldId);
  const [authoringSource, setAuthoringSource] = useState(initialAuthoringSource);
  const { data: worlds = [] } = useQuery(resourceQueries.worlds());
  const [worldError, setWorldError] = useState("");
  // All shared hosts now show one chapter at a time; the former advanced fold
  // no longer determines whether any card field is available.
  void initialAdvancedOpen;
  const [draftIdentity, setDraftIdentity] = useState<string>(() => createClientId());
  const [organizeOpen, setOrganizeOpen] = useState(false);
  const [organizeFields, setOrganizeFields] = useState<string[]>(['description', 'appearance', 'traits', 'personality']);
  const [referenceWorld, setReferenceWorld] = useState(true);
  const [organizeBusy, setOrganizeBusy] = useState(false);
  const [organizeError, setOrganizeError] = useState('');
  const [organizeJobId, setOrganizeJobId] = useState<string | null>(null);
  const organizePrepared = useRef<{ key: string; jobId: string } | null>(null);
  const organizeMounted = useRef(true);
  useEffect(() => { organizeMounted.current = true; return () => { organizeMounted.current = false; }; }, []);
  useEffect(() => { let alive = true; queryClient.fetchQuery(resourceQueries.worlds()).catch(error => { if (alive) setWorldError(errMsg(error)); }); return () => { alive = false; }; }, []);
  // 另存副本保留当前编辑身份，原角色的未保存修改仍由草稿恢复。
  const [cid] = useState(characterId);
  const [runtime, setRuntime] = useState<CharacterRuntimeConfig>(initialRuntime ?? {
    model: "",
    base_url: "",
    max_tokens: 0,
  });
  const [runtimeLoaded, setRuntimeLoaded] = useState(!characterId || Boolean(initialRuntime));
  const [runtimeReadAttempt, setRuntimeReadAttempt] = useState(0);
  const [saveError, setSaveError] = useState("");
  const [saveNotice, setSaveNotice] = useState("");
  const editorRoot = useRef<HTMLDivElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const [contentWidth, setContentWidth] = useState(() => typeof window === 'undefined' ? 1440 : window.innerWidth || 1440);
  const [sideTab, setSideTab] = useState('overview');
  const [previewTab, setPreviewTab] = useState('preview');
  const [saving, setSaving] = useState(false);
  const saveInFlight = useRef(false);
  const [draftSaving, setDraftSaving] = useState(false);
  const [nameInvalid, setNameInvalid] = useState(false);
  const [activeChapter, setActiveChapter] = useState('character-manuscript');
  const [focusMode, setFocusMode] = useState(false);
  const showSide = presentation === 'page' && contentWidth >= 1280 && !focusMode;
  const previewOpen = showSide || activeChapter === 'character-preview';
  const operationBusy = busy || saving || draftSaving;
  const chapterRevealRequested = useRef(false);
  const chapterVisible = (...ids: string[]) => ids.includes(activeChapter);
  const chapters = [
    ['character-manuscript', '人物原稿', '01'], ['character-appearance-ability', '外貌与立绘', '02'],
    ['character-traits', card.traits_label?.trim() || '能力与实力', '03'], ['character-personality-relationships', '性格与关系', '04'],
    ['character-opening-voice', '开场与口吻', '05'], ['character-profile', '身份资料', '06'], ['character-prompts', '高级提示', '07'],
    ...(!hideRuntime ? [['character-runtime', '运行配置', '08']] : []), ...(!showSide ? [['character-preview', '预览与试聊', '09']] : []),
  ];
  const chapterIndex = chapters.findIndex(([id]) => id === activeChapter);
  const selectChapter = (target: string, reveal = true) => {
    if (target === activeChapter) { if (reveal) revealCharacterChapter(editorRoot.current?.querySelector<HTMLElement>(`#${target}`) ?? null); return; }
    chapterRevealRequested.current = reveal;
    setActiveChapter(target);
  };
  useEffect(() => {
    if (chapterRevealRequested.current) {
      revealCharacterChapter(editorRoot.current?.querySelector<HTMLElement>(`#${activeChapter}`) ?? null);
      chapterRevealRequested.current = false;
    }
  }, [presentation, activeChapter]);
  useEffect(() => {
    const owner = editorRoot.current; if (!owner || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => setContentWidth(entry.contentRect.width));
    observer.observe(owner); return () => observer.disconnect();
  }, []);
  useEffect(() => { if (showSide && activeChapter === 'character-preview') setActiveChapter('character-manuscript'); }, [showSide, activeChapter]);
  const [pendingExit, setPendingExit] = useState<string | null>(null);
  const [confirmExit, setConfirmExit] = useState(false);
  const baseline = useRef({
    card: initialCard,
    aliases: initialAliases,
    sourceWorldId: initialSourceWorldId,
    runtime: initialRuntime ?? (characterId
      ? (null as CharacterRuntimeConfig | null)
      : { model: "", base_url: "", max_tokens: 0 }),
  });
  // -- 头像（编辑=立即上传；新建=本地预览，创建成功后由父层上传） --
  const [avatarV, setAvatarV] = useState(0);
  const [fullBodyUploading, setFullBodyUploading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [avatarCrop, setAvatarCrop] = useState<File | string | null>(null);
  const [localFullBody, setLocalFullBody] = useState<string | null>(null);
  useEffect(() => () => { if (localFullBody) URL.revokeObjectURL(localFullBody); }, [localFullBody]);
  const [localAvatar, setLocalAvatar] = useState<string | null>(null); // 新建模式预览 URL

  useEffect(() => () => { if (localAvatar) URL.revokeObjectURL(localAvatar); }, [localAvatar]);

  const mediaBaseline = useRef({ avatar: null as string | null, fullBody: null as string | null });
  const dirty = (!cid && (localAvatar !== mediaBaseline.current.avatar || localFullBody !== mediaBaseline.current.fullBody)) ||
    JSON.stringify({
      card,
      aliases,
      sourceWorldId,
      runtime: runtimeLoaded ? runtime : null,
    }) !==
    JSON.stringify({
      card: baseline.current.card,
      aliases: baseline.current.aliases,
      sourceWorldId: baseline.current.sourceWorldId,
      runtime: runtimeLoaded ? baseline.current.runtime : null,
    });

  const draftKey = characterDraftKey(characterId || draftScope, draftIdentity);
  type EditorDraft = { card: CharacterCard; aliases: string[]; runtime: CharacterRuntimeConfig; sourceWorldId?: string | null; base: string; updated: string; key: string };
  const [recoverableDrafts, setRecoverableDrafts] = useState<EditorDraft[]>(() => {
    try { return Object.keys(localStorage).filter(key => characterId || draftScope ? key === draftKey : key.startsWith('mrp.character-editor.new:')).map(key => ({ ...JSON.parse(localStorage.getItem(key) || 'null'), key })).filter(value => value?.card && Array.isArray(value.aliases)).sort((a,b) => String(b.updated).localeCompare(String(a.updated))); } catch { return []; }
  });
  const [savedDraft, setSavedDraft] = useState<EditorDraft | null>(recoverableDrafts[0] ?? null);
  const draftSnapshot = useRef({ key: draftKey, value: { card, aliases, runtime, sourceWorldId, base: JSON.stringify(initialCard), updated: new Date().toISOString() }, dirty, runtimeLoaded });
  draftSnapshot.current = { key: draftKey, value: { card, aliases, runtime, sourceWorldId, base: JSON.stringify(initialCard), updated: new Date().toISOString() }, dirty, runtimeLoaded };
  const editableSnapshot = JSON.stringify({ card, aliases, runtime, sourceWorldId, localAvatar, localFullBody });
  const liveEditableSnapshot = useRef(editableSnapshot); liveEditableSnapshot.current = editableSnapshot;
  const flushDraft = () => { const snapshot = draftSnapshot.current; if (snapshot.dirty && snapshot.runtimeLoaded) { try { localStorage.setItem(snapshot.key, JSON.stringify(snapshot.value)); } catch { /* Optional storage. */ } } };
  useEffect(() => () => { const snapshot = draftSnapshot.current; if (snapshot.dirty && snapshot.runtimeLoaded) { try { localStorage.setItem(snapshot.key, JSON.stringify(snapshot.value)); } catch { /* Optional storage. */ } } }, []);
  useEffect(() => {
    if (!dirty || !runtimeLoaded) return;
    const timer = window.setTimeout(() => {
      try { localStorage.setItem(draftKey, JSON.stringify({ card, aliases, runtime, sourceWorldId, base: JSON.stringify(initialCard), updated: new Date().toISOString() })); } catch { /* Optional browser storage. */ }
    }, 350);
    return () => window.clearTimeout(timer);
  }, [card, aliases, runtime, sourceWorldId, dirty, runtimeLoaded, draftKey, initialCard]);
  useEffect(() => {
    if (!dirty) return;
    const protect = (event: BeforeUnloadEvent) => { flushDraft(); event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', protect);
    return () => window.removeEventListener('beforeunload', protect);
  }, [dirty]);
  const clearLocalDraft = (key = draftKey) => { if (key === draftKey) draftSnapshot.current.dirty = false; try { localStorage.removeItem(key); } catch { /* Optional storage. */ } setRecoverableDrafts(rows => rows.filter(row => row.key !== key)); setSavedDraft(current => current?.key === key ? null : current); };



  // -- Persona 预览（debounce 800ms） --
  const [preview, setPreview] = useState<{
    persona: string;
    tokens: number;
  } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  const previewSeq = useRef(0);

  // -- AI 改写 --
  const [aiField, setAiField] = useState<AiField | null>(null);
  const [aiInstruction, setAiInstruction] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiCandidate, setAiCandidate] = useState<AiCandidate | null>(null);
  const [aiError, setAiError] = useState("");
  const selections = useRef<Partial<Record<AiField, {start: number; end: number}>>>({});
  const [aiSelection, setAiSelection] = useState<{start: number; end: number} | undefined>();
  const [aiSelectionValue, setAiSelectionValue] = useState('');
  const [aiScope, setAiScope] = useState<'field' | 'selection'>('field');
  const [aiUndo, setAiUndo] = useState<{field: AiField; before: string; after: string} | null>(null);
  const aiRequest = useRef(0);
  const aiAbort = useRef<AbortController | null>(null);

  // -- 试聊（不落任何 store） --
  const [chat, setChat] = useState<TrialMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const chatBox = useRef<HTMLDivElement>(null);
  const [chatError, setChatError] = useState("");
  const [chatSignature, setChatSignature] = useState<string | null>(null);
  const [voiceCandidate, setVoiceCandidate] = useState<string | null>(null);
  const [voiceNotice, setVoiceNotice] = useState("");
  const chatRequest = useRef(0);
  const chatAbort = useRef<AbortController | null>(null);
  const signature = personaSignature(card);
  const chatStale = chatSignature !== null && chatSignature !== signature;
  const liveSignature = useRef(signature); liveSignature.current = signature;
  useEffect(() => () => { ++aiRequest.current; ++chatRequest.current; aiAbort.current?.abort(); chatAbort.current?.abort(); }, []);
  useEffect(() => { if (chatSignature !== null && chatSignature !== signature && chatLoading) { ++chatRequest.current; chatAbort.current?.abort(); setChatLoading(false); setChatError('人设已改变，已停止接收旧版本回复。'); } }, [signature, chatSignature, chatLoading]);
  const resetChat = () => { ++chatRequest.current; chatAbort.current?.abort(); setChat([]); setChatSignature(null); setChatLoading(false); setChatError(''); setVoiceCandidate(null); };

  const setField = <K extends keyof CharacterCard>(k: K, v: CharacterCard[K]) =>
    setCard((c) => ({ ...c, [k]: v }));

  const organizeCharacter = async () => {
    if (!card.description.trim() || organizeBusy) return;
    setOrganizeBusy(true); setOrganizeError('');
    try {
      const revision = cid ? getCharacterRevision?.() : null;
      if (cid && !revision) throw new Error('无法读取角色修订号，请重新打开角色后整理。');
      // A persisted job freezes this exact version before any generation. It
      // carries the selected fields and target CAS into the shared review flow.
      flushDraft();
      const input = { source: card.description, world_id: sourceWorldId,
        selected_fields: organizeFields, target_asset_id: cid ?? null, target_revision: revision ?? null,
        character_seed: { ...card }, character_aliases: aliases, character_runtime: runtime,
        reference_world_id: referenceWorld ? sourceWorldId : null };
      const key = JSON.stringify(input);
      const jobId = organizePrepared.current?.key === key ? organizePrepared.current.jobId : (await characterCreation.organize(input)).id;
      organizePrepared.current = { key, jobId };
      if (!organizeMounted.current) return;
      setOrganizeJobId(jobId);
      await characterCreation.generateOrganized(jobId);
      if (organizeMounted.current) navigate(`/library/imports/${jobId}`);
    } catch (cause) { if (organizeMounted.current) setOrganizeError(errMsg(cause)); }
    finally { if (organizeMounted.current) setOrganizeBusy(false); }
  };

  // 编辑已有角色：运行时配置从后端回填
  useEffect(() => {
    if (!characterId || initialRuntime) return;
    let alive = true;
    api
      .getCharacter(characterId)
      .then((c) => {
        if (!alive) return;
        const loaded = {
          model: c.llm.inherit_model === true || (c.llm.inherit_model == null && c.llm.model === "deepseek/deepseek-v4-flash") ? "" : c.llm.model ?? "",
          base_url: c.llm.inherit_base_url === true || (c.llm.inherit_base_url == null && c.llm.base_url === "https://openrouter.ai/api/v1") ? "" : c.llm.base_url ?? "",
          max_tokens: Number(c.llm.sampling?.max_tokens ?? 0),
        };
        baseline.current.runtime = loaded;
        setRuntime(loaded);
        if (c.authoring_source) setAuthoringSource(c.authoring_source);
        setRuntimeLoaded(true); setSaveError("");
      })
      .catch(cause => { if (alive) setSaveError(`运行配置读取失败：${errMsg(cause)}。重新读取后才能保存。`); });
    return () => {
      alive = false;
    };
  }, [characterId, initialRuntime, runtimeReadAttempt]);

  // 预览按需展开；其内容与表单使用同一草稿。
  useEffect(() => {
    if (!previewOpen) return;
    const seq = ++previewSeq.current;
    const timer = setTimeout(async () => {
      setPreviewLoading(true);
      try {
        const r = await api.personaPreview(card);
        if (seq === previewSeq.current) setPreview(r);
      } catch {
        if (seq === previewSeq.current) setPreview(null);
      } finally {
        if (seq === previewSeq.current) setPreviewLoading(false);
      }
    }, 800);
    return () => { clearTimeout(timer); if (previewSeq.current === seq) ++previewSeq.current; };
  }, [card, previewOpen]);

  // 试聊自动滚到底
  useEffect(() => {
    const el = chatBox.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat, chatLoading]);

  // -- AI 改写动作 --
  const openAi = (field: AiField) => {
    ++aiRequest.current; aiAbort.current?.abort(); setAiBusy(false);
    setAiCandidate(null);
    setAiError("");
    setAiInstruction("");
    setAiField(field);
    const range = selections.current[field];
    const selected = range && range.end > range.start ? range : undefined;
    setAiSelection(selected); setAiScope(selected ? 'selection' : 'field');
    setAiSelectionValue(selected ? String(card[field] ?? '').slice(selected.start, selected.end) : '');
  };
  const clearAi = () => {
    ++aiRequest.current; aiAbort.current?.abort(); setAiBusy(false);
    setAiField(null);
    setAiCandidate(null);
    setAiError("");
    setAiInstruction("");
    setAiSelection(undefined);
  };
  const runAi = async () => {
    if (!aiField || !aiInstruction.trim() || aiBusy) return;
    const original = String(card[aiField] ?? '');
    const selection = aiScope === 'selection' ? aiSelection : undefined;
    if (selection && original.slice(selection.start, selection.end) !== aiSelectionValue) { setAiError('选中文字已经改变，请重新选择后改写。'); return; }
    setAiBusy(true);
    setAiError("");
    const requestId = ++aiRequest.current;
    const controller = new AbortController(); aiAbort.current = controller;
    const baseSignature = personaSignature(card);
    try {
      const { value } = await characterCreation.edit(card, aiField, aiInstruction.trim(), selection ? original.slice(selection.start, selection.end) : undefined, controller.signal);
      if (requestId !== aiRequest.current) return;
      setAiCandidate({
        field: aiField,
        original,
        value,
        signature: baseSignature,
        selection,
      });
    } catch (e) {
      if (requestId === aiRequest.current) setAiError(errMsg(e));
    } finally {
      if (requestId === aiRequest.current) setAiBusy(false);
    }
  };
  const adoptAi = () => {
    if (!aiCandidate) return;
    try { const next = applyAiCandidate(card, aiCandidate); setAiUndo({ field: aiCandidate.field as AiField, before: aiCandidate.original, after: String(next[aiCandidate.field as AiField]) }); setCard(next); clearAi(); }
    catch (error) { setAiError(errMsg(error)); }
  };

  // -- 备用开场白操作 --
  const setGreeting = (i: number, v: string) =>
    setCard((c) => ({
      ...c,
      alternate_greetings: c.alternate_greetings.map((g, j) =>
        j === i ? v : g,
      ),
    }));
  const addGreeting = () =>
    setCard((c) => ({
      ...c,
      alternate_greetings: [...c.alternate_greetings, ""],
    }));
  const removeGreeting = (i: number) =>
    setCard((c) => ({
      ...c,
      alternate_greetings: c.alternate_greetings.filter((_, j) => j !== i),
    }));
  const moveGreeting = (i: number, dir: -1 | 1) =>
    setCard((c) => {
      const j = i + dir;
      if (j < 0 || j >= c.alternate_greetings.length) return c;
      const gs = [...c.alternate_greetings];
      [gs[i], gs[j]] = [gs[j], gs[i]];
      return { ...c, alternate_greetings: gs };
    });
  const promoteGreeting = (i: number) =>
    // 与 first_mes 互换
    setCard((c) => {
      const gs = [...c.alternate_greetings];
      const g = gs[i];
      gs[i] = c.first_mes;
      return { ...c, first_mes: g, alternate_greetings: gs };
    });

  // -- 头像上传 --
  const onAvatarFile = async (file: File) => {
    if (!cid) { setLocalAvatar(URL.createObjectURL(file)); onAvatarPicked?.(file); return; }
    setUploading(true);
    setUploadError("");
    try {
      await useCharacterStore.getState().uploadMedia(cid, "avatar", file);
      const revision = useCharacterStore.getState().characters.find(item => item.id === cid)?.revision;
      if (revision) onMediaRevision?.(revision);
      setAvatarV((v) => v + 1);
    } catch (e) {
      setUploadError(errMsg(e));
      throw e;
    } finally {
      setUploading(false);
    }
  };

  // -- 试聊 --
  const sendChat = async () => {
    const text = chatInput.trim();
    if (!text || chatLoading || chatStale) return;
    const error = trialHistoryError(chat);
    if (error || text.length > 5000) { setChatError(error || '单条消息最多 5000 字符。'); return; }
    const requestId = ++chatRequest.current;
    const baseSignature = signature;
    const controller = new AbortController(); chatAbort.current = controller;
    setChatSignature(baseSignature); setChatError('');
    setChatLoading(true);
    try {
      const { reply } = await characterCreation.previewTurn(card, text, chat, controller.signal);
      if (requestId !== chatRequest.current || liveSignature.current !== baseSignature) return;
      if (!reply.trim()) throw new Error('模型返回空回复，请重试。');
      setChat(c => [...c, { role: 'user', content: text }, { role: 'assistant', content: reply }]);
      setChatInput('');
    } catch (e) {
      if (requestId === chatRequest.current) setChatError(errMsg(e));
    } finally {
      if (requestId === chatRequest.current) setChatLoading(false);
    }
  };

  // -- 保存 --
  const doSave = async (asCopy: boolean) => {
    if (operationBusy || saveInFlight.current || !runtimeLoaded) return;
    if (!card.name.trim()) { setNameInvalid(true); setSaveError("请先填写角色名字"); nameInput.current?.focus(); return; }
    const submitted = { card: structuredClone(card), aliases: [...aliases], runtime: { ...runtime }, sourceWorldId };
    const submittedMedia = { avatar: localAvatar, fullBody: localFullBody };
    const submittedSignature = editableSnapshot;
    saveInFlight.current = true; setSaving(true); setNameInvalid(false);
    setSaveError("");
    setSaveNotice("");
    try {
      await onSave({ card: submitted.card, aliases: submitted.aliases, runtime: submitted.runtime, source_world_id: submitted.sourceWorldId, asCopy: asCopy || undefined });
      const hasLaterEdits = submittedSignature !== liveEditableSnapshot.current;
      if (!asCopy) {
        baseline.current = submitted; mediaBaseline.current = submittedMedia;
        if (hasLaterEdits) flushDraft(); else clearLocalDraft();
      }
      setSaveNotice(asCopy ? "副本已保存。原角色仍可继续编辑。" : hasLaterEdits ? "已保存提交时的内容，后续修改仍未保存。" : "已保存，继续编织这个角色。");
      onSaveSucceeded?.(asCopy, hasLaterEdits);
      return true;
    } catch (e) {
      setSaveError(errMsg(e));
      return false;
    } finally { saveInFlight.current = false; setSaving(false); }
  };
  const saveCopy = async () => {
    // 副本独立创建；原编辑器仍可继续保存当前角色。
    await doSave(true);
  };

  const saveEditorDraft = async () => {
    if (!onSaveDraft || operationBusy || saveInFlight.current) return;
    const submitted = { card: structuredClone(card), aliases: [...aliases], runtime: { ...runtime }, sourceWorldId };
    const submittedSignature = editableSnapshot;
    setDraftSaving(true); saveInFlight.current = true; setSaveError('');
    try {
      await onSaveDraft({ card: submitted.card, aliases: submitted.aliases, runtime: submitted.runtime, source_world_id: submitted.sourceWorldId });
      baseline.current = submitted;
      setSaveNotice(submittedSignature === liveEditableSnapshot.current ? '草稿已保存，可以稍后继续审阅。' : '提交时的草稿已保存，后续修改仍未保存。');
    } finally { saveInFlight.current = false; setDraftSaving(false); }
  };

  // -- AI 改写交互块（渲染在对应字段下方） --
  const renderAi = (field: AiField) => {
    if (aiCandidate && aiCandidate.field === field) {
      return (
        <div className="character-ai-band">
          <div className="character-ai-compare">
            <div className="character-ai-column">
              <div className="character-ai-caption">
                原值
              </div>
              <div className="character-ai-original" tabIndex={0} role="region" aria-label="原值">
                {aiCandidate.original || "（空）"}
              </div>
            </div>
            <div className="character-ai-column">
              <div className="character-ai-caption">
                AI 改写
              </div>
              <Textarea aria-label="编辑 AI 候选" className="character-ai-candidate" value={aiCandidate.value} onChange={event => setAiCandidate(candidate => candidate ? {...candidate, value: event.target.value} : null)} />
            </div>
          </div>
          {aiCandidate.signature !== signature && <p role="status" className="character-ai-stale">人设已改变，候选已过期；可以复制候选，或放弃后重新生成。</p>}
          {aiCandidate.selection && <p className="character-ai-info">只替换选中文字，未选部分保持不变。</p>}
          {aiError && <p role="alert" className="character-ai-error">{aiError}</p>}
          <div className="character-ai-actions">
            <Button type="button" className={btnGhost} onClick={clearAi}>
              放弃
            </Button>
            <Button
              type="button"
              variant="tonal"
              onClick={adoptAi}
              disabled={aiCandidate.signature !== signature}
            >
              采用
            </Button>
          </div>
        </div>
      );
    }
    if (aiField === field) {
      return (
        <div className="character-ai-band">
          <label className="character-ai-scope">改写范围 <EditorSelect aria-label="AI 改写范围" value={aiScope} disabled={aiBusy} onChange={event => setAiScope(event.target.value as 'field' | 'selection')}><option value="field">整个字段</option>{aiSelection && <option value="selection">选中文字</option>}</EditorSelect></label>
          <div className="character-ai-command">
            <TextInput
              className="character-ai-instruction"
              value={aiInstruction}
              autoFocus
              placeholder="改写指令，如：更简洁、突出毒舌属性"
              aria-label="AI 改写指令"
              onChange={(e) => setAiInstruction(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  runAi();
                }
              }}
            />
            <Button
              type="button"
              variant="tonal"
              disabled={aiBusy || !aiInstruction.trim()}
              onClick={runAi}
            >
              {aiBusy ? "改写中…" : "确认"}
            </Button>
            <Button type="button" className={btnGhost} onClick={clearAi}>
              取消
            </Button>
          </div>
          {aiError && (
            <p role="alert" className="character-ai-error">改写失败：{aiError}</p>
          )}
        </div>
      );
    }
    return null;
  };

  // 带AI 改写按钮的 label 行
  const aiLabel = (field: AiField, text: string) => (
    <div className="character-writing-field__head">
      <label htmlFor={`character-${field}`} className="character-field-label">{text}<code>{field}</code></label>
      <Button
        type="button"
        variant="ghost" size="sm" className="character-ai-trigger" icon={Sparkles}
        onClick={() => (aiField === field ? clearAi() : openAi(field))}
      >
        AI 改写
      </Button>
    </div>
  );

  const requestClose = () => { if (dirty) setConfirmExit(true); else onClose(); };
  // BrowserRouter links are protected at the editor boundary; unmount always flushes the draft.
  useEffect(() => {
    if (presentation !== "page" || !dirty) return;
    const protectLink = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = (event.target as Element | null)?.closest<HTMLAnchorElement>('a[href]');
      if (!link || link.target === '_blank' || link.hasAttribute('download')) return;
      const target = new URL(link.href, window.location.href);
      if (target.origin !== window.location.origin || (target.pathname === window.location.pathname && target.search === window.location.search)) return;
      event.preventDefault(); event.stopPropagation(); flushDraft();
      setPendingExit(target.pathname + target.search + target.hash); setConfirmExit(true);
    };
    document.addEventListener('click', protectLink, true);
    return () => document.removeEventListener('click', protectLink, true);
  }, [presentation, dirty]);
  const filledChapters: Record<string, boolean> = {
    'character-manuscript': Boolean(card.description.trim()),
    'character-appearance-ability': Boolean(card.appearance?.trim() || cid || localFullBody),
    'character-traits': Boolean(card.traits?.trim()),
    'character-personality-relationships': Boolean(card.personality.trim() || card.scenario.trim()),
    'character-opening-voice': Boolean(card.first_mes.trim() || card.mes_example.trim() || card.alternate_greetings.some(value => value.trim())),
    'character-profile': Boolean(sourceWorldId || aliases.length || card.tags.length || card.creator || card.character_version || card.creator_notes),
    'character-prompts': Boolean(card.system_prompt?.trim() || card.post_history_instructions?.trim()),
    'character-runtime': Boolean(runtime.model.trim() || runtime.base_url.trim() || runtime.max_tokens),
  };
  const progressSections = chapters.filter(([id]) => id !== 'character-preview');
  const progress = progressSections.filter(([id]) => filledChapters[id]).length;
  const stateKey = operationBusy ? 'loading' : saveError ? 'error' : dirty ? 'dirty' : cid || saveNotice ? 'success' : 'new';
  const avatarSource = cid ? `/api/v1/characters/${encodeURIComponent(cid)}/avatar?v=${avatarV}` : localAvatar || undefined;
  const chapterTitle = chapters[chapterIndex]?.[1] || '人物原稿';
  const chapterNumber = chapters[chapterIndex]?.[2] || '01';
  const countText = activeChapter === 'character-manuscript' ? `${card.description.length} 字`
    : activeChapter === 'character-appearance-ability' ? `外貌 ${card.appearance?.length || 0} 字`
    : activeChapter === 'character-traits' ? `${card.traits?.length || 0} 字`
    : activeChapter === 'character-personality-relationships' ? `性格 ${card.personality.length} 字 · 背景与关系 ${card.scenario.length} 字`
    : activeChapter === 'character-opening-voice' ? `开场白 ${card.first_mes.length} 字 · 备用开场白 ${card.alternate_greetings.length} 条`
    : activeChapter === 'character-profile' ? `别名 ${aliases.length} · 标签 ${card.tags.length}`
    : activeChapter === 'character-prompts' ? '留空的提示使用全局默认'
    : activeChapter === 'character-runtime' ? '运行配置不写入角色卡' : '试聊不会创建故事，也不会写入记忆';
  const fold = (title: string, children: ReactNode) => <Collapsible.Root className="character-fold"><Collapsible.Trigger asChild><Button variant="ghost" size="sm">{title}</Button></Collapsible.Trigger><Collapsible.Content className="character-fold__body">{children}</Collapsible.Content></Collapsible.Root>;
  const longField = (field: AiField, title: string, placeholder = '') => <div className="character-writing-field">
    {aiLabel(field, title)}{renderAi(field)}
    <Textarea id={`character-${field}`} className={`character-paper${field === 'mes_example' ? ' character-paper--mono' : ''}`} aria-label={title}
      data-testid={`card-${field.replace(/_/g, '-')}`} value={String(card[field] ?? '')} placeholder={placeholder}
      onSelect={event => { selections.current[field] = { start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd }; }}
      onChange={event => setField(field, event.target.value)} />
  </div>;
  const renderPreview = () => <div className="character-preview-panel character-live-panel">
    <pre tabIndex={0} aria-label="人设预览文本" className="character-persona">{preview?.persona ?? (previewLoading ? '正在读取人设预览…' : '预览未能加载；修改字段后会重新读取。')}</pre>
    <div className="workbench-column__foot"><span>{previewLoading ? '刷新中…' : `tokens ≈ ${preview?.tokens ?? '—'} · 已是最新`}</span></div>
  </div>;
  const renderTrial = () => <div className="character-trial-panel character-live-panel">
    <div className="character-trial-bar"><span>短多轮试聊 · {chat.length / 2}/{TRIAL_ROUNDS} 轮</span>
      {(chat.length > 0 || chatLoading || chatError) && <Button size="sm" variant="ghost" onClick={resetChat}>按当前人设重新试聊</Button>}</div>
    {chatStale && <p role="status" className="creation-notice character-trial-notice">这段试聊基于修改前的人设。重新试聊会按当前人设开始；原回复仍可核对。</p>}
    <div ref={chatBox} className="character-trial-log" aria-live="polite">
      {!chat.length && !chatLoading && <p className="character-trial-empty">最多六轮，仅检查口吻；不会创建故事或写入记忆。</p>}
      {chat.map((message, index) => <div key={index} className={`character-trial-message${message.role === 'user' ? ' character-trial-message--user' : ''}`}>
        {message.role === 'assistant' && <span>{card.name || '角色'}</span>}<div>{message.content}</div>
        {message.role === 'assistant' && <Button size="sm" variant="ghost" onClick={() => { setVoiceCandidate(formatVoiceExample(chat[index - 1]?.content || '', message.content)); setVoiceNotice(''); }}>保存为口吻示例</Button>}
      </div>)}
      {chatLoading && <p>角色思考中…</p>}
      {chatError && <p role="alert" className="creation-error">试聊失败：{chatError}。输入已保留，可重试。</p>}
      {chat.length >= TRIAL_ROUNDS * 2 && <p className="creation-intro">已完成六轮，重新试聊可开启下一次检查。</p>}
    </div>
    {voiceCandidate !== null && <div className="character-voice-confirm"><p>{chatStale ? '来自修改前的人设，请核对后再采用。' : '核对示例后追加，原有示例会保留。'}</p>
      <Textarea autoFocus className="creation-candidate" aria-label="口吻示例预览" value={voiceCandidate} onChange={event => setVoiceCandidate(event.target.value)} />
      <div className="creation-actions"><Button variant="ghost" onClick={() => setVoiceCandidate(null)}>取消</Button><Button variant="tonal" disabled={!voiceCandidate.trim()} onClick={() => { const next = appendVoiceExample(card.mes_example, voiceCandidate); setField('mes_example', next); setVoiceNotice(next === card.mes_example ? '这个示例已存在，没有重复追加。' : '示例已追加到编辑草稿，保存角色后生效。'); setVoiceCandidate(null); }}>采用并追加示例</Button></div>
    </div>}
    {voiceNotice && <p role="status" className="character-voice-notice">{voiceNotice}</p>}
    <div className="workbench-column__foot character-trial-send">
      <TextInput aria-label="试聊消息" value={chatInput} disabled={chatLoading || chatStale || chat.length >= TRIAL_ROUNDS * 2} maxLength={5000} placeholder="输入消息…" onChange={event => setChatInput(event.target.value)}
        onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); void sendChat(); } }} />
      <Button variant="tonal" size="sm" disabled={chatLoading || chatStale || chat.length >= TRIAL_ROUNDS * 2 || !chatInput.trim()} onClick={() => void sendChat()}>发送</Button>
    </div>
  </div>;
  const renderOverview = () => <div className="character-live-panel"><div className="workbench-column__body character-overview">
    <div className="character-overview-media"><button type="button" className="character-overview-portrait" aria-label="全身立绘，跳到外貌与立绘" onClick={() => selectChapter('character-appearance-ability')}>
      <span>立绘 · 未填写</span>{(cid || localFullBody) && <img src={localFullBody || `/api/v1/characters/${encodeURIComponent(cid || '')}/full-body?v=${avatarV}`} alt="全身立绘缩略" onError={event => { event.currentTarget.hidden = true; }} />}</button>
      <button type="button" className="character-overview-avatar" aria-label="头像，跳到外貌与立绘" onClick={() => selectChapter('character-appearance-ability')}><Avatar name={card.name || '?'} size={56} src={avatarSource} /></button>
    </div>
    <dl className="character-overview-kv">
      {!hideSourceWorld && <div><dt>来源世界</dt><dd><button onClick={() => selectChapter('character-profile')}>{worlds.find(world => world.id === sourceWorldId)?.title || (sourceWorldId ? '来源世界暂不可用' : '未填写')}</button></dd></div>}
      <div><dt>标签</dt><dd><button onClick={() => selectChapter('character-profile')}>{card.tags.length ? card.tags.map(tag => <Tag key={tag}>{tag}</Tag>) : '未填写'}</button></dd></div>
      <div><dt>别名</dt><dd><button onClick={() => selectChapter('character-profile')}>{aliases.join('、') || '未填写'}</button></dd></div>
      <div><dt>人设 token</dt><dd>≈ {preview?.tokens ?? '—'} <small>估算</small></dd></div>
    </dl><section><h3>开场白</h3><button className="character-opening-summary" onClick={() => selectChapter('character-opening-voice')}>{card.first_mes.trim() || '未填写'}</button></section>
  </div><div className="workbench-column__foot"><span>只读速览 · 点击条目跳到对应章节</span></div></div>;
  const avatarPanel = <section className="character-editor-avatar"><h3>头像</h3><div className="character-avatar-row"><Avatar name={card.name || '?'} size={72} src={avatarSource} />
    <div className="character-avatar-actions">{(cid || onAvatarPicked) && <label className="character-avatar-upload ui-btn ui-btn--tonal" role="button" tabIndex={operationBusy || uploading || fullBodyUploading ? -1 : 0} aria-disabled={operationBusy || uploading || fullBodyUploading} aria-busy={uploading}
      onKeyDown={event => { if ((event.key === 'Enter' || event.key === ' ') && !operationBusy && !uploading && !fullBodyUploading) { event.preventDefault(); event.currentTarget.querySelector<HTMLInputElement>('input[type="file"]')?.click(); } }}>
      {uploading ? '上传中…' : cid ? '上传头像' : '选择头像'}<input type="file" aria-label="选择角色头像文件" disabled={operationBusy || uploading || fullBodyUploading} accept="image/png,image/jpeg,image/webp" hidden onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) setAvatarCrop(file); }} /></label>}
      {(cid || localAvatar) && <Button size="sm" disabled={operationBusy || uploading || fullBodyUploading} onClick={() => setAvatarCrop(localAvatar || `/api/v1/characters/${encodeURIComponent(cid || '')}/avatar?v=${avatarV}`)}>重新裁剪</Button>}
      {!cid && localAvatar && <Button variant="danger" disabled={operationBusy} onClick={() => { setLocalAvatar(null); onAvatarPicked?.(null); }}>移除头像</Button>}
    </div></div>{uploadError && <p role="alert" className="creation-error">{uploadError}</p>}<p className="creation-intro">头像框随外观设置，在此只预览效果。</p></section>;
  const chapterTabs = <Tabs value={activeChapter} onValueChange={target => selectChapter(target, false)}><TabsList aria-label="角色手稿章节" className="character-chapter-tabs">{chapters.map(([id, title, number]) => <TabsTrigger key={id} value={id} aria-controls={id}><span>{number}</span>{title}{id !== 'character-preview' && <i className={`character-status-dot${filledChapters[id] ? ' is-filled' : ''}`} aria-hidden="true" />}</TabsTrigger>)}</TabsList></Tabs>;
  const content = <>
    <div ref={editorRoot} className={`character-editor-document${presentation === 'page' ? ' workbench-page' : ' character-editor-modal-document'}`} data-region="character" data-side-visible={showSide} data-chapter-tabs={presentation === 'page' && contentWidth < 900}>
      <header className="character-editor-header workbench-bar">
        {presentation === 'page' && <Button variant="ghost" className="character-editor-back" disabled={operationBusy || uploading || fullBodyUploading} onClick={requestClose} aria-label="返回角色库" icon={ArrowLeft}>角色库</Button>}
        <div className="character-editor-title"><Avatar name={card.name || '?'} size={32} src={avatarSource} />
          <label className="character-title-name"><span className="sr-only">角色名字</span><input ref={nameInput} id="character-name" aria-required="true" aria-invalid={nameInvalid || undefined} data-testid="card-name" value={card.name} placeholder="未命名角色" autoComplete="off" className="ui-input__el character-title-input" style={{ width: `${Math.min(36, Math.max(7, Array.from(card.name || '未命名角色').length + 1))}ch` }} onChange={event => { setNameInvalid(false); setField('name', event.target.value); }} /></label>
          <div className="character-editor-save-state" role="status" data-save-state={stateKey}>
            {saveError ? <Popover.Root><Popover.Trigger asChild><Button variant="ghost" size="sm" aria-label="保存失败，查看原因并重试"><Tag tone="danger">保存失败</Tag></Button></Popover.Trigger><Popover.Portal><Popover.Content className="character-save-error-pop" sideOffset={8} collisionPadding={16}><strong>保存失败</strong><p role="alert">{saveError}</p><div className="creation-actions">{!runtimeLoaded ? <Button variant="tonal" onClick={() => setRuntimeReadAttempt(value => value + 1)}>重新读取运行配置</Button> : <Button variant="tonal" disabled={operationBusy} onClick={() => void doSave(false)}>重试保存</Button>}<Popover.Close asChild><Button variant="ghost">关闭</Button></Popover.Close></div></Popover.Content></Popover.Portal></Popover.Root>
              : <Tag dot tone={operationBusy ? 'accent' : dirty ? 'warning' : cid || saveNotice ? 'success' : 'neutral'}>{operationBusy ? '保存中…' : dirty ? (saveNotice || '有未保存的修改') : cid || saveNotice ? (saveNotice || '已保存') : '新角色尚未保存'}</Tag>}
          </div>
        </div>
        <div className="character-editor-commit">
          {presentation === 'page' && contentWidth >= 1280 && <Button className="character-focus-toggle" variant="ghost" icon={Eye} aria-pressed={focusMode} onClick={() => setFocusMode(value => !value)}>{focusMode ? '显示右栏' : '专注'}</Button>}
          {onSaveDraft && <Button variant="ghost" disabled={operationBusy || uploading || fullBodyUploading} onClick={() => void saveEditorDraft().catch(cause => setSaveError(errMsg(cause)))}>保存草稿</Button>}
          {onRegenerate && <Button variant="ghost" disabled={operationBusy || uploading || fullBodyUploading} onClick={() => void onRegenerate(structuredClone(card)).catch(cause => setSaveError(errMsg(cause)))}>重新生成</Button>}
          {cid && <Menu trigger={<IconButton label="更多角色操作" icon={MoreHorizontal} />} items={[
            { id: 'copy', label: '另存副本', disabled: operationBusy || uploading || fullBodyUploading, onSelect: () => { void saveCopy(); } },
            { id: 'json', label: '导出 JSON', onSelect: () => { window.open(exportCharacterUrl(cid, 'json'), '_blank'); } },
            { id: 'png', label: '导出 PNG', onSelect: () => { window.open(exportCharacterUrl(cid, 'png'), '_blank'); } },
          ]} />}
          {presentation === 'dialog' && <Button variant="ghost" disabled={operationBusy || uploading || fullBodyUploading} onClick={requestClose}>取消</Button>}
          <Button variant="primary" skin className={btnPrimary} disabled={!runtimeLoaded || operationBusy || uploading || fullBodyUploading} aria-busy={operationBusy} onClick={() => doSave(false)} icon={Save}>{operationBusy ? '保存中…' : saveLabel ?? (cid ? '保存' : '创建角色')}</Button>
        </div>
      </header>
      {savedDraft && <div role="status" className="creation-notice character-draft-notice"><span>发现未保存草稿。{savedDraft.base !== JSON.stringify(initialCard) ? '资料已更新，恢复后请核对再保存。' : '可以接着编辑。'}本地选图需要重新选择。</span>{recoverableDrafts.length > 1 && <EditorSelect aria-label="选择恢复的角色草稿" value={savedDraft.key} onChange={event => setSavedDraft(recoverableDrafts.find(row => row.key === event.target.value) ?? null)}>{recoverableDrafts.map(row => <option key={row.key} value={row.key}>{row.card.name || '未命名'} · {row.updated}</option>)}</EditorSelect>}<div className="creation-actions"><Button variant="tonal" disabled={!runtimeLoaded || operationBusy} onClick={() => { setCard(savedDraft.card); setAliases(savedDraft.aliases); setRuntime(savedDraft.runtime); setSourceWorldId(savedDraft.sourceWorldId ?? initialSourceWorldId); if (!characterId && !draftScope) setDraftIdentity(savedDraft.key.slice('mrp.character-editor.new:'.length)); setSavedDraft(null); }}>恢复草稿</Button><Button variant="ghost" onClick={() => clearLocalDraft(savedDraft.key)}>丢弃草稿</Button></div></div>}
      {(reviewTarget || reviewEvidence) && <div className="character-host-band">{reviewTarget}{reviewEvidence && fold('原稿对照', <pre className="creation-source">{reviewEvidence}</pre>)}</div>}
      {presentation === 'dialog' && <div className="character-modal-tabs">{chapterTabs}</div>}
      <WorkbenchDesk layout={presentation === 'dialog' || contentWidth < 900 ? 'main' : showSide ? 'rail-main-side' : 'rail-main'} ornament={presentation === 'page'} className="character-editor-body" data-focus-mode={focusMode}>
        {presentation === 'page' && <nav className="character-editor-directory workbench-column workbench-column--rail" aria-label="角色手稿章节">
          <div className="character-directory-identity workbench-column__head"><Avatar name={card.name || '?'} size={40} src={avatarSource} /><div><span>{progress} / {progressSections.length} 已填写</span><progress aria-label="填写进度" value={progress} max={progressSections.length} /></div></div>
          <div className="character-directory-links workbench-column__body">{chapters.map(([id, title, number], index) => <div key={id}>{[0, 4, 6].includes(index) && <p className="character-directory-group">{index === 0 ? '设定' : index === 4 ? '表现' : '运行'}</p>}<a href={`#${id}`} aria-current={activeChapter === id ? 'location' : undefined} onClick={event => { event.preventDefault(); selectChapter(id); }}><span>{number}</span><span>{title}</span>{id !== 'character-preview' && <i className={`character-status-dot${filledChapters[id] ? ' is-filled' : ''}`} aria-hidden="true" />}</a></div>)}</div>
          <div className="character-directory-footer workbench-column__foot"><span>● 已填写</span><span>○ 未填写</span></div>
          <div className="character-directory-tabs">{chapterTabs}</div>
        </nav>}
        <div className="character-editor-columns workbench-column workbench-column--main" data-active-chapter={activeChapter}>
          <header className="character-panel-heading workbench-column__head"><span>{chapterNumber}</span><h2>{chapterTitle}</h2>
            {activeChapter === 'character-manuscript' && <div className="character-section-tools">
              {!onSaveDraft && <Button size="sm" variant="secondary" disabled={!card.description.trim() || operationBusy || organizeBusy} aria-expanded={organizeOpen} onClick={() => { setOrganizeOpen(value => !value); setOrganizeError(''); }}>整理为字段</Button>}
              <Button size="sm" variant="secondary" icon={Sparkles} aria-expanded={aiField === 'description'} onClick={() => aiField === 'description' ? clearAi() : openAi('description')}>AI 改写</Button>
              <Popover.Root><Popover.Trigger asChild><IconButton label="编辑帮助" icon={CircleHelp} size="sm" /></Popover.Trigger><Popover.Portal><Popover.Content className="character-save-error-pop" sideOffset={8} collisionPadding={16}><p>先写人物设定即可保存；外貌、能力与开场可按需精修，所有卡片字段都会保留。</p><p>整理会冻结当前正文，当前角色在确认采用前不会改变。选中文字后改写可只替换选中部分。</p></Popover.Content></Popover.Portal></Popover.Root>
            </div>}
          </header>
          {aiUndo && <div className="workbench-column__band character-ai-undo"><span>已采用改写</span><Button variant="ghost" size="sm" disabled={String(card[aiUndo.field]) !== aiUndo.after} onClick={() => { setField(aiUndo.field, aiUndo.before); setAiUndo(null); }}>撤销此次采用</Button><IconButton label="关闭提示" icon={X} size="sm" onClick={() => setAiUndo(null)} /></div>}
          {activeChapter === 'character-manuscript' && organizeOpen && <div className="workbench-column__band creation-organize-panel"><fieldset className="creation-organize-fields" disabled={organizeBusy}><legend>本次整理的字段</legend>{([['description', '整理后的简介'], ['appearance', '外貌'], ['traits', '能力与实力'], ['personality', '性格'], ['scenario', '背景与关系'], ['first_mes', '开场'], ['mes_example', '口吻示例']] as const).map(([field, label]) => <Checkbox key={field} label={label} checked={organizeFields.includes(field)} disabled={field === 'description'} onChange={() => setOrganizeFields(rows => rows.includes(field) ? rows.filter(value => value !== field) : [...rows, field])} />)}</fieldset>
            {sourceWorldId && <div className="creation-reference"><Checkbox label="使用来源世界的作者资料作为创作参考" disabled={organizeBusy} checked={referenceWorld} onChange={event => setReferenceWorld(event.target.checked)} /><small>包含该世界的私密作者资料；只用于本次整理。</small></div>}
            <p className="creation-intro">简介保留未迁移的设定；拆出的内容移入所选字段，未选字段保留。会冻结当前正文，并打开整理任务核对；原稿单独保留供作者查阅。</p><p className="creation-intro">{cid ? '其他未保存编辑仍留在本地草稿。' : '人物字段、别名与独立模型配置会保留。本地选图需在采用后重新选择。'}</p>
            <div className="creation-actions"><Button variant="ghost" disabled={organizeBusy} onClick={() => setOrganizeOpen(false)}>取消整理</Button><Button variant="tonal" disabled={!card.description.trim() || operationBusy || organizeBusy} onClick={() => void organizeCharacter()}>{organizeBusy ? '建立整理任务…' : '生成整理候选'}</Button></div>{organizeError && <p className="creation-error" role="alert">整理未完成：{organizeError}。正文草稿仍保留。{organizeJobId && <Button variant="ghost" onClick={() => navigate(`/library/imports/${organizeJobId}`)}>打开已保存的整理任务</Button>}</p>}
          </div>}
          <div className="character-editor-main">
            <section id="character-manuscript" className="character-editor-chapter character-long-chapter" hidden={!chapterVisible('character-manuscript')} tabIndex={-1}>{renderAi('description')}<Textarea id="character-description" className="character-paper" aria-label="人物原稿" value={card.description} onSelect={event => { selections.current.description = { start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd }; }} onChange={event => setField('description', event.target.value)} placeholder="写下或粘贴人物设定，无需先拆分字段。" data-testid="card-description" /></section>
            <section id="character-appearance-ability" className="character-editor-chapter character-media-chapter" hidden={!chapterVisible('character-appearance-ability')} tabIndex={-1}>
              {longField('appearance', '外貌', '体态、五官、发色、衣着、辨识特征与形态变化')}
              <div className="character-media-column">{(cid || onFullBodyPicked) && <CharacterArtPanel characterId={cid} characterName={card.name} appearance={card.appearance} onPicked={file => { setLocalFullBody(file ? URL.createObjectURL(file) : null); onFullBodyPicked?.(file); }} onUploading={setFullBodyUploading} onRevision={revision => { setAvatarV(value => value + 1); onMediaRevision?.(revision); }} disabled={operationBusy || uploading} />}{avatarPanel}</div>
            </section>
            <section id="character-traits" className="character-editor-chapter character-long-chapter" hidden={!chapterVisible('character-traits')} tabIndex={-1}><div className="character-short-field"><label className={labelCls} htmlFor="character-traits-label">特质栏名称<code>traits_label</code></label><TextInput id="character-traits-label" className="character-width320" value={card.traits_label ?? '能力与实力'} onChange={event => setField('traits_label', event.target.value)} placeholder="能力与实力、专业技能、专长与装备" data-testid="card-traits-label" /><p className="creation-intro">可按题材修改。</p></div>{longField('traits', card.traits_label?.trim() || '能力与实力')}</section>
            <section id="character-personality-relationships" className="character-editor-chapter character-long-chapter" hidden={!chapterVisible('character-personality-relationships')} tabIndex={-1}>{longField('personality', '性格')}{longField('scenario', '背景与关系', '当前情境：时间、地点、与 {{user}} 的关系等')}</section>
            <section id="character-opening-voice" className="character-editor-chapter character-scroll-chapter" hidden={!chapterVisible('character-opening-voice')} tabIndex={-1}>{longField('first_mes', '开场白', '角色的第一条消息')}
              <div className="character-greetings"><h3>备用开场白<code>alternate_greetings</code><Tag>{card.alternate_greetings.length}</Tag></h3>{card.alternate_greetings.map((greeting, index) => <div key={index} className="character-greeting"><span>#{index + 1}</span><Textarea aria-label={`备用开场白 ${index + 1}`} className="character-greeting-text" rows={2} value={greeting} onChange={event => setGreeting(index, event.target.value)} /><div className="character-greeting-actions"><IconButton label={`上移第 ${index + 1} 条`} icon={ArrowUp} size="sm" disabled={index === 0} onClick={() => moveGreeting(index, -1)} /><IconButton label={`下移第 ${index + 1} 条`} icon={ArrowDown} size="sm" disabled={index === card.alternate_greetings.length - 1} onClick={() => moveGreeting(index, 1)} /><IconButton label={`把第 ${index + 1} 条设为开场白`} icon={Pin} size="sm" onClick={() => promoteGreeting(index)} /><IconButton label={`删除第 ${index + 1} 条`} icon={Trash2} size="sm" onClick={() => removeGreeting(index)} /></div></div>)}<div className="character-greetings-add"><Button size="sm" icon={Plus} onClick={addGreeting}>添加备用开场白</Button></div></div>
              {longField('mes_example', '对话示例', '<START>\n{{user}}: 你好\n{{char}}: ……')}
            </section>
            <section id="character-profile" className="character-editor-chapter character-form-chapter" hidden={!chapterVisible('character-profile')} tabIndex={-1}><div className="character-form">
              <div className="character-form-grid">{!hideSourceWorld && <div><label className={labelCls} htmlFor="character-source-world">来源世界<code>source_world</code></label><EditorSelect id="character-source-world" aria-label="来源世界" value={sourceWorldId ?? ''} onChange={event => setSourceWorldId(event.target.value || null)}><option value="">未指定</option>{sourceWorldId && !worlds.some(world => world.id === sourceWorldId) && <option value={sourceWorldId}>来源世界暂不可用</option>}{worlds.map(world => <option key={world.id} value={world.id}>{world.title}</option>)}</EditorSelect><p className="creation-intro">用于整理和查找角色；不会在开局时自动加载这个世界。</p>{worldError && <p role="alert" className="creation-error">世界列表读取失败：{worldError}</p>}</div>}
                <div><label className={labelCls}>别名</label><TagInput label="别名" tags={aliases} onChange={setAliases} placeholder="输入别名后回车添加" /></div><div><label className={labelCls}>标签<code>tags</code></label><TagInput label="标签" tags={card.tags} onChange={tags => setField('tags', tags)} placeholder="输入标签后回车添加" /></div></div>
              <section className="character-form-group"><h3>创作者信息</h3><div className="character-form-grid"><div><label className={labelCls} htmlFor="character-creator">创作者<code>creator</code></label><TextInput id="character-creator" value={card.creator} onChange={event => setField('creator', event.target.value)} /></div><div><label className={labelCls} htmlFor="character-version">角色版本<code>character_version</code></label><TextInput id="character-version" value={card.character_version} onChange={event => setField('character_version', event.target.value)} /></div></div><div><label className={labelCls} htmlFor="character-creator-notes">创作者备注<code>creator_notes</code></label><Textarea id="character-creator-notes" rows={3} value={card.creator_notes} onChange={event => setField('creator_notes', event.target.value)} /></div></section>
              {authoringSource && fold('查看整理前原稿', <><p className="creation-intro">原稿保留供创作核对，不进入角色提示词。</p><pre className="creation-source">{authoringSource.text}</pre></>)}
            </div></section>
            <section id="character-prompts" className="character-editor-chapter character-long-chapter" hidden={!chapterVisible('character-prompts')} tabIndex={-1}>{(['system_prompt', 'post_history_instructions'] as const).map((field, index) => <div className="character-writing-field" key={field}><div className="character-writing-field__head"><label className={labelCls} htmlFor={`character-${field}`}>{index === 0 ? '系统提示' : '历史后指令'}<code>{field}</code><small>留空使用全局默认</small></label></div><Textarea id={`character-${field}`} aria-label={index === 0 ? '系统提示' : '历史后指令'} className="character-paper" value={card[field] ?? ''} onChange={event => setField(field, event.target.value || null)} /><p className="character-writing-hint">{index === 0 ? '可使用 {{original}} 引用项目基础角色指令词。' : '位于对话历史之后的指令（ST 的 UJB 位置），同样支持 {{original}}。'}</p></div>)}</section>
            {!hideRuntime && <section id="character-runtime" className="character-editor-chapter character-form-chapter" hidden={!chapterVisible('character-runtime')} tabIndex={-1}><div className="character-form character-form--narrow">{cid && !runtimeLoaded && <p>正在读取该角色现有配置…</p>}
              <div><label className={labelCls} htmlFor="character-model">模型<code>model</code></label><TextInput id="character-model" disabled={!runtimeLoaded} value={runtime.model} onChange={event => setRuntime(value => ({ ...value, model: event.target.value }))} placeholder="留空使用全局默认" /><p className="creation-intro">留空时继承全局配置。</p></div>
              <div><label className={labelCls} htmlFor="character-base-url">接口地址<code>base_url</code><small>可选</small></label><TextInput id="character-base-url" disabled={!runtimeLoaded} value={runtime.base_url} onChange={event => setRuntime(value => ({ ...value, base_url: event.target.value }))} placeholder="https://openrouter.ai/api/v1" /><p className="creation-intro">指定接口时使用设置页保存的对应密钥。</p></div>
              <div><label className={labelCls} htmlFor="character-max-tokens">单条回复上限<code>max_tokens</code></label><TextInput id="character-max-tokens" disabled={!runtimeLoaded} type="number" min={0} max={32768} step={256} className="character-width160" value={runtime.max_tokens} onChange={event => setRuntime(value => ({ ...value, max_tokens: Number(event.target.value) }))} /><p className="creation-intro">0表示自动，不设置人为上限；实际长度仍受模型容量和剧情需要影响。</p></div><p className="creation-notice">运行配置不写入角色卡。</p>
            </div></section>}
            {!showSide && <section id="character-preview" className="character-editor-chapter character-companion-chapter" hidden={!chapterVisible('character-preview')} tabIndex={-1}><Tabs value={previewTab} onValueChange={setPreviewTab}><TabsList aria-label="预览与试聊"><TabsTrigger value="preview">人设预览</TabsTrigger><TabsTrigger value="trial">试聊</TabsTrigger></TabsList></Tabs>{previewTab === 'trial' ? renderTrial() : renderPreview()}</section>}
          </div>
          <nav className="character-chapter-pager workbench-column__foot" aria-label="章节切换"><span>{countText}</span><div><Button size="sm" disabled={chapterIndex <= 0} onClick={() => selectChapter(chapters[chapterIndex - 1][0])}>上一节</Button><Button size="sm" disabled={chapterIndex >= chapters.length - 1} onClick={() => selectChapter(chapters[chapterIndex + 1][0])}>下一节</Button></div></nav>
        </div>
        {showSide && <aside id="character-related" className="character-editor-related workbench-column workbench-column--aside" aria-label="速览、人设预览与试聊"><div className="workbench-column__head"><Tabs value={sideTab} onValueChange={setSideTab}><TabsList aria-label="右栏内容" className="character-side-tabs"><TabsTrigger value="overview">速览</TabsTrigger><TabsTrigger value="preview">人设预览</TabsTrigger><TabsTrigger value="trial">试聊</TabsTrigger></TabsList></Tabs></div>{sideTab === 'overview' ? renderOverview() : sideTab === 'trial' ? renderTrial() : renderPreview()}</aside>}
      </WorkbenchDesk>
    </div>
    {avatarCrop && <CharacterCropDialog source={avatarCrop} kind="avatar" onCancel={() => setAvatarCrop(null)} onSave={onAvatarFile} />}
    <ConfirmDialog open={confirmExit} title="离开角色手稿？" description="当前修改尚未保存。离开后会保留本地文字草稿，选中的图片需要重新选择。" confirmLabel="保留草稿并离开" onClose={() => { setConfirmExit(false); setPendingExit(null); }} onConfirm={() => { flushDraft(); setConfirmExit(false); if (pendingExit) { navigate(pendingExit); setPendingExit(null); } else onClose(); }} />
  </>;
  return presentation === 'page' ? <div className="character-editor-page">{content}</div> : <SurfaceDialog title={cid ? '编辑角色' : '新建角色'} className="character-editor-dialog" onClose={requestClose} busy={operationBusy || uploading || fullBodyUploading} nested={false}><Ornament variant="dialog" tone="iris" />{content}</SurfaceDialog>;
}

// ---------- 内部小组件 ----------

function CharacterArtPanel({
  characterId,
  characterName,
  appearance,
  onPicked, onUploading, onRevision, disabled,
}: {
  characterId?: string;
  onPicked?: (file: File | null) => void;
  onUploading: (busy: boolean) => void;
  onRevision?: (revision: number) => void;
  disabled?: boolean;
  characterName: string;
  appearance: string;
}) {
  const [assetRevision, setAssetRevision] = useState(() => Date.now());
  const [missing, setMissing] = useState(!characterId);
  const [localImage, setLocalImage] = useState<string | null>(null);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadFailure, setUploadFailure] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  useEffect(() => () => { if (localImage) URL.revokeObjectURL(localImage); }, [localImage]);
  const [notice, setNotice] = useState("");
  const [fullBodyOpen, setFullBodyOpen] = useState(false);
  const [cropSource, setCropSource] = useState<File | string | null>(null);
  const imageUrl = localImage || `/api/v1/characters/${encodeURIComponent(characterId || "")}/full-body?v=${assetRevision}`;
  const prompt = [
    `$character-card-art 为角色「${characterName.trim() || "当前角色"}」生成或更新头像和正面全身立绘。此指令包含完整外貌依据，可在另一台已打开本项目的电脑上使用，不依赖当前聊天记录。`,
    `角色 ID：${characterId}`,
    `角色名称：${characterName.trim() || "未命名角色"}`,
    `角色卡外貌设定：\n${appearance.trim() || "（角色卡的外貌字段为空；请从本项目当前数据中的该角色卡读取外貌字段。如新电脑没有对应数据，请先询问，不要自行编造外貌。）"}`,
    "生成完成后，根据项目技能确定当前机器的数据目录，并保存为该角色目录下的 avatar.png 与 full_body.png；不要写入代码仓库。",
  ].join("\n\n");

  const reload = () => {
    setMissing(false);
    setAssetRevision(Date.now());
  };
  const uploadImage = async (file: File) => {
    setUploadFailure("");
    if (!characterId) { setLocalImage(URL.createObjectURL(file)); setMissing(false); onPicked?.(file); setNotice("创建角色时一并保存这张立绘"); return; }
    setUploadBusy(true); onUploading(true);
    try { await useCharacterStore.getState().uploadMedia(characterId, 'full-body', file); const revision = useCharacterStore.getState().characters.find(item => item.id === characterId)?.revision; if (revision) onRevision?.(revision); reload(); setNotice("全身立绘已上传"); }
    catch (cause) { setUploadFailure(errMsg(cause)); throw cause; }
    finally { setUploadBusy(false); onUploading(false); }
  };
  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(prompt);
      setNotice("指令已复制，可以粘贴到 Codex");
    } catch {
      setNotice("自动复制不可用，可选中下方指令手动复制");
    }
  };

  return (
    <section className="character-art-panel">
      <div className="character-art-layout">
        <div className="character-art-preview">
          {missing ? (
            <div className="max-w-sm px-5 text-center text-slate-300">
              <div className="mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-full border border-indigo-100 bg-white text-2xl text-indigo-400 shadow-sm">
                <img src={moonweaveAsset("v3/cards/card-silver-archive-room.webp")} alt="" className="character-art-placeholder" />
              </div>
              <p className="text-sm font-medium text-slate-800">还没有全身立绘</p>
              <p className="mt-2 text-xs leading-relaxed text-slate-500">
                可上传已有全身图，也可以用项目内的 Codex 角色插画技能生成。
              </p>

            </div>
          ) : (
            <Button
              type="button"
              onClick={() => setFullBodyOpen(true)}
              aria-label={`放大查看${characterName || "角色"}的全身立绘`}
              variant="secondary"
            >
              <img
                key={assetRevision}
                src={imageUrl}
                alt={`${characterName || "角色"}的全身立绘`}
                onError={() => setMissing(true)}
                className="character-art-image"
              />

            </Button>
          )}
        </div>

        <aside className="character-art-details">
          <div>
            <h3 className="text-base font-semibold text-slate-800">全身立绘</h3>
            <p className="mt-1 text-xs leading-relaxed text-slate-500">
              画像按 9:16 展示。点击图片放大查看。
            </p>
          </div>

          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
            <p className="text-xs font-medium text-slate-700">
              项目已内置 Codex 技能
            </p>
            <p className="mt-1 text-xs text-indigo-600">$character-card-art</p>
            <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
              指令会附带角色 ID 和外貌设定，可复制到另一台打开本项目的 Codex 使用。
            </p>
          </div>

          <Collapsible.Root className="character-art-instructions"><Collapsible.Trigger asChild><Button variant="ghost" size="sm">查看将复制的指令与外貌设定</Button></Collapsible.Trigger><Collapsible.Content><pre>{prompt}</pre></Collapsible.Content></Collapsible.Root>

          <div className="flex flex-wrap gap-2">
            <input ref={fileInput} type="file" hidden accept="image/png,image/jpeg,image/webp" aria-label="选择全身立绘文件" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) setCropSource(file); }} />
            <Button type="button" variant="tonal" disabled={disabled || uploadBusy} onClick={() => fileInput.current?.click()}>{uploadBusy ? '上传中…' : characterId ? '上传全身立绘' : '选择全身立绘'}</Button>
            <Button type="button" className={btnGhost} disabled={!characterId || uploadBusy} onClick={() => void copyPrompt()}>
              复制给 Codex
            </Button>
            <Button type="button" className={btnGhost} disabled={!characterId || uploadBusy} onClick={reload}>
              重新读取
            </Button>
            {!characterId && localImage && <Button variant="danger" onClick={() => { setLocalImage(null); setMissing(true); onPicked?.(null); }}>移除立绘</Button>}
            {!missing && <><Button type="button" className={btnGhost} onClick={() => setFullBodyOpen(true)}>放大查看</Button><Button disabled={disabled || uploadBusy} onClick={() => setCropSource(imageUrl)}>重新裁剪</Button></>}
          </div>
          <small className="text-xs text-slate-500">PNG、JPEG、WebP，全身图不超过 20MB。</small>
          {uploadFailure && <p role="alert" className="text-xs text-rose-500">{uploadFailure}</p>}
          <p aria-live="polite" className="min-h-4 text-[10px] text-slate-400">
            {notice}
          </p>
        </aside>
      </div>
      {cropSource && <CharacterCropDialog source={cropSource} kind="portrait" onCancel={() => setCropSource(null)} onSave={uploadImage} />}
      {fullBodyOpen && (
        <SurfaceDialog title={`${characterName || "角色"}的全身立绘`} className="character-art-dialog" onClose={() => setFullBodyOpen(false)} busy={false} nested={true}>
          <div className="character-art-dialog-surface" onClick={(event) => event.stopPropagation()}>
            <img src={imageUrl} alt={`${characterName || "角色"}的全身立绘原图`} className="character-art-original" />
            <Button type="button" onClick={() => setFullBodyOpen(false)} aria-label="关闭立绘" variant="secondary">关闭</Button>
          </div>
        </SurfaceDialog>
      )}
    </section>
  );
}

/** 标签式输入：回车添加，× 删除 */
function TagInput({
  label,
  tags,
  onChange,
  placeholder,
}: {
  label: string;
  tags: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
}) {
  const [input, setInput] = useState("");
  const add = () => {
    const v = input.trim();
    setInput("");
    if (!v || tags.includes(v)) return;
    onChange([...tags, v]);
  };
  return (
    <div className="character-tags-input">
      {tags.map(t => <Tag key={t} tone="accent" onRemove={() => onChange(tags.filter(value => value !== t))} removeLabel={`移除${label}「${t}」`}>{t}</Tag>)}
      <TextInput
        className="character-tag-input" tone="quiet" aria-label={`添加${label}`}
        value={input}
        placeholder={placeholder}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.nativeEvent.isComposing) {
            e.preventDefault();
            add();
          } else if (e.key === 'Backspace' && !input && tags.length) {
            onChange(tags.slice(0, -1));
          }
        }}
      />
    </div>
  );
}

/** Native-style adapter keeps current form events while adopting the shared Select primitive. */
function EditorSelect({ children, onChange, value, className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  const options = Children.toArray(children).filter(isValidElement).map(child => {
    const option = child as React.ReactElement<{ value: string; children: ReactNode }>;
    return { value: String(option.props.value || '__none__'), label: option.props.children };
  });
  return <Select options={options} value={String(value || '__none__')} onValueChange={next => onChange?.({ target: { value: next === '__none__' ? '' : next } } as ChangeEvent<HTMLSelectElement>)}
    disabled={props.disabled} id={props.id} aria-label={props['aria-label']} name={props.name} className={className} />;
}
