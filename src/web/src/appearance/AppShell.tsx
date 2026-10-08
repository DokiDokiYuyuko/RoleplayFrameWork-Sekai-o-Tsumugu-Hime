import type { ComponentType, ReactNode } from "react";
import { useRef } from "react";
import { Link, useLocation } from "react-router";
import { BookOpen, Clapperboard, Globe2, LibraryBig, Menu, MessageCircle, Moon, PenTool, Settings2, Sun, Upload, Users, X, type IconProps } from "../design-system/Icon";
import { IconButton, NavItem, PublicArt } from "../design-system";
import { useMediaQuery } from "../design-system/useMediaQuery";
import { useNavDrawer } from "../design-system/layouts/useNavDrawer";
import "../design-system/layouts/shell.css";
import { useAppearanceStore } from "./store";
import { getThemePack } from "./registry";
import { useMoonweaveMotion } from "./useMoonweaveMotion";

interface Destination {
  to: string;
  label: string;
  icon: ComponentType<IconProps>;
  /** Active-state rule; defaults to a path prefix match. */
  current?: (path: string) => boolean;
}

const groups: { label?: string; name: string; items: Destination[] }[] = [
  {
    name: "故事与聊天",
    items: [
      { to: "/", label: "故事", icon: BookOpen, current: (path) => path === "/" || path.startsWith("/stories") },
      { to: "/simple-chats", label: "聊天", icon: MessageCircle },
    ],
  },
  {
    label: "素材",
    name: "素材",
    items: [
      { to: "/library/characters", label: "角色", icon: Users },
      { to: "/worlds", label: "世界", icon: Globe2 },
      { to: "/library/lorebooks", label: "世界书", icon: LibraryBig },
      { to: "/library/scenarios", label: "场景", icon: Clapperboard },
    ],
  },
  {
    label: "创作",
    name: "创作",
    items: [
      { to: "/create", label: "创作工坊", icon: PenTool },
      { to: "/library/imports", label: "导入素材", icon: Upload },
    ],
  },
];
const settings: Destination = { to: "/settings", label: "设置", icon: Settings2 };
const crest = `${import.meta.env.BASE_URL}brand/moonweave/v1/moonweave-crest.webp`;
const RAIL = "(min-width: 900px) and (max-width: 1199px)";

export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const preferences = useAppearanceStore((state) => state.preferences);
  const updateAppearance = useAppearanceStore((state) => state.update);
  const appearanceSaving = useAppearanceStore((state) => state.saving);
  const appearanceError = useAppearanceStore((state) => state.error);
  const dark = getThemePack(preferences.theme_id).color_scheme === "dark";
  const editor = /^\/library\/characters\/[^/]+$/.test(pathname);
  const workbench = editor || pathname.startsWith("/create") || /^\/library\/(?:lorebooks|scenarios|imports)(?:\/|$)/.test(pathname) || /^\/worlds\/[^/]+\/lorebooks(?:\/|$)/.test(pathname);
  const conversation = /^\/stories\/[^/]+\/branches\//.test(pathname) || pathname.startsWith("/simple-chats");
  const drawer = useNavDrawer(pathname);
  const rail = useMediaQuery(RAIL);
  const route = useRef<HTMLDivElement>(null);
  useMoonweaveMotion(true, preferences.effect_intensity, pathname, route);

  const destination = ({ to, label, icon, current }: Destination) => (
    <NavItem key={to} as={Link} to={to} icon={icon} current={(current ?? ((path) => path.startsWith(to)))(pathname)} tooltip={rail ? label : undefined}>
      {label}
    </NavItem>
  );
  const alert = appearanceError ? "外观保存失败，请重试。" : null;

  // v7-app / app-shell stay as ancestor hooks for unmigrated pages (workspace-refresh, simple-chat, pointer cursor).
  return (
    <div className="tpl-shell v7-app app-shell" data-nav={drawer.open ? "open" : "closed"} data-layout={conversation ? "conversation" : "workspace"} data-editor={editor || undefined} data-workbench={workbench || undefined} data-immersive={conversation || editor || undefined}>
      <nav className="tpl-nav" ref={drawer.nav} aria-label="主导航">
        <PublicArt path={`backdrops/nav-${dark ? "night" : "light"}.webp`} className="tpl-nav__art" aria-hidden="true" />
        <IconButton className="tpl-nav__close" label="关闭导航" icon={X} onClick={drawer.close} />
        <Link className="tpl-nav__brand" to="/" aria-label="世界を紡ぐ姫 · 织界之姬，回到故事首页">
          <img className="tpl-nav__crest" src={crest} width={56} height={56} alt="" draggable={false} onError={(event) => { event.currentTarget.hidden = true; }} />
          <PublicArt path="v3/brand/brand-compact.svg" className="tpl-nav__compact" aria-hidden="true" />
          <span className="tpl-nav__name"><strong lang="ja">世界を紡ぐ姫</strong><span>织界之姬</span></span>
          <span className="tpl-rule" aria-hidden="true" />
        </Link>
        {groups.map((group) => (
          <div className="tpl-nav__group" key={group.name}>
            <p className={group.label ? "tpl-nav__label" : "tpl-sr-only"}>{group.name}</p>
            {group.items.map(destination)}
          </div>
        ))}
        <div className="tpl-nav__foot">
          {destination(settings)}
          <NavItem
            as="button"
            type="button"
            icon={dark ? Moon : Sun}
            disabled={appearanceSaving}
            aria-label={dark ? "切换为亮色外观" : "切换为深色外观"}
            tooltip={rail ? (dark ? "夜读" : "白昼") : undefined}
            onClick={() => void updateAppearance({ theme_id: dark ? "iris-light" : "iris-night" }).catch(() => undefined)}
          >
            {dark ? "夜读" : "白昼"}
          </NavItem>
          {alert && <p className="tpl-alert" role="alert">{alert}</p>}
        </div>
      </nav>
      <button type="button" className="tpl-scrim" aria-label="关闭导航" tabIndex={-1} onClick={drawer.close} />
      <div className="tpl-main" ref={drawer.main} aria-hidden={drawer.open || undefined}>
        <header className="tpl-appbar">
          <PublicArt path="v3/brand/brand-compact.svg" className="tpl-appbar__brand" aria-hidden="true" />
          <IconButton ref={drawer.trigger} label="打开导航" icon={Menu} aria-expanded={drawer.open} onClick={drawer.show} />
          <Link className="tpl-appbar__title" to="/" aria-label="世界を紡ぐ姫，回到故事首页" lang="ja">世界を紡ぐ姫</Link>
        </header>
        {alert && <p className="tpl-alert tpl-alert--bar" role="alert">{alert}</p>}
        <div className="tpl-content" ref={route}><PublicArt path={`backdrops/ambient-${dark ? "night" : "light"}.webp`} className="tpl-content__art" aria-hidden="true" />{children}</div>
      </div>
    </div>
  );
}
