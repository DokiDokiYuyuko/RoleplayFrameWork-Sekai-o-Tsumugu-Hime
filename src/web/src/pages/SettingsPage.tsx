import { Button, PageHeader, TextInput, Textarea } from "../design-system";
import { SettingsSelect } from "../components/SettingsSelect";
import { createClientId } from "../utils/clientId.js";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import {
  CheckCircle2,
  AudioLines,
  KeyRound,
  Plus,
  Settings2,
  Smartphone,
  Shield,
  Sun,
  Trash2,
} from "lucide-react";
import { useSettingsStore } from "../store/settingsStore";
import PersonalLibraryPanel from "./PersonalLibraryPanel";
import AIConnectionSettingsPanel from "./AIConnectionSettingsPanel";
import { GenerationControls } from "../components/GenerationControls";
import TTSSettingsPanel from "./TTSSettingsPanel";
import PromptPresetSettingsPanel from "./PromptPresetSettingsPanel";
import ResponseStyleSettingsPanel from "./ResponseStyleSettingsPanel";
import LanAccessPanel from "./LanAccessPanel";
import AppearancePreferencesPanel from "../appearance/AppearancePreferencesPanel";
import { Ornament } from "../appearance/Ornament";
import "./settings-production.css";
import { LoadState } from "../components/LoadState";
import type {
  BreakArmorPromptPreset,
  GenerationSettings,
  SettingsPatch,
} from "../types";

const sections = [
  {
    id: "personal-library",
    label: "个人库恢复",
    desc: "备份范围与恢复校验",
    icon: Shield,
  },
  {
    id: "response-styles",
    label: "回应风格",
    desc: "情感表达与回应偏好",
    icon: Settings2,
  },
  {
    id: "connection",
    label: "连接与模型",
    desc: "渠道、密钥与模型",
    icon: KeyRound,
  },
  {
    id: "prompt",
    label: "破甲 Prompt",
    desc: "破甲词库与注入方式",
    icon: Shield,
  },
  {
    id: "prompt-presets",
    label: "提示词方案",
    desc: "片段编排与外部导入",
    icon: Shield,
  },
  {
    id: "voice",
    label: "本地语音",
    desc: "Fish Speech 与音色库",
    icon: AudioLines,
  },
  { id: "preferences", label: "界面外观", desc: "主题、字体与星光", icon: Sun },
  {
    id: "phone",
    label: "手机访问",
    desc: "局域网配对与连接",
    icon: Smartphone,
  },
  {
    id: "advanced",
    label: "高级选项",
    desc: "引擎、思考、审查与记忆整理",
    icon: Settings2,
  },
];

const sectionHelp: Record<
  string,
  { title: string; text: string; note: string }
> = {
  "personal-library": {
    title: "恢复到独立目录",
    text: "网页校验备份清单和文件。完整创作库的备份与恢复由离线维护工具执行。",
    note: "恢复目标必须没有已有内容；故事分享包与私密库备份的范围不同。",
  },
  "response-styles": {
    title: "回应偏好",
    text: "风格正文会影响情感态度与表达习惯。保存到风格库后，可以在故事中选择。",
    note: "编辑中的草稿不会因列表刷新而自动保存。",
  },
  connection: {
    title: "先保存，再测试",
    text: "每个渠道分别保存网关、模型与密钥。留空密钥会沿用该渠道已保存的密钥。",
    note: "测试连接需要已保存配置；模型供应商与网关渠道是不同的选择。",
  },
  prompt: {
    title: "注入时机",
    text: "仅故事开头注入一次，或按玩家有效对话轮次周期注入。同一轮多角色回复共用轮次。",
    note: "指令只加入当次请求，不会成为聊天消息；留空内容关闭注入。",
  },
  "prompt-presets": {
    title: "片段顺序与预览",
    text: "方案按片段的插入位置与顺序编排指令，正则替换在发送前执行。",
    note: "保存方案与设为当前方案是两个独立操作。导入报告保留兼容性说明。",
  },
  voice: {
    title: "本地音色",
    text: "参考音频与对应原文一起用于还原音色。选择默认音色后，故事可以使用本地语音。",
    note: "音频只保存到应用数据目录。服务状态、加载失败与重试结果以左侧反馈为准。",
  },
  preferences: {
    title: "实时预览与自动保存",
    text: "配色、字体、头像、气泡与鼠标特效使用同一套应用外观偏好。每次修改都会自动保存。",
    note: "保存失败会显示错误并恢复确认过的设置；装饰三档与系统减少动态效果继续生效。",
  },
  phone: {
    title: "局域网访问",
    text: "手机与电脑需要处在同一网络。局域网访问保留配对、设备会话、证书与关闭入口。",
    note: "配对码和访问地址使用当前服务实际状态；敏感管理操作仅在本机可用。",
  },
  advanced: {
    title: "引擎与生成参数",
    text: "引擎、思考、回复审查与记忆整理控制模型工作方式。角色卡明确指定的采样参数优先。",
    note: "回复审查会额外调用辅助模型；自动快照保留规则不会清理回收区恢复点。",
  },
};

