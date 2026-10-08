import { Checkbox } from "../design-system";
import { TextInput, Button } from "../design-system";
import { SettingsSelect } from "../components/SettingsSelect";
import { PageArt } from "../appearance/PageArt";
import { useQuery, useQueries } from "@tanstack/react-query";
import { resourceQueries } from "../features/resources/resourceQueries";
import { queryClient } from "../queryClient";
import { useEffect, useRef, useState } from "react";
import { CheckCircle2, KeyRound, RefreshCw, TestTube2 } from "lucide-react";
import { settingsClient as api } from "../features/settings/settingsClient";
import { GATEWAY_PRESETS, profileIdForGateway } from "../api/gateways";
import { ModelPicker } from "../components/ModelPicker";
import { useSettingsStore } from "../store/settingsStore";
import type { GatewayProfile, Settings } from "../types";

type ConnectionDraft = GatewayProfile & { apiKey: string };

function isOpenRouterModelId(value: string): boolean {
  const parts = value.trim().replace(/^~/, "").split("/");
  return (
    parts.length === 2 &&
    parts.every((part) => Boolean(part) && !/\s/.test(part))
  );
}

function gatewayLabel(gateway: string): string {
  try {
    return new URL(gateway).host;
  } catch {
    return gateway || "自定义接口";
  }
}

function defaultDraft(profileId: string): ConnectionDraft {
  const preset = GATEWAY_PRESETS.find((item) => item.id === profileId);
  return {
    gateway: preset?.gateway ?? "",
    model:
      profileId === "getgoapi"
        ? "gemini-3.5-flash"
        : profileId === "openrouter"
          ? "deepseek/deepseek-v4-flash"
          : "",
    auxiliary_model: "",
    model_provider: "",
    auxiliary_provider: "",
    provider_allow_fallbacks: true,
    apiKey: "",
  };
}

function savedDraft(settings: Settings, profileId: string): ConnectionDraft {
  const activeId = profileIdForGateway(settings.gateway);
  if (profileId === activeId) {
    return {
      gateway: settings.gateway,
      model: settings.model,
      auxiliary_model: settings.auxiliary_model,
      model_provider: settings.model_provider,
      auxiliary_provider: settings.auxiliary_provider,
      provider_allow_fallbacks: settings.provider_allow_fallbacks,
      apiKey: "",
    };
  }
  return {
    ...defaultDraft(profileId),
    ...settings.gateway_profiles?.[profileId],
    apiKey: "",
  };
}

