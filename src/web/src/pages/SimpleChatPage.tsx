import { useQuery } from '@tanstack/react-query';
import { resourceQueries } from '../features/resources/resourceQueries';
import { queryClient } from '../queryClient';
import { Avatar, Button, TextInput, Textarea, WritingPanel, Select, Checkbox } from "../design-system";
import { SurfaceDialog } from "../design-system/SurfaceDialog";
import { SimpleChatList } from "../features/simple-chat/SimpleChatList";
import { chatTitle, modelShortName } from "../features/simple-chat/simpleChatPresentation.js";
import { useAppearanceStore } from "../appearance/store";
import { moonweaveAsset } from "../appearance/moonweaveAssets";
import { LoadState } from "../components/LoadState";
import { isCancelled } from "../api/request";
import { beginLoad } from "../utils/loadDiagnostics";
import { RefreshCw } from "lucide-react";
import { createClientId } from "../utils/clientId.js";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import {
  ArrowDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  MessageCircle,
  Pencil,
  Plus,
  RotateCcw,
  Send,
  Settings2,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { simpleChatClient as api } from '../features/simple-chat/simpleChatClient';
import {
  GATEWAY_PRESETS,
  profileIdForGateway as profileId,
} from "../api/gateways";
import { ModelPicker } from "../components/ModelPicker";
import { GenerationControls } from "../components/GenerationControls";
import { MessageActionBar } from "../components/MessageActionBar";
import {
  SimpleMessageEditDialog,
  type SimpleEditPreview,
} from "../components/SimpleMessageEditDialog";
import {
  editConfirmedSimpleChat,
  deleteConfirmedSimpleChat,
} from "../api/simpleChatEditing";
import {
  isLanSessionExpiredError,
  probeLanSessionAfterSseClose,
} from "../api/lanSession";
import { useComposerInput } from "../components/useComposerInput";
import { useAutoSizeTextarea } from "../components/useAutoSizeTextarea";
import type {
  SimpleChat,
  SimpleChatMessage,
  SimpleChatUsage,
  SimpleChatSummary,
} from "../types";

type ConfigDraft = Pick<
  SimpleChat,
  | "title"
  | "gateway"
  | "model"
  | "provider"
  | "provider_allow_fallbacks"
  | "system_prompt"
  | "generation"
  | "input_price_per_million"
  | "output_price_per_million"
  | "price_currency"
>;
const draftOf = (chat: SimpleChat): ConfigDraft => ({
  title: chat.title,
  gateway: chat.gateway,
  model: chat.model,
  provider: chat.provider,
  provider_allow_fallbacks: chat.provider_allow_fallbacks,
  system_prompt: chat.system_prompt,
  generation: chat.generation,
  input_price_per_million: chat.input_price_per_million,
  output_price_per_million: chat.output_price_per_million,
  price_currency: chat.price_currency,
});
function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : "操作失败";
}
const tokens = (value: number | undefined) =>
  (value ?? 0).toLocaleString("zh-CN");
function priceText(usage: SimpleChatUsage): string {
  if (typeof usage.cost_usd === "number")
    return `实付 $${usage.cost_usd.toFixed(6)}`;
  if (typeof usage.estimated_cost === "number")
    return `估算 ${usage.cost_currency ?? "USD"} ${usage.estimated_cost.toFixed(6)}`;
  return "费用未提供";
}
function finishNote(reason: string | undefined): string {
  if (!reason || reason === "stop") return "";
  if (reason === "cancelled")
    return "你已停止生成；当前内容已保留。上游已处理部分仍可能计费。";
  if (["length", "max_tokens", "MAX_TOKENS"].includes(reason))
    return "上游达到输出上限，回复可能被截断。";
  if (["content_filter", "safety", "SAFETY"].includes(reason))
    return "上游内容过滤，回复可能不完整。";
  if (reason === "connection_closed" || reason === "connection_error")
    return "流连接提前结束，回复可能不完整。";
  if (reason === "unknown")
    return "上游未提供结束原因；若句子中断，可再发送“继续”。";
  return `上游结束原因：${reason}`;
}