export default function SettingsPage() {
  const { section = "connection" } = useParams();
  const current =
    section === "models" || !sections.some((item) => item.id === section)
      ? "connection"
      : section;
  const hydrated = useRef(false);
  const settings = useSettingsStore((s) => s.settings);
  const load = useSettingsStore((s) => s.load);
  const patch = useSettingsStore((s) => s.patch);
  const [breakArmorPrompts, setBreakArmorPrompts] = useState<
    BreakArmorPromptPreset[]
  >([]);
  const [activeBreakArmorPromptId, setActiveBreakArmorPromptId] = useState<
    string | null
  >(null);
  const [breakArmorMode, setBreakArmorMode] = useState<"opening" | "interval">(
    "opening",
  );
  const [breakArmorInterval, setBreakArmorInterval] = useState("10");
  const [generation, setGeneration] = useState<GenerationSettings>({
    temperature: null,
    top_p: null,
    frequency_penalty: null,
    presence_penalty: null,
    max_output_tokens: null,
  });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");
  const [readRetry, setReadRetry] = useState(0);
  useEffect(() => {
    setSaved(false);
    setError("");
  }, [current]);
  useEffect(() => {
    if (settings) return;
    let live = true;
    setReadError("");
    void load().catch((cause) => {
      if (live)
        setReadError(cause instanceof Error ? cause.message : "设置读取失败");
    });
    return () => {
      live = false;
    };
  }, [settings, load, readRetry]);
  useEffect(() => {
    if (settings && !hydrated.current) {
      hydrated.current = true;
      setGeneration(settings.generation);
      const prompts = settings.break_armor_prompts ?? [];
      const compatiblePrompts =
        prompts.length || !settings.break_armor_prompt?.trim()
          ? prompts
          : [
              {
                id: "default",
                name: "默认破甲词",
                content: settings.break_armor_prompt,
              },
            ];
      setBreakArmorPrompts(compatiblePrompts);
      const requestedActiveId = settings.active_break_armor_prompt_id;
      setActiveBreakArmorPromptId(
        requestedActiveId &&
          compatiblePrompts.some((item) => item.id === requestedActiveId)
          ? requestedActiveId
          : requestedActiveId === null
            ? null
            : (compatiblePrompts[0]?.id ?? null),
      );
      setBreakArmorMode(settings.break_armor_mode ?? "opening");
      setBreakArmorInterval(String(settings.break_armor_interval ?? 10));
    }
  }, [
    settings?.break_armor_prompts,
    settings?.active_break_armor_prompt_id,
    settings?.break_armor_prompt,
    settings?.break_armor_mode,
    settings?.break_armor_interval,
    settings?.generation,
  ]);
  const activeBreakArmorPrompt =
    breakArmorPrompts.find(
      (prompt) => prompt.id === activeBreakArmorPromptId,
    ) ?? null;
  const apply = async (value: SettingsPatch) => {
    if (saving) return false;
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      await patch(value);
      setSaved(true);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败");
      return false;
    } finally {
      setSaving(false);
    }
  };
  const saveBreakArmorPrompt = async () => {
    const prompts = breakArmorPrompts.map((prompt) => ({
      ...prompt,
      name: prompt.name.trim(),
    }));
    if (prompts.some((prompt) => !prompt.name)) {
      setError("请为每个破甲词填写名称");
      return;
    }
    const normalizedNames = prompts.map((prompt) =>
      prompt.name.toLocaleLowerCase(),
    );
    if (new Set(normalizedNames).size !== normalizedNames.length) {
      setError("破甲词名称不能重复");
      return;
    }
    const value: SettingsPatch = {
      break_armor_prompts: prompts,
      active_break_armor_prompt_id: activeBreakArmorPromptId,
      break_armor_mode: breakArmorMode,
    };
    if (breakArmorMode === "interval") {
      const interval = Number.parseInt(breakArmorInterval, 10);
      if (!Number.isInteger(interval) || interval < 1 || interval > 1000) {
        setError("对话轮次请填写 1 到 1000 之间的整数");
        return;
      }
      value.break_armor_interval = interval;
    }
    await apply(value);
  };
  const addBreakArmorPrompt = () => {
    const baseName = "新建破甲词";
    let name = baseName;
    let suffix = 2;
    const names = new Set(
      breakArmorPrompts.map((prompt) => prompt.name.toLocaleLowerCase()),
    );
    while (names.has(name.toLocaleLowerCase()))
      name = `${baseName} ${suffix++}`;
    const id = createClientId();
    setBreakArmorPrompts((current) => [...current, { id, name, content: "" }]);
    setActiveBreakArmorPromptId(id);
    setSaved(false);
  };
  const deleteBreakArmorPrompt = () => {
    if (!activeBreakArmorPrompt) return;
    if (
      !window.confirm(
        `删除破甲词「${activeBreakArmorPrompt.name}」？保存后才会生效。`,
      )
    )
      return;
    const remaining = breakArmorPrompts.filter(
      (prompt) => prompt.id !== activeBreakArmorPrompt.id,
    );
    setBreakArmorPrompts(remaining);
    setActiveBreakArmorPromptId(remaining[0]?.id ?? null);
    setSaved(false);
  };
  return (
    <main className="v7-settings production-settings" data-region="settings" data-saving={saving}>
      <PageHeader
        className="production-settings-header"
        title="设置"
        meta="连接模型，选择阅读方式，再回到故事里。"
      />
      <div className="v7-settings-layout">
        <nav className="v7-settings-nav" aria-label="设置分类">
          {sections.map(({ id, label, desc, icon: Icon }) => (
            <Link
              key={id}
              to={`/settings/${id}`}
              className={current === id ? "is-active" : ""}
              aria-current={current === id ? "page" : undefined}
            >
              <Icon size={18} />
              <span>
                <strong>{label}</strong>
                <small>{desc}</small>
              </span>
            </Link>
          ))}
        </nav>
        <div className="production-settings-panel moonweave-frame">
          <Ornament variant="main" crest tone="iris" />
          <section
            className="v7-settings-content"
            aria-label={sections.find((item) => item.id === current)?.label}
          >
            {!settings ? (
              <LoadState
                title="读取设置"
                error={readError || undefined}
                onRetry={() => setReadRetry((value) => value + 1)}
              />
            ) : (
              <>
                {current === "personal-library" && <PersonalLibraryPanel />}
                {current === "connection" && <AIConnectionSettingsPanel />}
                {current === "prompt-presets" && <PromptPresetSettingsPanel />}
                {current === "response-styles" && (
                  <ResponseStyleSettingsPanel />
                )}
                {current === "prompt" && (
                  <>
                    <div className="v7-settings-title">
                      <Shield size={22} />
                      <div>
                        <h2>破甲 Prompt</h2>
                        <p>选择只在开场注入，或按对话轮次周期注入。</p>
                      </div>
                    </div>
                    <div className="v7-settings-card">
                      <div className="v7-field">
                        <label htmlFor="break-armor-preset">
                          当前使用的破甲词
                        </label>
                        <div className="v7-break-armor-presets-row">
                          <SettingsSelect
                            id="break-armor-preset"
                            value={activeBreakArmorPromptId ?? ""}
                            onValueChange={(event) => {
                              setActiveBreakArmorPromptId(event || null);
                              setSaved(false);
                            }}
                          >
                            <option value="">不选择（关闭破甲词）</option>
                            {breakArmorPrompts.map((prompt) => (
                              <option key={prompt.id} value={prompt.id}>
                                {prompt.name}
                              </option>
                            ))}
                          </SettingsSelect>
                          <Button
                            variant="secondary"
                            type="button"
                            onClick={addBreakArmorPrompt}
                          >
                            <Plus size={16} /> 新建
                          </Button>
                          <Button
                            variant="secondary"

                            type="button"
                            disabled={!activeBreakArmorPrompt}
                            onClick={deleteBreakArmorPrompt}
                          >
                            <Trash2 size={16} /> 删除
                          </Button>
                        </div>
                        <small>
                          可以保存多条带名称的破甲词，选择其中一条作为当前使用内容。
                        </small>
                      </div>
                      {activeBreakArmorPrompt ? (
                        <label
                          className="v7-field"
                          htmlFor="break-armor-preset-name"
                        >
                          破甲词名称
                          <TextInput
                            id="break-armor-preset-name"
                            value={activeBreakArmorPrompt.name}
                            maxLength={80}
                            onChange={(event) => {
                              const name = event.target.value;
                              setBreakArmorPrompts((current) =>
                                current.map((prompt) =>
                                  prompt.id === activeBreakArmorPrompt.id
                                    ? { ...prompt, name }
                                    : prompt,
                                ),
                              );
                              setSaved(false);
                            }}
                            placeholder="例如：沉浸式叙事、角色边界"
                          />
                        </label>
                      ) : (
                        <p className="v7-muted">
                          还没有选中的破甲词。新建一条，或选择已有内容开始编辑。
                        </p>
                      )}
                      <label className="v7-field" htmlFor="break-armor-prompt">
                        破甲词内容
                        <Textarea
                          id="break-armor-prompt"
                          value={activeBreakArmorPrompt?.content ?? ""}
                          disabled={!activeBreakArmorPrompt}
                          onChange={(event) => {
                            const content = event.target.value;
                            if (!activeBreakArmorPrompt) return;
                            setBreakArmorPrompts((current) =>
                              current.map((prompt) =>
                                prompt.id === activeBreakArmorPrompt.id
                                  ? { ...prompt, content }
                                  : prompt,
                              ),
                            );
                            setSaved(false);
                          }}
                          rows={12}
                          maxLength={4000}
                          placeholder="填写这条破甲词的内容……"
                          spellCheck={false}
                        />
                        <small>
                          {(
                            activeBreakArmorPrompt?.content.length ?? 0
                          ).toLocaleString()}{" "}
                          / 4,000 字
                        </small>
                      </label>
                      <div className="v7-field">
                        <span>注入方式</span>
                        <div
                          className="v7-option-grid"
                          role="group"
                          aria-label="破甲 Prompt 注入方式"
                        >
                          <Button
                            variant="secondary"
                            type="button"
                            className={
                              breakArmorMode === "opening" ? "is-selected" : ""
                            }
                            aria-pressed={breakArmorMode === "opening"}
                            onClick={() => {
                              setBreakArmorMode("opening");
                              setSaved(false);
                            }}
                          >
                            <strong>仅故事开头</strong>
                            <span>只在故事首次生成回复时注入</span>
                          </Button>
                          <Button
                            variant="secondary"
                            type="button"
                            className={
                              breakArmorMode === "interval" ? "is-selected" : ""
                            }
                            aria-pressed={breakArmorMode === "interval"}
                            onClick={() => {
                              setBreakArmorMode("interval");
                              setSaved(false);
                            }}
                          >
                            <strong>按对话轮次注入</strong>
                            <span>按设定的轮次间隔重复注入</span>
                          </Button>
                        </div>
                      </div>
                      {breakArmorMode === "interval" && (
                        <label
                          className="v7-field"
                          htmlFor="break-armor-interval"
                        >
                          对话轮次间隔
                          <TextInput
                            id="break-armor-interval"
                            type="number"
                            min={1}
                            max={1000}
                            step={1}
                            value={breakArmorInterval}
                            onChange={(event) => {
                              setBreakArmorInterval(event.target.value);
                              setSaved(false);
                            }}
                          />
                          <small>
                            填 1 表示每个对话轮次都注入；填 2 表示第
                            2、4、6……轮注入。
                          </small>
                        </label>
                      )}
                      <p className="v7-muted">
                        {breakArmorMode === "opening"
                          ? "仅在故事开场的首次模型回复中注入一次。"
                          : "按玩家发送的有效对话轮次计数；多个角色在同一轮并行回复时共用该轮次。"}
                        指令只加入当次模型请求，不会显示成聊天消息；Prompt
                        留空时功能关闭。
                      </p>
                      <div className="v7-settings-actions">
                        <Button
                          variant="primary"

                          disabled={saving || !settings}
                          onClick={() => void saveBreakArmorPrompt()}
                        >
                          {saving ? "保存中…" : "保存 Prompt"}
                        </Button>
                      </div>
                    </div>
                  </>
                )}
                {current === "preferences" && <AppearancePreferencesPanel />}
                {current === "voice" && <TTSSettingsPanel />}
                {current === "phone" && <LanAccessPanel />}
                {current === "advanced" && (
                  <>
                    <div className="v7-settings-title">
                      <Settings2 size={22} />
                      <div>
                        <h2>高级选项</h2>
                        <p>引擎、思考、审查和记忆整理。</p>
                      </div>
                    </div>
                    <div className="v7-settings-card">
                      <h3>推理引擎</h3>
                      <div className="v7-option-grid" role="group" aria-label="推理引擎">
                        <Button
                          variant="secondary"
                          aria-pressed={settings?.engine === "openrouter"}
                          disabled={saving}
                          onClick={() => void apply({ engine: "openrouter" })}
                        >
                          <strong>兼容接口直连</strong>
                          <span>
                            OpenRouter、GetGoAPI 或自定义 OpenAI 兼容网关
                          </span>
                        </Button>
                        <Button
                          variant="secondary"
                          aria-pressed={settings?.engine === "dsh"}
                          disabled={saving}
                          onClick={() => void apply({ engine: "dsh" })}
                        >
                          <strong>DSH</strong>
                          <span>每个角色独立运行</span>
                        </Button>
                      </div>
                      <h3>模型思考</h3>
                      <div className="v7-option-grid" role="group" aria-label="模型思考">
                        <Button
                          variant="secondary"
                          aria-pressed={settings?.thinking !== "off"}
                          disabled={saving}
                          onClick={() => void apply({ thinking: "on" })}
                        >
                          <strong>开启</strong>
                          <span>较充分的推理过程</span>
                        </Button>
                        <Button
                          variant="secondary"
                          aria-pressed={settings?.thinking === "off"}
                          data-unavailable={settings?.engine === "dsh" || undefined}
                          disabled={saving || settings?.engine === "dsh"}
                          onClick={() => void apply({ thinking: "off" })}
                        >
                          <strong>关闭</strong>
                          <span>
                            {settings?.engine === "dsh"
                              ? "DSH 引擎不支持关闭"
                              : "优先响应速度"}
                          </span>
                        </Button>
                      </div>
                      <h3>回复审查</h3>
                      <p className="v7-muted">
                        检查角色是否抢戏、读心或出戏。开启后每条回复会额外调用一次辅助模型；故事内也可以单独关闭。
                      </p>
                      <div
                        className="v7-option-grid"
                        role="group"
                        aria-label="回复审查"
                      >
                        <Button
                          variant="secondary"
                          type="button"
                          className={
                            !settings?.hygiene_enabled ? "is-selected" : ""
                          }
                          aria-pressed={!settings?.hygiene_enabled}
                          disabled={saving}
                          onClick={() => void apply({ hygiene_enabled: false })}
                        >
                          <strong>关闭 · 默认</strong>
                          <span>生成完成后直接显示回复</span>
                        </Button>
                        <Button
                          variant="secondary"
                          type="button"
                          className={
                            settings?.hygiene_enabled ? "is-selected" : ""
                          }
                          aria-pressed={Boolean(settings?.hygiene_enabled)}
                          disabled={saving}
                          onClick={() => void apply({ hygiene_enabled: true })}
                        >
                          <strong>开启</strong>
                          <span>审查回复，发现违规时尝试重写</span>
                        </Button>
                      </div>
                      <h3>记忆整理</h3>
                      <p className="v7-muted">
                        用辅助模型把对话整理成以后能召回的记忆。关闭后，回合结束不会自动整理，角色记忆里的手动整理也会停。已经记下的记忆仍会在回复时召回。角色对白仍用主模型。
                      </p>
                      <div
                        className="v7-option-grid"
                        role="group"
                        aria-label="记忆整理"
                      >
                        <Button
                          variant="secondary"
                          type="button"
                          className={
                            !settings?.memory_consolidation_enabled
                              ? "is-selected"
                              : ""
                          }
                          aria-pressed={!settings?.memory_consolidation_enabled}
                          disabled={saving}
                          onClick={() =>
                            void apply({ memory_consolidation_enabled: false })
                          }
                        >
                          <strong>关闭 · 默认</strong>
                          <span>不调用辅助模型整理记忆</span>
                        </Button>
                        <Button
                          variant="secondary"
                          type="button"
                          className={
                            settings?.memory_consolidation_enabled
                              ? "is-selected"
                              : ""
                          }
                          aria-pressed={Boolean(
                            settings?.memory_consolidation_enabled,
                          )}
                          disabled={saving}
                          onClick={() =>
                            void apply({ memory_consolidation_enabled: true })
                          }
                        >
                          <strong>开启</strong>
                          <span>
                            每次只整理一小段新对话，失败的旧窗口不会每回合重试
                          </span>
                        </Button>
                      </div>
                      <h3>故事生成参数</h3>
                      <p className="v7-muted">
                        留空时沿用模型默认值。角色卡中明确指定的采样参数优先；故事会话的输出上限仍可单独覆盖。
                      </p>
                      <GenerationControls
                        inputComponent={TextInput}
                        value={generation}
                        onChange={(value) => {
                          setGeneration(value);
                          setSaved(false);
                        }}
                        idPrefix="settings-generation"
                        disabled={saving}
                      />
                      <Button
                        variant="primary"
                        type="button"
                        disabled={saving || !settings}
                        onClick={() => void apply({ generation })}
                      >
                        保存生成参数
                      </Button>
                      <h3>故事快照保留</h3>
                      <p className="v7-muted">
                        保存后的自动快照按天数、份数和总空间清理；回收区恢复点始终保留，直到你另行处理。
                      </p>
                      {settings && (
                        <div className="v7-ai-two-column">
                          <label className="v7-field">
                            保留天数
                            <TextInput
                              key={`days-${settings.backup_retention_days}`}
                              type="number"
                              min={1}
                              max={3650}
                              defaultValue={settings.backup_retention_days}
                              onBlur={(e) => {
                                const value = Number(e.target.value);
                                if (
                                  Number.isInteger(value) &&
                                  value >= 1 &&
                                  value <= 3650 &&
                                  value !== settings.backup_retention_days
                                )
                                  void apply({ backup_retention_days: value });
                              }}
                            />
                          </label>
                          <label className="v7-field">
                            每个故事最多份数
                            <TextInput
                              key={`count-${settings.backup_keep_count}`}
                              type="number"
                              min={1}
                              max={500}
                              defaultValue={settings.backup_keep_count}
                              onBlur={(e) => {
                                const value = Number(e.target.value);
                                if (
                                  Number.isInteger(value) &&
                                  value >= 1 &&
                                  value <= 500 &&
                                  value !== settings.backup_keep_count
                                )
                                  void apply({ backup_keep_count: value });
                              }}
                            />
                          </label>
                          <label className="v7-field">
                            自动快照空间上限（GB）
                            <TextInput
                              key={`space-${settings.backup_max_bytes}`}
                              type="number"
                              min={0.1}
                              step={0.1}
                              defaultValue={
                                settings.backup_max_bytes / 1_000_000_000
                              }
                              onBlur={(e) => {
                                const value = Math.round(
                                  Number(e.target.value) * 1_000_000_000,
                                );
                                if (
                                  Number.isFinite(value) &&
                                  value >= 100_000_000 &&
                                  value !== settings.backup_max_bytes
                                )
                                  void apply({ backup_max_bytes: value });
                              }}
                            />
                          </label>
                        </div>
                      )}
                      <div className="v7-diagnostic">
                        <span>当前模式</span>
                        <strong>
                          {settings?.info.fake_mode ? "测试模式" : "真实模型"}
                        </strong>
                      </div>
                    </div>
                  </>
                )}
                {saved && (
                  <p className="v7-success" role="status">
                    <CheckCircle2 size={16} /> 设置已保存
                  </p>
                )}
                {error && (
                  <p className="v7-error" role="alert">
                    {error}
                  </p>
                )}
              </>
            )}
          </section>
          <aside className="production-settings-help" aria-label="设置说明">
            <span className="v7-eyebrow">使用说明</span>
            <h3>{sectionHelp[current].title}</h3>
            <p>{sectionHelp[current].text}</p>
            <small>{sectionHelp[current].note}</small>
          </aside>
        </div>
      </div>
    </main>
  );
}