export default function AIConnectionSettingsPanel() {
  const hydrated = useRef(false);
  const settings = useSettingsStore((state) => state.settings);
  const patch = useSettingsStore((state) => state.patch);
  const [selectedProfileId, setSelectedProfileId] = useState("openrouter");
  const [draft, setDraft] = useState<ConnectionDraft>(() =>
    defaultDraft("openrouter"),
  );
  const draftsRef = useRef<Record<string, ConnectionDraft>>({});
  const [mainCustom, setMainCustom] = useState(false);
  const [auxCustom, setAuxCustom] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [testResult, setTestResult] = useState<{
    model: string;
    latency_ms: number;
  } | null>(null);
  const [contextOverride, setContextOverride] = useState("");

  useEffect(() => {
    if (!settings || hydrated.current) return;
    hydrated.current = true;
    const id = profileIdForGateway(settings.gateway);
    setSelectedProfileId(id);
    setDraft(savedDraft(settings, id));
    draftsRef.current = {};
    setMainCustom(false);
    setAuxCustom(false);
    setContextOverride(
      settings.context_limit_override
        ? String(settings.context_limit_override)
        : "",
    );
  }, [
    settings?.gateway,
    settings?.model,
    settings?.auxiliary_model,
    settings?.model_provider,
    settings?.auxiliary_provider,
    settings?.provider_allow_fallbacks,
    settings?.context_limit_override,
  ]);

  const profileId = profileIdForGateway(draft.gateway);
  const isOpenRouter = profileId === "openrouter";
  const activeSavedId = settings ? profileIdForGateway(settings.gateway) : "";
  const hasSavedKey = Boolean(
    settings?.info.configured_providers?.includes(profileId),
  );
  const isDirty =
    !settings ||
    profileId !== activeSavedId ||
    draft.gateway !== settings.gateway ||
    draft.model !== settings.model ||
    draft.auxiliary_model !== settings.auxiliary_model ||
    draft.model_provider !== settings.model_provider ||
    draft.auxiliary_provider !== settings.auxiliary_provider ||
    draft.provider_allow_fallbacks !== settings.provider_allow_fallbacks ||
    Boolean(draft.apiKey.trim()) ||
    contextOverride !==
      (settings.context_limit_override
        ? String(settings.context_limit_override)
        : "");
  const auxiliaryLookupModel =
    draft.auxiliary_model.trim() || draft.model.trim();
  const catalogEnabled =
    !!settings &&
    !!profileId &&
    !(
      profileId.startsWith("custom:") &&
      profileId !== activeSavedId &&
      !settings.gateway_profiles?.[profileId]
    );
  const catalogQuery = useQuery({
    ...resourceQueries.models(profileId),
    enabled: catalogEnabled,
  });
  const catalog = {
    models: catalogEnabled ? (catalogQuery.data?.models ?? []) : [],
    loading: catalogQuery.isFetching,
    error:
      catalogQuery.error instanceof Error
        ? catalogQuery.error.message
        : !profileId
          ? "填写网关地址后可读取模型候选。"
          : !catalogEnabled
            ? "先保存自定义接口，再读取模型候选。"
            : "",
  };
  const [providerModels, setProviderModels] = useState<string[]>([]);
  useEffect(() => {
    const timer = window.setTimeout(
      () =>
        setProviderModels(
          isOpenRouter
            ? Array.from(
                new Set([draft.model.trim(), auxiliaryLookupModel]),
              ).filter(isOpenRouterModelId)
            : [],
        ),
      300,
    );
    return () => window.clearTimeout(timer);
  }, [isOpenRouter, draft.model, auxiliaryLookupModel]);
  const providerQueries = useQueries({
    queries: providerModels.map((model) => resourceQueries.providers(model)),
  });
  const providerLookups = Object.fromEntries(
    providerModels.map((model, index) => [
      model,
      {
        providers: providerQueries[index].data?.providers ?? [],
        loading: providerQueries[index].isFetching,
        error: providerQueries[index].error?.message ?? "",
      },
    ]),
  );
  const selectedModel = catalog.models.find(
    (item) => item.id === draft.model.trim(),
  );
  const selectedProvider = providerLookups[draft.model.trim()]?.providers.find(
    (item) => item.slug === draft.model_provider,
  );
  const catalogCapacity =
    selectedProvider?.context_length ?? selectedModel?.context_length ?? null;
  const declaredCapacity = Number(contextOverride);
  const hasDeclaredCapacity =
    contextOverride.trim() !== "" && Number.isFinite(declaredCapacity);
  const capacityValue = hasDeclaredCapacity
    ? declaredCapacity.toLocaleString()
    : catalogCapacity
      ? catalogCapacity.toLocaleString()
      : "未知";
  const capacityDetail = hasDeclaredCapacity
    ? "手动声明，未由上游验证"
    : catalogCapacity
      ? "目录显示。实际生成按当前上游端点能力规划"
      : "生成时尽量保留原文，上游可能拒绝过长请求。";

  useEffect(() => {
    if (!settings || !isOpenRouter || catalog.loading || !catalog.models.length)
      return;
    const savedModel = savedDraft(settings, "openrouter").model.trim();
    if (
      !savedModel ||
      isOpenRouterModelId(savedModel) ||
      draft.model.trim() !== savedModel
    )
      return;
    const matches = catalog.models.filter(
      (item) => item.id.split("/").slice(1).join("/") === savedModel,
    );
    if (matches.length !== 1) return;
    setDraft((current) =>
      current.model.trim() === savedModel
        ? {
            ...current,
            model: matches[0].id,
            auxiliary_model:
              current.auxiliary_model.trim() === savedModel
                ? ""
                : current.auxiliary_model,
          }
        : current,
    );
  }, [settings, isOpenRouter, catalog.loading, catalog.models, draft.model]);

  const customProfiles = Object.entries(
    settings?.gateway_profiles ?? {},
  ).filter(([id]) => id.startsWith("custom:"));
  const selectProfile = (id: string) => {
    if (!settings || id === selectedProfileId) return;
    draftsRef.current[selectedProfileId] = draft;
    setDraft(
      draftsRef.current[id] ??
        (id === "__custom__" ? defaultDraft(id) : savedDraft(settings, id)),
    );
    setSelectedProfileId(id);
    setMainCustom(false);
    setAuxCustom(false);
    setSaved(false);
    setError("");
    setTestResult(null);
    setContextOverride("");
  };
  const changeDraft = (patchValue: Partial<ConnectionDraft>) => {
    setDraft((current) => ({ ...current, ...patchValue }));
    setSaved(false);
    setTestResult(null);
  };
  const changeModel = (value: string, auxiliary: boolean) => {
    if (auxiliary) {
      if (value === draft.auxiliary_model) return;
      changeDraft({ auxiliary_model: value, auxiliary_provider: "" });
      setAuxCustom(false);
    } else {
      if (value === draft.model) return;
      setDraft((current) => ({
        ...current,
        model: value,
        model_provider: "",
        auxiliary_model:
          current.auxiliary_model === current.model
            ? ""
            : current.auxiliary_model,
        auxiliary_provider:
          current.auxiliary_model === current.model
            ? ""
            : current.auxiliary_provider,
      }));
      setSaved(false);
      setTestResult(null);
      setMainCustom(false);
      setContextOverride("");
      if (draft.auxiliary_model === draft.model) setAuxCustom(false);
    }
  };
  const providerStatus = (modelId: string) => {
    if (!isOpenRouterModelId(modelId)) return "先选择 OpenRouter 模型";
    const lookup = providerLookups[modelId];
    if (!lookup || lookup.loading) return "正在读取上游提供商…";
    if (lookup.error) return `读取失败；可手动填写代码。`;
    return lookup.providers.length
      ? `已找到 ${lookup.providers.length} 个上游提供商`
      : "暂无候选，可手动填写代码。";
  };
  const save = async () => {
    if (!settings || saving) return;
    const gateway = draft.gateway.trim().replace(/\/+$/, "");
    try {
      const parsed = new URL(gateway);
      if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname)
        throw new Error();
    } catch {
      setError("请输入有效的 http 或 https 网关地址");
      return;
    }
    const model = draft.model.trim();
    if (!model) {
      setError("请选择或填写主模型");
      return;
    }
    if (
      contextOverride.trim() &&
      (!Number.isInteger(Number(contextOverride)) ||
        Number(contextOverride) < 1024 ||
        Number(contextOverride) > 10000000)
    ) {
      setError("上下文容量请填写 1024 到 10000000 之间的整数");
      return;
    }
    if (
      isOpenRouter &&
      (!isOpenRouterModelId(model) ||
        (draft.auxiliary_model.trim() &&
          !isOpenRouterModelId(draft.auxiliary_model)))
    ) {
      setError(
        "OpenRouter 模型 ID 应为“厂商/模型名”；请从候选中选择或按此格式填写。",
      );
      return;
    }
    if (
      isOpenRouter &&
      ((mainCustom && !draft.model_provider.trim()) ||
        (auxCustom && !draft.auxiliary_provider.trim()))
    ) {
      setError("请填写自定义上游提供商代码");
      return;
    }
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      await patch({
        gateway,
        model,
        auxiliary_model: draft.auxiliary_model.trim(),
        model_provider: isOpenRouter ? draft.model_provider.trim() : "",
        auxiliary_provider: isOpenRouter ? draft.auxiliary_provider.trim() : "",
        provider_allow_fallbacks: draft.provider_allow_fallbacks,
        context_limit_override: contextOverride.trim()
          ? Number(contextOverride)
          : null,
        ...(draft.apiKey.trim() ? { api_key: draft.apiKey.trim() } : {}),
      });
      setDraft((current) => ({ ...current, gateway, apiKey: "" }));
      draftsRef.current = {};
      setSaved(true);
      setTestResult(null);
      void queryClient.invalidateQueries({
        queryKey: resourceQueries.models(profileId).queryKey,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };
  const clearKey = async () => {
    if (!settings || saving || isDirty) return;
    setSaving(true);
    setError("");
    try {
      await patch({ clear_api_key: true });
      setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "清除密钥失败");
    } finally {
      setSaving(false);
    }
  };
  const test = async () => {
    if (testing || saving || isDirty) return;
    setTesting(true);
    setError("");
    setTestResult(null);
    try {
      setTestResult(await api.testSettingsConnection());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "连接测试失败");
    } finally {
      setTesting(false);
    }
  };
  const renderProvider = (auxiliary: boolean) => {
    const modelId = auxiliary ? auxiliaryLookupModel : draft.model.trim();
    const providers = providerLookups[modelId]?.providers ?? [];
    const value = auxiliary ? draft.auxiliary_provider : draft.model_provider;
    const custom = auxiliary ? auxCustom : mainCustom;
    const setCustom = auxiliary ? setAuxCustom : setMainCustom;
    const field = auxiliary ? "auxiliary_provider" : "model_provider";
    const title = auxiliary ? "辅助模型供应商" : "主模型供应商";
    const updateProvider = (next: string) =>
      changeDraft(
        auxiliary ? { auxiliary_provider: next } : { model_provider: next },
      );
    return (
      <div className="v7-ai-provider">
        <label className="v7-field" htmlFor={`${field}-select`}>
          {title}
          <SettingsSelect
            id={`${field}-select`}
            value={custom ? "__custom__" : value}
            onValueChange={(event) => {
              if (event === "__custom__") setCustom(true);
              else {
                setCustom(false);
                updateProvider(event);
              }
            }}
          >
            <option value="">OpenRouter 自动选择</option>
            {value &&
              !custom &&
              !providers.some((item) => item.slug === value) && (
                <option value={value}>{value}（当前设置）</option>
              )}
            {providers.map((provider) => (
              <option key={provider.slug} value={provider.slug}>
                {provider.name} · {provider.slug}
              </option>
            ))}
            <option value="__custom__">手动填写代码…</option>
          </SettingsSelect>
        </label>
        {custom && (
          <label className="v7-field">
            提供商代码
            <TextInput
              value={value}
              onChange={(event) => updateProvider(event.target.value)}
              placeholder="例如 atlas-cloud"
              spellCheck={false}
            />
          </label>
        )}
        <p className="v7-provider-status">{providerStatus(modelId)}</p>
      </div>
    );
  };

  return (
    <>
      <div className="v7-settings-title">
        <span className="settings-section-emblem">
          <PageArt kind="connections" className="page-art--header" />
          <KeyRound size={22} />
        </span>
        <div>
          <h2>连接与模型</h2>
          <p>一个渠道保存一套密钥、模型和路由设置，切换时会恢复上次的选择。</p>
        </div>
      </div>
      <div className="v7-settings-card v7-ai-settings-card">
        <section className="v7-ai-section">
          <div className="v7-ai-section-head">
            <span>接入渠道</span>
            <small>先选择模型接口</small>
          </div>
          <div className="v7-ai-two-column">
            <label className="v7-field" htmlFor="ai-channel">
              API 渠道
              <SettingsSelect
                id="ai-channel"
                value={selectedProfileId}
                onValueChange={(event) => selectProfile(event)}
              >
                {GATEWAY_PRESETS.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
                {customProfiles.map(([id, profile]) => (
                  <option key={id} value={id}>
                    {gatewayLabel(profile.gateway)}
                  </option>
                ))}
                <option value="__custom__">新增自定义接口…</option>
              </SettingsSelect>
            </label>
            <label className="v7-field">
              网关地址
              <TextInput
                value={draft.gateway}
                onChange={(event) => {
                  const nextGateway = event.target.value;
                  const nextId = profileIdForGateway(nextGateway);
                  if (nextId && nextId !== selectedProfileId)
                    setSelectedProfileId(
                      nextId.startsWith("custom:") &&
                        !settings?.gateway_profiles?.[nextId]
                        ? "__custom__"
                        : nextId,
                    );
                  changeDraft({ gateway: nextGateway });
                }}
                placeholder="https://example.com/v1"
                spellCheck={false}
              />
            </label>
          </div>
          <div className="v7-ai-key-row">
            <label className="v7-field">
              API Key{" "}
              <small>
                {draft.apiKey.trim()
                  ? "新密钥待保存"
                  : hasSavedKey
                    ? "此渠道已保存密钥"
                    : "此渠道尚无密钥"}
              </small>
              <TextInput
                type="password"
                value={draft.apiKey}
                onChange={(event) =>
                  changeDraft({ apiKey: event.target.value })
                }
                placeholder={
                  hasSavedKey ? "留空沿用已保存密钥" : "输入此渠道的密钥"
                }
                autoComplete="new-password"
                spellCheck={false}
              />
            </label>
            {hasSavedKey && (
              <Button
                variant="secondary"
                type="button"
                className="v7-text-danger"
                disabled={saving || isDirty}
                onClick={() => void clearKey()}
              >
                清除此渠道密钥
              </Button>
            )}
          </div>
        </section>

        <section className="v7-ai-section">
          <div className="v7-ai-section-head">
            <span>{isOpenRouter ? "模型与上游路由" : "对话模型"}</span>
            <Button
              variant="secondary"
              type="button"
              className="v7-provider-refresh"
              disabled={!profileId || catalog.loading}
              onClick={() =>
                void queryClient.invalidateQueries({
                  queryKey: resourceQueries.models(profileId).queryKey,
                })
              }
            >
              <RefreshCw size={14} /> 刷新模型
            </Button>
          </div>
          <p className="v7-ai-hint">
            从候选中搜索并选择，也可以直接填写模型 ID。
          </p>
          <div className="v7-ai-model-rows">
            <div
              className={`v7-ai-model-row${isOpenRouter ? " has-provider" : ""}`}
            >
              <ModelPicker
                id="main-model"
                label="主模型 · 角色回复"
                value={draft.model}
                options={catalog.models}
                placeholder="搜索或填写主模型 ID"
                onChange={(value) => changeModel(value, false)}
              />
              {isOpenRouter && renderProvider(false)}
            </div>
            <div
              className={`v7-ai-model-row${isOpenRouter ? " has-provider" : ""}`}
            >
              <ModelPicker
                id="aux-model"
                label="辅助模型 · 导演与记忆"
                value={draft.auxiliary_model}
                options={catalog.models}
                placeholder="留空时沿用主模型"
                onChange={(value) => changeModel(value, true)}
              />
              {isOpenRouter && renderProvider(true)}
            </div>
          </div>
          <p className="v7-ai-catalog-status" role="status">
            {catalog.loading
              ? "正在读取当前渠道的模型…"
              : catalog.error
                ? `模型候选读取失败：${catalog.error}；仍可手动填写。`
                : `当前渠道提供 ${catalog.models.length} 个模型候选。`}
          </p>
          {profileId === "getgoapi" && (
            <p className="v7-ai-hint v7-ai-catalog-note">
              目录也包含图像和嵌入模型；用于故事对话时请选择聊天模型。
            </p>
          )}
          {isOpenRouter &&
            draft.model.trim() &&
            !isOpenRouterModelId(draft.model) && (
              <p className="v7-ai-warning">
                当前主模型 ID 不符合 OpenRouter
                的“厂商/模型名”格式。选择候选模型后才能保存。
              </p>
            )}
          {isOpenRouter && (
            <div className="v7-ai-route-card">
              <Checkbox
                className="v7-ai-route-toggle"
                checked={draft.provider_allow_fallbacks}
                onChange={(event) =>
                  changeDraft({
                    provider_allow_fallbacks: event.target.checked,
                  })
                }
                label={
                  <>
                    <span>
                      <strong>所选供应商不可用时允许切换</strong>
                      <small>
                        关闭后只使用指定供应商；该供应商不可用时生成会失败。
                      </small>
                    </span>
                  </>
                }
              />
              <Button
                variant="secondary"
                type="button"
                className="v7-provider-refresh"
                onClick={() =>
                  void queryClient.invalidateQueries({
                    queryKey: ["model-providers"],
                  })
                }
              >
                <RefreshCw size={14} /> 刷新供应商列表
              </Button>
            </div>
          )}
          <div className="v7-ai-spec" aria-label="主模型目录信息">
            <div className="v7-ai-spec-grid">
              <div className="v7-ai-spec-item">
                <span>上下文容量</span>
                <strong>{capacityValue}</strong>
                <small>
                  {capacityValue === "未知"
                    ? capacityDetail
                    : `tokens · ${capacityDetail}`}
                </small>
              </div>
              {selectedModel?.max_completion_tokens != null &&
                selectedModel.max_completion_tokens > 0 && (
                  <div className="v7-ai-spec-item">
                    <span>目录最大输出</span>
                    <strong>
                      {selectedModel.max_completion_tokens.toLocaleString()}
                    </strong>
                    <small>tokens</small>
                  </div>
                )}
              {selectedModel?.prompt_price_per_million != null && (
                <div className="v7-ai-spec-item">
                  <span>OpenRouter 目录价格</span>
                  <strong className="v7-ai-spec-price">
                    <span>
                      输入 ${selectedModel.prompt_price_per_million.toFixed(2)}
                    </span>
                    <span>
                      输出{" "}
                      {selectedModel.completion_price_per_million == null
                        ? "未知"
                        : `$${selectedModel.completion_price_per_million.toFixed(2)}`}
                    </span>
                  </strong>
                  <small>每百万 tokens。实际上游费用以账单为准。</small>
                </div>
              )}
            </div>
            {selectedModel?.reasoning?.mandatory && (
              <p className="v7-ai-spec-note" role="note">
                该模型必须开启推理。“关闭推理”时会自动采用该模型支持的最低推理档位；输出上限包含推理与正文。
              </p>
            )}
            <label className="v7-field v7-ai-capacity-field">
              手动填写上下文容量（可选）
              <TextInput
                type="number"
                min={1024}
                max={10000000}
                value={contextOverride}
                onChange={(event) => {
                  setContextOverride(event.target.value);
                  setSaved(false);
                }}
                placeholder="留空按模型/端点目录自动读取"
              />
            </label>
          </div>
        </section>

        <div className="v7-settings-actions v7-ai-actions">
          <Button
            variant="primary"
            type="button"
            disabled={
              saving ||
              !settings ||
              !draft.gateway.trim() ||
              !draft.model.trim()
            }
            onClick={() => void save()}
          >
            {saving ? "保存中…" : "保存此渠道配置"}
          </Button>
          <Button
            variant="secondary"
            type="button"
            disabled={saving || testing || isDirty || !hasSavedKey}
            onClick={() => void test()}
          >
            <TestTube2 size={16} /> {testing ? "测试中…" : "测试连接"}
          </Button>
          {isDirty && <small>保存后可测试当前渠道</small>}
        </div>
      </div>
      {saved && (
        <p className="v7-success" role="status">
          <CheckCircle2 size={16} /> 此渠道配置已保存
        </p>
      )}
      {testResult && (
        <p className="v7-success" role="status">
          <CheckCircle2 size={16} /> 连接成功 · {testResult.model} ·{" "}
          {(testResult.latency_ms / 1000).toFixed(1)} 秒
        </p>
      )}
      {error && (
        <p className="v7-error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}
