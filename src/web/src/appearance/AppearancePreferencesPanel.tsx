import { Radio } from "../design-system";
import { Avatar, Button, PageHeader, Portrait, Switch } from "../design-system";
import { SettingsSelect } from "../components/SettingsSelect";
import { useEffect, useRef, useState } from "react";
import { BubbleStylePicker } from "./BubbleStylePicker";
import { Check, Sparkles } from "lucide-react";
import type { AppearancePreferences } from "../types";
import {
  CLICK_EFFECT_OPTIONS,
  AVATAR_FRAME_OPTIONS,
  CURSOR_OPTIONS,
  DEFAULT_APPEARANCE,
  ORNAMENT_OPTIONS,
  getThemePack,
  resolveAppearanceOption,
  resolveOrnament,
  THEME_PACKS,
  TRAIL_OPTIONS,
  TYPOGRAPHY_OPTIONS,
} from "./registry";
import { useAppearanceStore } from "./store";
import { themeSampleStyle } from "./buttons/sampleStyle";
import "./appearance-settings.css";

export default function AppearancePreferencesPanel() {
  const preferences = useAppearanceStore((state) => state.preferences);
  const initialized = useAppearanceStore((state) => state.initialized);
  const initialize = useAppearanceStore((state) => state.initialize);
  const update = useAppearanceStore((state) => state.update);
  const saving = useAppearanceStore((state) => state.saving);
  const error = useAppearanceStore((state) => state.error);
  const [intensity, setIntensity] = useState(preferences.effect_intensity);
  const editingIntensity = useRef(false);

  useEffect(() => {
    void initialize().catch(() => undefined);
  }, [initialize]);
  useEffect(() => {
    if (!editingIntensity.current) setIntensity(preferences.effect_intensity);
  }, [preferences.effect_intensity]);

  const save = (value: Partial<AppearancePreferences>) => {
    void update(value).catch(() => undefined);
  };
  const commitIntensity = () => {
    editingIntensity.current = false;
    if (intensity !== preferences.effect_intensity)
      save({ effect_intensity: intensity });
  };
  const themeId = getThemePack(preferences.theme_id).id;
  const typographyId = resolveAppearanceOption(
    TYPOGRAPHY_OPTIONS,
    preferences.typography_id,
    DEFAULT_APPEARANCE.typography_id,
  ).id;
  const ornament = resolveOrnament(preferences.decoration_id);
  const cursorId = resolveAppearanceOption(
    CURSOR_OPTIONS,
    preferences.cursor_id,
    DEFAULT_APPEARANCE.cursor_id,
  ).id;
  const trailId = resolveAppearanceOption(
    TRAIL_OPTIONS,
    preferences.trail_id,
    DEFAULT_APPEARANCE.trail_id,
  ).id;
  const clickId = resolveAppearanceOption(
    CLICK_EFFECT_OPTIONS,
    preferences.click_effect_id,
    "none",
  ).id;
  const typography = resolveAppearanceOption(
    TYPOGRAPHY_OPTIONS,
    typographyId,
    "mincho",
  );

  return (
    <div className="appearance-preferences">
      <PageHeader
        headingLevel={2}
        title="界面外观"
        meta="把颜色、书卷字体与星光调成你的习惯，选择会保存到当前应用。"
        actions={
          <Button
            variant="primary"
            skin
            disabled={!initialized || saving}
            onClick={() =>
              save({
                theme_id:
                  getThemePack(preferences.theme_id).color_scheme === "dark"
                    ? "iris-night"
                    : "iris-light",
                visual_style_id: "picturebook",
                typography_id: "picturebook",
                decoration_id: "rich",
                bubble_style_id: "plain",
              })
            }
          >
            采用月光织锦
          </Button>
        }
      />
      <nav className="appearance-jumps" aria-label="外观设置分区">
        <a href="#appearance-styles">装饰与配色</a>
        <a href="#appearance-controls">字体与鼠标</a>
        <a href="#appearance-bubbles">密度与气泡</a>
        <a href="#appearance-artwork">头像与素材</a>
      </nav>
      <div className="picturebook-preset">
        <div>
          <strong>月光织锦</strong>
          <p>
            月白玻璃、鸢尾珐琅与织月窗框，配合文楷故事、光纹点击和轻盈转场。
          </p>
        </div>
      </div>
      <div className="v7-settings-card appearance-settings-card">
        <fieldset
          id="appearance-styles"
          className="appearance-density-fieldset"
          disabled={!initialized}
        >
          <legend>装饰强度</legend>
          <p className="appearance-help">
            决定导航、页头、对话框与主框的装饰多少；卡片、字段与按钮组始终保持安静。
          </p>
          <div className="appearance-density-options appearance-density-options--three">
            {ORNAMENT_OPTIONS.map((option) => (
              <Radio
                key={option.id}
                name="appearance-ornament"
                value={option.id}
                checked={ornament === option.id}
                onChange={() => save({ decoration_id: option.id })}
                label={
                  <>
                    <span>
                      <strong>{option.name}</strong>
                      <small>{option.note}</small>
                    </span>
                  </>
                }
              />
            ))}
          </div>
        </fieldset>
        <fieldset className="appearance-theme-fieldset" disabled={!initialized}>
          <legend>配色</legend>
          <p className="appearance-help">更换颜色时保留当前的装饰强度。</p>
          <div className="appearance-theme-grid">
            {THEME_PACKS.map((pack) => (
              <Button
                variant="secondary"
                key={pack.id}
                type="button"
                aria-pressed={themeId === pack.id}
                className={`appearance-theme-option${themeId === pack.id ? " is-selected" : ""}`}
                onClick={() => save({ theme_id: pack.id })}
              >
                <span
                  className="appearance-theme-sample"
                  style={themeSampleStyle(pack)}
                  aria-hidden="true"
                >
                  <span className="ui-btn ui-btn--primary">保存</span>
                </span>
                <span className="appearance-swatches" aria-hidden="true">
                  {pack.preview.map((color, index) => (
                    <i
                      key={`${pack.id}-${index}`}
                      style={{ backgroundColor: color }}
                    />
                  ))}
                </span>
                <span className="appearance-theme-name">
                  <strong>{pack.name}</strong>
                  {themeId === pack.id && (
                    <Check size={14} aria-hidden="true" />
                  )}
                </span>
                <span className="appearance-help">{pack.note}</span>
              </Button>
            ))}
          </div>
        </fieldset>
        <div className="appearance-control-grid" id="appearance-controls">
          <div className="v7-field">
            <label htmlFor="appearance-typography">书卷字体</label>
            <SettingsSelect
              id="appearance-typography"
              disabled={!initialized}
              value={typographyId}
              onValueChange={(event) => save({ typography_id: event })}
            >
              {TYPOGRAPHY_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))}
            </SettingsSelect>
            <p className="appearance-help">{typography.note}</p>
          </div>
          <div className="v7-field">
            <label htmlFor="appearance-cursor">指针样式</label>
            <SettingsSelect
              id="appearance-cursor"
              disabled={!initialized}
              value={cursorId}
              onValueChange={(event) => save({ cursor_id: event })}
            >
              {CURSOR_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))}
            </SettingsSelect>
          </div>
          <div className="v7-field">
            <label htmlFor="appearance-trail">指针拖尾</label>
            <SettingsSelect
              id="appearance-trail"
              disabled={!initialized}
              value={trailId}
              onValueChange={(event) => save({ trail_id: event })}
            >
              {TRAIL_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))}
            </SettingsSelect>
            <p className="appearance-help">
              {
                resolveAppearanceOption(TRAIL_OPTIONS, trailId, "iridescent")
                  .note
              }
            </p>
          </div>
          <div className="v7-field">
            <label htmlFor="appearance-click">点击特效</label>
            <SettingsSelect
              id="appearance-click"
              disabled={!initialized}
              value={clickId}
              onValueChange={(event) => save({ click_effect_id: event })}
            >
              {CLICK_EFFECT_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))}
            </SettingsSelect>
            <p className="appearance-help">
              {
                resolveAppearanceOption(CLICK_EFFECT_OPTIONS, clickId, "none")
                  .note
              }
            </p>
          </div>
        </div>
        <div className="appearance-font-specimen" aria-label="字体预览">
          <span>字间，有一个世界</span>
          <strong style={{ fontFamily: typography.display }}>
            风起时，故事便有了下一页。
          </strong>
          <p
            style={{
              fontFamily: typography.body,
              fontSize: preferences.reading_font_size,
              maxWidth:
                preferences.reading_width === "narrow"
                  ? "40ch"
                  : preferences.reading_width === "wide"
                    ? "72ch"
                    : "56ch",
            }}
          >
            夜色越过书窗，星光停在纸上。你提起笔，写下旅途的第一行。
          </p>
          <small>标题与正文预览 · 中文 Aa 0123456789</small>
        </div>
        <div className="appearance-control-grid">
          <div className="v7-field">
            <label htmlFor="appearance-reading-width">阅读宽度</label>
            <SettingsSelect
              id="appearance-reading-width"
              disabled={!initialized}
              value={preferences.reading_width}
              onValueChange={(value) =>
                save({ reading_width: value as "narrow" | "standard" | "wide" })
              }
            >
              <option value="narrow">窄</option>
              <option value="standard">标准</option>
              <option value="wide">宽</option>
            </SettingsSelect>
            <p className="appearance-help">
              限制正文行长，面板仍铺满可用空间。
            </p>
          </div>
          <div className="v7-field">
            <label htmlFor="appearance-reading-size">正文字号</label>
            <SettingsSelect
              id="appearance-reading-size"
              disabled={!initialized}
              value={String(preferences.reading_font_size)}
              onValueChange={(value) =>
                save({ reading_font_size: Number(value) })
              }
            >
              {Array.from({ length: 11 }, (_, index) => index + 14).map(
                (size) => (
                  <option key={size} value={String(size)}>
                    {size} px
                  </option>
                ),
              )}
            </SettingsSelect>
          </div>
        </div>
        <Button
          variant="secondary"
          type="button"
          className="appearance-pointer-playground"
          disabled={!initialized}
          aria-label="点击此处预览已选择的鼠标特效"
        >
          <Sparkles size={24} aria-hidden="true" />
          <strong>在这里移动、点击鼠标</strong>
          <span>
            {resolveAppearanceOption(TRAIL_OPTIONS, trailId, "iridescent").name}{" "}
            ·{" "}
            {
              resolveAppearanceOption(CLICK_EFFECT_OPTIONS, clickId, "none")
                .name
            }
          </span>
        </Button>
        <div className="v7-field appearance-intensity">
          <label htmlFor="appearance-intensity">
            特效强度{" "}
            <output htmlFor="appearance-intensity">
              {Math.round(intensity * 100)}%
            </output>
          </label>
          <input
            id="appearance-intensity"
            type="range"
            min="0"
            max="1"
            step="0.05"
            disabled={!initialized}
            value={intensity}
            onPointerDown={() => {
              editingIntensity.current = true;
            }}
            onKeyDown={() => {
              editingIntensity.current = true;
            }}
            onChange={(event) => setIntensity(Number(event.target.value))}
            onPointerUp={commitIntensity}
            onPointerCancel={commitIntensity}
            onKeyUp={commitIntensity}
            onBlur={commitIntensity}
            aria-describedby="appearance-motion-note"
          />
          <p className="appearance-help" id="appearance-motion-note">
            拖尾与点击特效独立选择；强度为 0
            或系统启用“减少动态效果”时，两者均关闭。离开窗口后自动清空。
          </p>
        </div>
        <fieldset
          className="appearance-density-fieldset"
          id="appearance-bubbles"
          disabled={!initialized}
        >
          <legend>界面密度</legend>
          <div className="appearance-density-options">
            <Radio
              name="appearance-density"
              value="comfortable"
              checked={preferences.density === "comfortable"}
              onChange={() => save({ density: "comfortable" })}
              label={
                <>
                  <span>
                    <strong>舒适</strong>
                    <small>宽松间距，适合长篇故事</small>
                  </span>
                </>
              }
            />
            <Radio
              name="appearance-density"
              value="compact"
              checked={preferences.density === "compact"}
              onChange={() => save({ density: "compact" })}
              label={
                <>
                  <span>
                    <strong>紧凑</strong>
                    <small>同屏查看更多内容</small>
                  </span>
                </>
              }
            />
          </div>
        </fieldset>
        <BubbleStylePicker
          value={preferences.bubble_style_id}
          disabled={!initialized}
          onChange={(id) => save({ bubble_style_id: id })}
        />
        <fieldset id="appearance-artwork" disabled={!initialized}>
          <legend>头像与公共素材</legend>
          <p className="appearance-help">
            消息头像使用所选框和尺寸；密集列表保留细环。素材加载失败时自动退回简洁外观。
          </p>
          <div
            className="appearance-avatar-options"
            role="group"
            aria-label="头像框风格"
          >
            {AVATAR_FRAME_OPTIONS.map((option) => (
              <Button
                key={option.id}
                variant="ghost"
                className="appearance-avatar-option"
                aria-pressed={preferences.avatar_frame_id === option.id}
                onClick={() => save({ avatar_frame_id: option.id })}
              >
                <Avatar name="织" size={40} frame={option.id} />
                <strong>{option.name}</strong>
              </Button>
            ))}
          </div>
          <div className="appearance-control-grid">
            <div className="v7-field">
              <label htmlFor="appearance-avatar-size">对话头像大小</label>
              <SettingsSelect
                id="appearance-avatar-size"
                value={String(preferences.dialogue_avatar_size)}
                onValueChange={(value) =>
                  save({ dialogue_avatar_size: Number(value) as 32 | 40 | 56 })
                }
              >
                <option value="32">小 · 32 px</option>
                <option value="40">中 · 40 px</option>
                <option value="56">大 · 56 px</option>
              </SettingsSelect>
            </div>
            <div className="v7-field">
              <label htmlFor="appearance-portrait-placeholder">无图画像</label>
              <SettingsSelect
                id="appearance-portrait-placeholder"
                value={preferences.portrait_placeholder}
                onValueChange={(value) =>
                  save({ portrait_placeholder: value as "art" | "initial" })
                }
              >
                <option value="art">公共美术底板</option>
                <option value="initial">首字渐变</option>
              </SettingsSelect>
              <p className="appearance-help">
                这是无图时的通用底板，不代表角色已有立绘。
              </p>
            </div>
          </div>
          <div className="appearance-art-switches">
            <Switch
              label="主按钮皮肤"
              checked={preferences.primary_button_skin}
              onChange={(e) => save({ primary_button_skin: e.target.checked })}
            />
            <Switch
              label="次要按钮皮肤"
              checked={preferences.secondary_button_skin}
              onChange={(e) =>
                save({ secondary_button_skin: e.target.checked })
              }
            />
            <Switch
              label="卡片名字与接缝花饰"
              checked={preferences.card_ornaments}
              onChange={(e) => save({ card_ornaments: e.target.checked })}
            />
            <Switch
              label="卡片边框"
              checked={preferences.card_border}
              onChange={(e) => save({ card_border: e.target.checked })}
            />
            <Switch
              label="页面背景美术"
              checked={preferences.background_art}
              onChange={(e) => save({ background_art: e.target.checked })}
            />
          </div>
          <p className="appearance-help">
            装饰素材仅在华丽档显示；按钮皮肤仅用于桌面页头的指定操作。头像框与无图底板不受装饰档位影响。
          </p>
          <div className="appearance-live-preview" aria-label="当前外观预览">
            <Portrait name="织界" className="appearance-portrait-preview" />
            <div>
              <Avatar
                name="织"
                size={preferences.dialogue_avatar_size}
                frame={preferences.avatar_frame_id}
              />
              <strong>当前消息头像与无图画像</strong>
              <p className="appearance-help">预览与页面共用真实外观偏好。</p>
            </div>
          </div>
        </fieldset>
        <div className="appearance-save-state" role="status" aria-live="polite">
          {saving
            ? "正在保存外观…"
            : initialized
              ? "外观会自动保存"
              : "正在读取已保存的外观…"}
        </div>
        {error && (
          <div className="appearance-error" role="alert">
            <span>{error}</span>
            {!initialized && (
              <Button
                variant="secondary"

                type="button"
                onClick={() => void initialize().catch(() => undefined)}
              >
                重新加载
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