export default function SimpleChatPage() {
  const { chatId } = useParams();
  const avatarSize = useAppearanceStore(state => state.preferences.dialogue_avatar_size);
  const navigate = useNavigate();
  const listQueryState = useQuery(resourceQueries.simpleChats());
  const list = listQueryState.data ?? [];
  const listLoading = listQueryState.isPending;
  const [readState, setReadState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [readError, setReadError] = useState("");
  const [readRetry, setReadRetry] = useState(0);

  const [chat, setChat] = useState<SimpleChat | null>(null);
  const settingsQuery = useQuery(resourceQueries.settings());
  const settings = settingsQuery.data ?? null;
  const [draft, setDraft] = useState<ConfigDraft | null>(null);






  const [showConfig, setShowConfig] = useState(false);
  const [wideSession, setWideSession] = useState(false);
  const layoutRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = layoutRef.current;
    if (!element) return;
    const observer = new ResizeObserver(entries => setWideSession(entries[0].contentRect.width >= 1500));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [generationActive, setGenerationActive] = useState(false);
  const [stopPending, setStopPending] = useState(false);
  const [followingTail, setFollowingTail] = useState(true);
  const [pendingId, setPendingId] = useState("");
  const [streamText, setStreamText] = useState("");
  const [editingId, setEditingId] = useState("");
  const [editText, setEditText] = useState("");
  const [editPreview, setEditPreview] = useState<SimpleEditPreview | null>(
    null,
  );
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState("");
  const [listQuery, setListQuery] = useState("");
  const [error, setError] = useState("");
  const messagesRef = useRef<HTMLDivElement>(null);
  const followTailRef = useRef(true);
  const sendingRef = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  useAutoSizeTextarea(inputRef, input, readState === "ready" ? chat?.id : null);
  const activeChatRef = useRef(chatId);
  activeChatRef.current = chatId;
  const eventRevision = useRef(0);

  const pageLive = useRef(true);

  const jumpToLatest = () => {
    const element = messagesRef.current;
    if (!element) return;
    followTailRef.current = true;
    setFollowingTail(true);
    element.scrollTo({ top: element.scrollHeight, behavior: "auto" });
  };

  const handleMessagesScroll = () => {
    const element = messagesRef.current;
    if (!element) return;
    const shouldFollow =
      element.scrollHeight - element.clientHeight - element.scrollTop < 72;
    followTailRef.current = shouldFollow;
    setFollowingTail((current) =>
      current === shouldFollow ? current : shouldFollow,
    );
  };

  const refreshList = () => { void queryClient.invalidateQueries({ queryKey: resourceQueries.simpleChats().queryKey }); };
  useEffect(() => {
    pageLive.current = true;
    return () => { pageLive.current = false; };
  }, []);
  useEffect(() => {
    const cause = listQueryState.error ?? settingsQuery.error;
    if (cause) setError(errorText(cause));
  }, [listQueryState.error, settingsQuery.error]);
  useEffect(() => {
    if (!chatId) {
      setChat(null);
      setDraft(null);
      setReadState("idle");
      setReadError("");
      return;
    }
    let live = true;
    const controller = new AbortController();
    const finish = beginLoad("state", "/simple-chats/:id");
    setReadState("loading");
    setReadError("");
    sendingRef.current = false;
    followTailRef.current = true;
    setFollowingTail(true);
    setChat(null);
    setDraft(null);
    setError("");
    setEditingId("");
    setEditPreview(null);
    setEditError("");
    setEditBusy(false);
    setBusy(false);
    setGenerationActive(false);
    setStopPending(false);
    setPendingId("");
    setStreamText("");
    void api
      .getSimpleChat(chatId, { signal: controller.signal })
      .then((value) => {
        if (live) {
          setChat(value);
          setDraft(draftOf(value));
          setReadState("ready");
          finish();
        }
      })
      .catch((cause) => {
        finish(isCancelled(cause) ? "cancelled" : "error");
        if (live && !isCancelled(cause)) {
          setReadError(errorText(cause));
          setReadState("error");
        }
      });
    return () => {
      live = false;
      controller.abort();
    };
  }, [chatId, readRetry]);
  useEffect(() => {
    if (!chatId || readState !== "ready" || chat?.id !== chatId) return;
    let live = true;
    const controller = new AbortController();
    const stream = new EventSource(
      `/api/v1/simple-chats/${encodeURIComponent(chatId)}/events`,
    );
    stream.addEventListener("message.pending", (event) => {
      if (!live || activeChatRef.current !== chatId) return;
      const data = JSON.parse((event as MessageEvent).data) as { id: string };
      setPendingId(data.id);
      setStreamText("");
    });
    stream.addEventListener("message.delta", (event) => {
      if (!live || activeChatRef.current !== chatId) return;
      const data = JSON.parse((event as MessageEvent).data) as {
        id: string;
        delta: string;
      };
      setPendingId(data.id);
      setStreamText((current) => current + data.delta);
    });
    stream.addEventListener("message.final", (event) => {
      if (!live || activeChatRef.current !== chatId) return;
      eventRevision.current++;
      const data = JSON.parse((event as MessageEvent).data) as {
        message: SimpleChatMessage;
      };
      setChat((current) =>
        current?.id === chatId
          ? {
              ...current,
              messages: current.messages.some(
                (item) => item.id === data.message.id,
              )
                ? current.messages.map((item) =>
                    item.id === data.message.id ? data.message : item,
                  )
                : [...current.messages, data.message],
            }
          : current,
      );
      if (data.message.role === "assistant") {
        setPendingId("");
        setStreamText("");
      }
    });
    stream.addEventListener("message.error", (event) => {
      if (!live || activeChatRef.current !== chatId) return;
      const data = JSON.parse((event as MessageEvent).data) as {
        error: string;
      };
      setError(data.error);
      setPendingId("");
      setStreamText("");
    });
    stream.addEventListener("message.cancelled", () => {
      if (!live || activeChatRef.current !== chatId) return;
      setPendingId("");
      setStreamText("");
    });
    const refreshAfterOpen = async () => {
      if (sendingRef.current) return;
      // Re-read on every connection open to recover events missed while away.
      for (let attempt = 0; attempt < 2 && live; attempt++) {
        const revision = eventRevision.current;
        try {
          const value = await api.getSimpleChat(chatId, {
            signal: controller.signal,
          });
          if (!live || activeChatRef.current !== chatId) return;
          if (sendingRef.current) return;
          if (revision !== eventRevision.current) continue;
          setChat(value);
          return;
        } catch (cause) {
          if (live && !isCancelled(cause)) setError(errorText(cause));
          return;
        }
      }
    };
    stream.onopen = () => {
      void refreshAfterOpen();
    };
    stream.onerror = () => {
      if (stream.readyState === EventSource.CLOSED)
        probeLanSessionAfterSseClose();
    };
    return () => {
      live = false;
      controller.abort();
      stream.close();
    };
  }, [chatId, readState, chat?.id]);
  useLayoutEffect(() => {
    const element = messagesRef.current;
    if (!element || !followTailRef.current) return;
    element.scrollTop = element.scrollHeight;
  }, [chat?.messages.length, streamText, pendingId]);

  const profile = profileId(draft?.gateway ?? "");
  const catalogQuery = useQuery({ ...resourceQueries.models(profile), enabled: showConfig && !!settings && !!profile });
  const catalog = catalogQuery.data?.models ?? [];
  const catalogError = catalogQuery.error ? errorText(catalogQuery.error) : '';
  const catalogLoading = catalogQuery.isFetching;
  const [providerModel, setProviderModel] = useState('');
  useEffect(() => { const timer = window.setTimeout(() => setProviderModel(draft?.model ?? ''), 250); return () => window.clearTimeout(timer); }, [draft?.model]);
  const providersQuery = useQuery({ ...resourceQueries.providers(providerModel), enabled: showConfig && profile === 'openrouter' && providerModel.includes('/') });
  const providers = providersQuery.data?.providers ?? [];
  const providerLoading = providersQuery.isFetching;

  const gatewayOptions = settings
    ? [
        ...GATEWAY_PRESETS.map((preset) => ({
          ...preset,
          gateway:
            profileId(settings.gateway) === preset.id
              ? settings.gateway
              : settings.gateway_profiles[preset.id]?.gateway || preset.gateway,
        })),
        ...Object.entries(settings.gateway_profiles)
          .filter(([id]) => !GATEWAY_PRESETS.some((preset) => preset.id === id))
          .map(([id, item]) => ({
            id,
            gateway: item.gateway,
            label: id.replace(/^custom:/, ""),
            model: item.model,
          })),
        ...(profileId(settings.gateway).startsWith("custom:") &&
        !settings.gateway_profiles[profileId(settings.gateway)]
          ? [
              {
                id: profileId(settings.gateway),
                gateway: settings.gateway,
                label: profileId(settings.gateway).replace(/^custom:/, ""),
                model: settings.model,
              },
            ]
          : []),
      ].filter(
        (item, index, all) =>
          item.gateway &&
          all.findIndex((row) => row.gateway === item.gateway) === index,
      )
    : [];
  const configured = Boolean(
    settings?.info.configured_providers.includes(profile),
  );
  const sync = (value: SimpleChat) => {
    if (value.id !== activeChatRef.current) return;
    eventRevision.current++;
    setChat(value);
    setDraft(draftOf(value));
    refreshList();
  };
  const create = async () => {
    try {
      const value = await api.createSimpleChat();
      refreshList();
      setShowConfig(true);
      navigate(`/simple-chats/${value.id}`);
    } catch (cause) {
      setError(errorText(cause));
    }
  };
  const saveConfig = async () => {
    if (!chat || !draft || busy) return;
    setError("");
    setBusy(true);
    try {
      sync(await api.patchSimpleChat(chat.id, draft));
      setShowConfig(false);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };
  const send = async () => {
    const content = (inputRef.current?.value ?? input).trim();
    if (!chat || busy || sendingRef.current || !content) return;
    sendingRef.current = true;
    setInput("");
    setError("");
    setBusy(true);
    setGenerationActive(true);
    setStopPending(false);
    let optimisticId = "";
    try {
      optimisticId = `msg-${createClientId().replace(/-/g, "")}`;
      setChat((current) =>
        current
          ? {
              ...current,
              messages: [
                ...current.messages,
                {
                  id: optimisticId,
                  role: "user",
                  content,
                  created_at: new Date().toISOString(),
                  variants: [],
                  active_variant: null,
                  usage: {},
                  variant_usages: [],
                },
              ],
            }
          : current,
      );
      eventRevision.current++;
      sync(await api.sendSimpleChatMessage(chat.id, content, optimisticId));
    } catch (cause) {
      if (activeChatRef.current !== chat.id) return;
      if (!optimisticId || isLanSessionExpiredError(cause)) {
        setInput((current) => current || content);
        setChat((current) =>
          current
            ? {
                ...current,
                messages: current.messages.filter(
                  (item) => item.id !== optimisticId,
                ),
              }
            : current,
        );
        setError(
          isLanSessionExpiredError(cause)
            ? "会话已过期；输入已恢复。请重新配对后手动重试，消息不会自动发送。"
            : errorText(cause),
        );
      } else {
        setError(errorText(cause));
        try {
          sync(await api.getSimpleChat(chat.id));
        } catch {
          /* retain visible error */
        }
      }
    } finally {
      if (activeChatRef.current === chat.id) {
        sendingRef.current = false;
        setBusy(false);
        setGenerationActive(false);
        setStopPending(false);
        setPendingId("");
        setStreamText("");
      }
    }
  };
  const stopGeneration = async () => {
    if (!chat || !generationActive || stopPending) return;
    setStopPending(true);
    setError("");
    try {
      const result = await api.stopSimpleChatGeneration(chat.id);
      if (!result.stopping) {
        setError("这次生成已经结束，正在同步最终回复。");
        setStopPending(false);
      }
    } catch (cause) {
      setError(`停止请求未能送达：${errorText(cause)}`);
      setStopPending(false);
    }
  };
  const removeChat = async () => {
    if (!chat || !window.confirm(`删除“${chat.title}”及其全部消息？`)) return;
    try {
      await api.deleteSimpleChat(chat.id);
      refreshList();
      navigate("/simple-chats");
    } catch (cause) {
      setError(errorText(cause));
    }
  };
  const renameListChat = async (item: SimpleChatSummary) => {
    const title = window.prompt("聊天标题", item.title);
    if (title === null || !title.trim()) return;
    try {
      const value = await api.patchSimpleChat(item.id, { title: title.trim() });
      refreshList();
      if (activeChatRef.current === item.id) {
        setChat(current => current?.id === item.id ? { ...current, title: value.title } : current);
        setDraft(current => current ? { ...current, title: value.title } : current);
      }
    } catch (cause) { setError(errorText(cause)); }
  };
  const deleteListChat = async (item: SimpleChatSummary) => {
    if (!window.confirm(`删除“${item.title}”及其全部消息？`)) return;
    try {
      await api.deleteSimpleChat(item.id);
      refreshList();
      if (activeChatRef.current === item.id) navigate("/simple-chats");
    } catch (cause) { setError(errorText(cause)); }
  };
  const openEdit = async (messageId: string, keepText = false) => {
    if (!chat || busy || editBusy) return;
    const targetChatId = chat.id;
    setEditBusy(true);
    setEditError("");
    try {
      const fresh = await api.getSimpleChat(targetChatId);
      if (activeChatRef.current !== targetChatId) return;
      const index = fresh.messages.findIndex(
        (message) => message.id === messageId,
      );
      if (index < 0) throw new Error("这条消息已不存在，请选择另一条。");
      sync(fresh);
      setEditingId(messageId);
      if (!keepText) setEditText(fresh.messages[index].content);
      setEditPreview({
        updatedAt: fresh.updated_at,
        futureCount: fresh.messages.length - index - 1,
        variantCount:
          fresh.messages[index].role === "assistant"
            ? fresh.messages[index].variants.length
            : 0,
      });
    } catch (cause) {
      if (activeChatRef.current === targetChatId) {
        setEditError(errorText(cause));
        setError(errorText(cause));
      }
    } finally {
      if (activeChatRef.current === targetChatId) setEditBusy(false);
    }
  };
  const saveEdit = async () => {
    if (
      !chat ||
      !editingId ||
      !editPreview ||
      !editText.trim() ||
      editBusy ||
      busy
    )
      return;
    const targetChatId = chat.id;
    setEditBusy(true);
    setEditError("");
    try {
      const updated = await editConfirmedSimpleChat(
        targetChatId,
        editingId,
        editText.trim(),
        editPreview.futureCount > 0,
        editPreview.updatedAt,
      );
      if (activeChatRef.current !== targetChatId) return;
      sync(updated);
      setEditingId("");
      setEditPreview(null);
    } catch (cause) {
      if (activeChatRef.current === targetChatId)
        setEditError(errorText(cause));
    } finally {
      if (activeChatRef.current === targetChatId) setEditBusy(false);
    }
  };
  const removeMessage = async (message: SimpleChatMessage) => {
    if (!chat || busy || editBusy) return;
    const targetChatId = chat.id;
    try {
      const fresh = await api.getSimpleChat(targetChatId);
      if (activeChatRef.current !== targetChatId) return;
      const index = fresh.messages.findIndex((item) => item.id === message.id);
      if (index < 0) throw new Error("这条消息已不存在。");
      const futureCount = fresh.messages.length - index - 1;
      if (
        !window.confirm(
          futureCount
            ? `删除这条消息及其后续 ${futureCount} 条对话？`
            : "删除这条消息？",
        )
      )
        return;
      sync(
        await deleteConfirmedSimpleChat(
          targetChatId,
          message.id,
          futureCount > 0,
          fresh.updated_at,
        ),
      );
    } catch (cause) {
      if (activeChatRef.current === targetChatId) setError(errorText(cause));
    }
  };
  const regenerate = async (message: SimpleChatMessage) => {
    if (!chat || busy) return;
    setError("");
    setBusy(true);
    setGenerationActive(true);
    setStopPending(false);
    setPendingId(message.id);
    setStreamText("");
    try {
      sync(await api.regenerateSimpleChatMessage(chat.id, message.id));
    } catch (cause) {
      if (activeChatRef.current !== chat.id) return;
      setError(errorText(cause));
      try {
        sync(await api.getSimpleChat(chat.id));
      } catch {
        /* retain error */
      }
    } finally {
      if (activeChatRef.current === chat.id) {
        setBusy(false);
        setGenerationActive(false);
        setStopPending(false);
        setPendingId("");
        setStreamText("");
      }
    }
  };

  const generations = chat?.generations ?? [];
  const totalInput = generations.reduce(
    (sum, item) => sum + (item.usage.input_tokens ?? 0),
    0,
  );
  const totalOutput = generations.reduce(
    (sum, item) => sum + (item.usage.output_tokens ?? 0),
    0,
  );
  const actualTotal = generations.reduce(
    (sum, item) => sum + (item.usage.cost_usd ?? 0),
    0,
  );
  const estimatedTotals = generations.reduce<Record<string, number>>(
    (totals, item) => {
      if (item.usage.estimated_cost != null) {
        const currency = item.usage.cost_currency ?? "USD";
        totals[currency] = (totals[currency] ?? 0) + item.usage.estimated_cost;
      }
      return totals;
    },
    {},
  );
  const unpricedCount = generations.filter(
    (item) => item.usage.cost_usd == null && item.usage.estimated_cost == null,
  ).length;

  const inputEvents = useComposerInput(inputRef, () => {
    void send();
  });

  const configContent = draft ? <div className="sc-config-content">
                <div className="sc-config-title">
                  <strong>本次聊天的模型配置</strong>
                  {!wideSession && <Button
                    type="button"
                    onClick={() => setShowConfig(false)}
                    aria-label="关闭"
                  >
                    <X size={18} />
                  </Button>}
                </div>
                <div className="sc-config-grid">
                  <label>
                    标题
                    <TextInput
                      value={draft.title}
                      onChange={(event) =>
                        setDraft({ ...draft, title: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    渠道
                    <Select aria-label="渠道" options={[{ value: "__custom__", label: "自定义地址" }, ...gatewayOptions.map(item => ({ value: item.gateway, label: item.label }))]}
                      value={
                        gatewayOptions.some(
                          (item) => item.gateway === draft.gateway,
                        )
                          ? draft.gateway
                          : "__custom__"
                      }
                      onValueChange={(value) => {
                        const selected = gatewayOptions.find(
                          (item) => item.gateway === value,
                        );
                        const source =
                          selected?.id === profileId(settings?.gateway ?? "")
                            ? settings
                            : settings?.gateway_profiles[selected?.id ?? ""];
                        setDraft({
                          ...draft,
                          gateway: selected?.gateway ?? draft.gateway,
                          model:
                            source?.model ?? selected?.model ?? draft.model,
                          provider:
                            selected?.id === "openrouter"
                              ? (source?.model_provider ?? "")
                              : "",
                          provider_allow_fallbacks:
                            source?.provider_allow_fallbacks ?? true,
                          input_price_per_million:
                            selected?.gateway === draft.gateway
                              ? draft.input_price_per_million
                              : null,
                          output_price_per_million:
                            selected?.gateway === draft.gateway
                              ? draft.output_price_per_million
                              : null,
                        });
                      }}
 />
                  </label>
                  <label className="sc-wide">
                    接口地址
                    <TextInput
                      spellCheck={false}
                      value={draft.gateway}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          gateway: event.target.value,
                          provider: "",
                          input_price_per_million: null,
                          output_price_per_million: null,
                        })
                      }
                    />
                  </label>
                  <div
                    className={`sc-model-cell ${profile === "openrouter" ? "" : "sc-wide"}`}
                  >
                    <ModelPicker
                      id="sc-model"
                      label="模型"
                      value={draft.model}
                      options={catalog}
                      placeholder={
                        catalogLoading ? "正在读取模型…" : "选择或输入模型 ID"
                      }
                      onChange={(value) =>
                        setDraft({
                          ...draft,
                          model: value,
                          provider: "",
                          input_price_per_million: null,
                          output_price_per_million: null,
                        })
                      }
                    />
                    <div className="sc-catalog-status">
                      {catalogLoading ? (
                        "正在自动读取模型候选…"
                      ) : catalogError ? (
                        <>
                          候选读取失败，仍可手动输入模型 ID。
                          <Button
                            type="button"
                            onClick={() =>
                              void catalogQuery.refetch()
                            }
                          >
                            重试
                          </Button>
                        </>
                      ) : (
                        `已读取 ${catalog.length} 个模型候选，可展开选择或输入 ID。`
                      )}
                    </div>
                  </div>
                  {profile === "openrouter" && (
                    <label>
                      上游供应商
                      <Select aria-label="上游供应商" value={draft.provider || "__auto__"}
                        options={[{ value: "__auto__", label: "自动选择" }, ...(draft.provider && !providers.some(item => item.slug === draft.provider) ? [{ value: draft.provider, label: draft.provider }] : []), ...providers.map(item => ({ value: item.slug, label: `${item.name} · ${item.slug}` }))]}
                        onValueChange={value => setDraft({ ...draft, provider: value === "__auto__" ? "" : value })} />
                      {providerLoading && <small>正在读取供应商…</small>}
                    </label>
                  )}
                  {profile === "openrouter" && (
                    <Checkbox className="sc-check" label="允许上游回退"
                        checked={draft.provider_allow_fallbacks}
                        onChange={(event) =>
                          setDraft({
                            ...draft,
                            provider_allow_fallbacks: event.target.checked,
                          })
                        }
                      />
                  )}
                  <div className="sc-wide sc-generation">
                    <strong>生成参数</strong>
                    <p>
                      留空使用模型默认值。当前聊天单独保存，新聊天默认继承总设置。
                    </p>
                    <GenerationControls
                      inputComponent={TextInput}
                      value={draft.generation}
                      onChange={(value) =>
                        setDraft({ ...draft, generation: value })
                      }
                      idPrefix="simple-chat-generation"
                      disabled={busy}
                    />
                  </div>
                  <div className="sc-wide sc-pricing">
                    <strong>价格 / 百万 token（可选）</strong>
                    <p>
                      网关返回实际费用时优先采用；未返回时，按这里的单价估算。不同渠道的价格请分别填写。
                      {profile === "getgoapi" && (
                        <>
                          {" "}
                          <a
                            href="https://getgoapi.com/en/models"
                            target="_blank"
                            rel="noreferrer"
                          >
                            查看 GetGoAPI 模型价格
                          </a>
                        </>
                      )}
                    </p>
                    <div>
                      <label>
                        输入单价
                        <TextInput
                          type="number"
                          min="0"
                          step="any"
                          value={draft.input_price_per_million ?? ""}
                          onChange={(event) =>
                            setDraft({
                              ...draft,
                              input_price_per_million:
                                event.target.value === ""
                                  ? null
                                  : Number(event.target.value),
                            })
                          }
                          placeholder="例如 0.40"
                        />
                      </label>
                      <label>
                        输出单价
                        <TextInput
                          type="number"
                          min="0"
                          step="any"
                          value={draft.output_price_per_million ?? ""}
                          onChange={(event) =>
                            setDraft({
                              ...draft,
                              output_price_per_million:
                                event.target.value === ""
                                  ? null
                                  : Number(event.target.value),
                            })
                          }
                          placeholder="例如 2.00"
                        />
                      </label>
                      <label>
                        币种
                        <TextInput
                          value={draft.price_currency}
                          maxLength={3}
                          onChange={(event) =>
                            setDraft({
                              ...draft,
                              price_currency: event.target.value.toUpperCase(),
                            })
                          }
                        />
                      </label>
                    </div>
                  </div>
                  <label className="sc-wide">
                    System Prompt
                    <Textarea
                      rows={5}
                      value={draft.system_prompt}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          system_prompt: event.target.value,
                        })
                      }
                      placeholder="告诉模型它应该如何回答…"
                    />
                  </label>
                </div>
                <div className="sc-config-foot">
                  <span>
                    {configured ? (
                      "已读取该渠道保存的密钥"
                    ) : (
                      <>
                        当前渠道未保存密钥，请先到{" "}
                        <Link to="/settings/connection">连接与模型</Link> 配置。
                      </>
                    )}
                  </span>
                  <Button
                    type="button"
                    className="sc-save-config"
                    onClick={() => void saveConfig()}
                    disabled={busy}
                  >
                    保存配置
                  </Button>
                </div>
  </div> : null;

  return (
    <div ref={layoutRef} className={`sc-layout ${chatId ? "has-detail" : "is-list"}`}>
      <SimpleChatList list={list} chatId={chatId} loading={listLoading} query={listQuery} onQuery={setListQuery}
        onCreate={() => void create()} onRename={item => void renameListChat(item)} onDelete={item => void deleteListChat(item)} busy={busy} error={error}
        onRetry={() => { setError(""); refreshList(); }} />
      <section className="sc-main">
        {error && (
          <div className="sc-error" role="alert">
            {error}
            <Button
              type="button"
              onClick={() => {
                setError("");
                refreshList();
              }}
              aria-label="重新读取聊天列表"
            >
              <RefreshCw size={16} />
            </Button>
          </div>
        )}
        {chatId && (readState !== "ready" || chat?.id !== chatId) ? (
          <LoadState
            title={
              readState === "error" ? "这段聊天未能打开" : "正在读取聊天记录"
            }
            error={readError || undefined}
            onRetry={
              readState === "error"
                ? () => setReadRetry((value) => value + 1)
                : undefined
            }
          />
        ) : !chat ? (
          <div className="sc-welcome">
            <div className="sc-welcome-art" aria-hidden="true"><img src={moonweaveAsset("v3/pageart/empty-unselected-route.png")} alt="" onError={event => { event.currentTarget.hidden = true; }} /><MessageCircle size={44} strokeWidth={1} /></div>
            <span className="sc-kicker">A QUIET CONVERSATION</span>
            <h1>从一句话开始</h1>
            <p>
              留一个安静的角落，聊想法、问问题。选择模型与渠道后，开始属于你的对话。
            </p>
            <Button
              type="button"
              className="sc-new-chat"
              onClick={() => void create()}
            >
              <Plus size={17} />
              新建聊天
            </Button>
          </div>
        ) : (
          <div className="sc-detail">
            <div className="sc-conversation">
            <header className="sc-head">
              <div>
                <Button variant="ghost" size="sm" className="sc-back" onClick={() => navigate("/simple-chats")}><ChevronLeft size={16} />聊天列表</Button>
                <h1>{chatTitle(chat.title)}</h1>
                <small>{modelShortName(chat.model)}</small>
              </div>
              <div className="sc-head-actions">
                <Button
                  type="button"
                  skin="secondary"
                  onClick={() => { if (wideSession) document.querySelector('.sc-session .sc-config-content')?.scrollIntoView({ behavior: 'smooth' }); else setShowConfig((value) => !value); }}
                >
                  <Settings2 size={17} /> 配置
                </Button>
                <Button
                  type="button"
                  onClick={() => void removeChat()}
                  title="删除聊天"
                >
                  <Trash2 size={17} />
                </Button>
              </div>
            </header>
            <div className="sc-usage-summary">
              {!generations.length && <span>暂无用量记录</span>}
              {generations.some(item => item.usage.usage_incomplete) && <span>用量不完整</span>}
              {generations.length > 0 && <span>
                累计 {tokens(totalInput + totalOutput)} tokens（输入{" "}
                {tokens(totalInput)} · 输出 {tokens(totalOutput)}）
              </span>}
              {generations.some((item) => item.usage.cost_usd != null) && (
                <span>实付 ${actualTotal.toFixed(6)}</span>
              )}
              {Object.entries(estimatedTotals).map(([currency, amount]) => (
                <span key={currency}>
                  估算 {currency} {amount.toFixed(6)}
                </span>
              ))}
              {unpricedCount > 0 && <span>{unpricedCount} 次费用未知</span>}
            </div>
            {showConfig && !wideSession && configContent && (
              <SurfaceDialog title="本次聊天的模型配置" className="sc-config" onClose={() => setShowConfig(false)} busy={busy}>{configContent}</SurfaceDialog>
            )}
            <div
              className="sc-messages"
              ref={messagesRef}
              onScroll={handleMessagesScroll}
            >
              {!chat.messages.length && (
                <div className="sc-empty">
                  <img className="sc-empty-emblem" src={moonweaveAsset("v3/conversation/linked-thread-emblem.webp")} alt="" onError={event => { event.currentTarget.hidden = true; }} /><MessageCircle size={30} />
                  <strong>开始对话</strong>
                  <span>这段聊天独立保存，不会注入故事中的角色或世界书。</span>
                </div>
              )}
              <div className="sc-message-stack">
              {chat.messages.map((message, index) => (
                <article
                  className={`sc-message sc-${message.role}`}
                  key={message.id}
                >
                  <div className="sc-message-name">
                    <Avatar name={message.role === "user" ? "你" : modelShortName(chat.model)} size={avatarSize} />
                    {message.role === "user" ? "你" : "助手"}
                  </div>
                  <div className="sc-bubble">
                    {pendingId === message.id
                      ? streamText || (
                          <span className="sc-generating">
                            正在生成<span className="sc-dots">···</span>
                          </span>
                        )
                      : message.content}
                  </div>
                  {message.role === "assistant" &&
                    Object.keys(message.usage ?? {}).length > 0 && (
                      <div className="sc-message-usage">
                        {message.usage.usage_incomplete ? (
                          "本次已停止；上游未返回完整 token 用量"
                        ) : (
                          <>
                            输入 {tokens(message.usage.input_tokens)} · 输出{" "}
                            {tokens(message.usage.output_tokens)} · 共{" "}
                            {tokens(
                              (message.usage.input_tokens ?? 0) +
                                (message.usage.output_tokens ?? 0),
                            )}{" "}
                            tokens
                            {message.usage.reasoning_tokens != null && (
                              <>
                                （思考 {tokens(message.usage.reasoning_tokens)}
                                ）
                              </>
                            )}
                          </>
                        )}{" "}
                        <span>· {priceText(message.usage)}</span>
                      </div>
                    )}
                  {message.role === "assistant" &&
                    finishNote(message.usage?.finish_reason) && (
                      <div className="sc-finish-note" role="status">
                        {finishNote(message.usage.finish_reason)}
                      </div>
                    )}
                  <div className="sc-message-tools">
                    <MessageActionBar
                      actions={[
                        {
                          id: "copy",
                          label: "复制",
                          icon: <Copy size={14} />,
                          onClick: () =>
                            navigator.clipboard.writeText(message.content),
                        },
                        {
                          id: "edit",
                          label: "编辑",
                          title: "编辑前核对后续对话与候选的变化",
                          icon: <Pencil size={14} />,
                          disabled: busy || editBusy,
                          onClick: () => openEdit(message.id),
                        },
                        ...(message.role === "assistant" &&
                        index === chat.messages.length - 1
                          ? [
                              {
                                id: "regenerate",
                                label: "重新生成",
                                icon: <RotateCcw size={14} />,
                                disabled: busy || editBusy,
                                onClick: () => regenerate(message),
                              },
                            ]
                          : []),
                        {
                          id: "delete",
                          label: "删除",
                          title: "删除前核对后续对话范围",
                          icon: <Trash2 size={14} />,
                          danger: true,
                          disabled: busy || editBusy,
                          onClick: () => removeMessage(message),
                        },
                      ]}
                    />
                    {message.role === "assistant" &&
                      message.variants.length > 1 && (
                        <span className="sc-variants">
                          <Button
                            type="button"
                            disabled={
                              (message.active_variant ?? 0) <= 0 || busy
                            }
                            aria-label="上一候选"
                            onClick={() =>
                              void api
                                .switchSimpleChatVariant(
                                  chat.id,
                                  message.id,
                                  (message.active_variant ?? 0) - 1,
                                )
                                .then(sync)
                                .catch((cause) => setError(errorText(cause)))
                            }
                          >
                            <ChevronLeft size={14} />
                          </Button>
                          {(message.active_variant ?? 0) + 1}/
                          {message.variants.length}
                          <Button
                            type="button"
                            disabled={
                              (message.active_variant ?? 0) >=
                                message.variants.length - 1 || busy
                            }
                            aria-label="下一候选"
                            onClick={() =>
                              void api
                                .switchSimpleChatVariant(
                                  chat.id,
                                  message.id,
                                  (message.active_variant ?? 0) + 1,
                                )
                                .then(sync)
                                .catch((cause) => setError(errorText(cause)))
                            }
                          >
                            <ChevronRight size={14} />
                          </Button>
                        </span>
                      )}
                  </div>
                </article>
              ))}
              {busy &&
                pendingId &&
                !chat.messages.some((item) => item.id === pendingId) && (
                  <article className="sc-message sc-assistant">
                    <div className="sc-message-name"><Avatar name={modelShortName(chat.model)} size={avatarSize} />助手</div>
                    <div className="sc-bubble">
                      {streamText || (
                        <span className="sc-generating">
                          正在生成<span className="sc-dots">···</span>
                        </span>
                      )}
                    </div>
                  </article>
                )}
              {busy && !pendingId && (
                <div className="sc-wait">正在连接模型…</div>
              )}
              <div className="sc-messages-end" aria-hidden="true" />
              </div>
            </div>
            {!followingTail && (
              <Button
                type="button"
                className="sc-jump-latest"
                onClick={() => jumpToLatest()}
                aria-label="滚动到最新消息"
              >
                <ArrowDown size={16} />
                最新消息
              </Button>
            )}
            <div className="sc-compose">
              <WritingPanel
                ref={inputRef}
                className="sc-writing"
                aria-label="聊天消息"
                value={input}
                onChange={(event) => setInput(event.currentTarget.value)}
                onInput={(event) => setInput(event.currentTarget.value)}
                {...inputEvents}
                enterKeyHint="send"
                placeholder="输入消息，Enter 发送，Shift + Enter 换行"
                rows={2}
              />
              {generationActive ? (
                <Button
                  type="button"
                  className="sc-stop"
                  disabled={stopPending}
                  onClick={() => void stopGeneration()}
                  title="断开本次上游流并保留已输出内容；上游已处理部分仍可能计费"
                  aria-label="停止生成并保留已输出内容"
                >
                  <Square size={15} fill="currentColor" />
                  {stopPending ? "正在停止…" : "停止生成"}
                </Button>
              ) : (
                <Button
                  type="button"
                  className="sc-primary"
                  variant="primary" skin="primary"
                  disabled={busy || !input.trim()}
                  onClick={() => void send()}
                >
                  <Send size={17} />
                  发送
                </Button>
              )}
            </div>
            </div>
            <aside className="sc-session" aria-label="会话信息">
              <div className="sc-session-head"><h2>会话</h2></div>
              <section><h3>用量与费用</h3>
                {!generations.length ? <p>暂无用量记录</p> : <>
                  <dl><dt>输入 token</dt><dd>{tokens(totalInput)}</dd><dt>输出 token</dt><dd>{tokens(totalOutput)}</dd>
                  <dt>缓存 token</dt><dd>{generations.some(item => item.usage.cached_tokens != null) ? tokens(generations.reduce((sum, item) => sum + (item.usage.cached_tokens ?? 0), 0)) : "未提供"}</dd></dl>
                  {generations.some(item => item.usage.usage_incomplete) && <p>用量不完整</p>}
                  {generations.some(item => item.usage.cost_usd != null) && <p>实际 · ${actualTotal.toFixed(6)}</p>}
                  {Object.entries(estimatedTotals).map(([currency, amount]) => <p key={currency}>估算 · {currency} {amount.toFixed(6)}</p>)}
                  {unpricedCount > 0 && <p>{unpricedCount} 次费用未知</p>}
                </>}
              </section>
              {wideSession && configContent}
            </aside>
          </div>
        )}
      </section>
      {editingId && editPreview && (
        <SimpleMessageEditDialog
          text={editText}
          preview={editPreview}
          busy={editBusy || busy}
          error={editError}
          onText={setEditText}
          onSave={() => void saveEdit()}
          onRefresh={() => void openEdit(editingId, true)}
          onClose={() => {
            setEditingId("");
            setEditPreview(null);
            setEditError("");
          }}
        />
      )}
    </div>
  );
}
