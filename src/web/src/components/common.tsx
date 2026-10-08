import { SurfaceDialog } from '../design-system/SurfaceDialog';
import { X } from '../design-system/Icon';
import type { ReactNode } from "react";
import { Avatar as DesignAvatar } from "../design-system/Avatar";
import { useAppearanceAttribute } from "../design-system/internal/useAppearanceAttribute";
import type { CharacterColor } from "../types";

export const COLOR_CLASSES: Record<
  CharacterColor,
  { avatar: string; name: string; bubble: string; badge: string; dot: string }
> = {
  emerald: {
    avatar: "bg-emerald-500",
    name: "text-emerald-700",
    bubble: "bg-emerald-50 border-emerald-200",
    badge: "bg-emerald-100 text-emerald-700",
    dot: "bg-emerald-500",
  },
  violet: {
    avatar: "bg-violet-500",
    name: "text-violet-700",
    bubble: "bg-violet-50 border-violet-200",
    badge: "bg-violet-100 text-violet-700",
    dot: "bg-violet-500",
  },
  amber: {
    avatar: "bg-amber-500",
    name: "text-amber-700",
    bubble: "bg-amber-50 border-amber-200",
    badge: "bg-amber-100 text-amber-700",
    dot: "bg-amber-500",
  },
  rose: {
    avatar: "bg-rose-500",
    name: "text-rose-700",
    bubble: "bg-rose-50 border-rose-200",
    badge: "bg-rose-100 text-rose-700",
    dot: "bg-rose-500",
  },
  sky: {
    avatar: "bg-sky-500",
    name: "text-sky-700",
    bubble: "bg-sky-50 border-sky-200",
    badge: "bg-sky-100 text-sky-700",
    dot: "bg-sky-500",
  },
  slate: {
    avatar: "bg-slate-500",
    name: "text-slate-700",
    bubble: "bg-slate-50 border-slate-200",
    badge: "bg-slate-100 text-slate-700",
    dot: "bg-slate-500",
  },
};

export function Avatar({
  name,
  color,
  size = "md",
  onClick,
  title,
  src,
  characterId,
  imageVersion,
}: {
  name: string;
  color: CharacterColor;
  size?: "xs" | "sm" | "md";
  onClick?: () => void;
  title?: string;
  src?: string;
  /** Stable library/session character ID; generated art is the default avatar source. */
  characterId?: string;
  imageVersion?: string | number;
}) {
  const preferredSize = useAppearanceAttribute("data-dialogue-avatar-size", "40");
  const imageSrc = src ?? (characterId
    ? `/api/v1/characters/${encodeURIComponent(characterId)}/avatar?v=${encodeURIComponent(String(imageVersion ?? 0))}`
    : undefined);
  const diameter = size === "xs" ? 24 : size === "sm" ? 32 : preferredSize === "32" ? 32 : preferredSize === "56" ? 56 : 40;
  const content = <DesignAvatar name={name} src={imageSrc} size={diameter} dense={size !== "md"} data-character-color={color} />;
  return onClick ? <button type="button" title={title ?? "查看角色信息"} aria-label={title ?? `查看${name}的角色信息`} onClick={onClick} className="ui-avatar-trigger">{content}</button> : content;
}

export function Switch({
  on,
  onToggle,
  label,
}: {
  on: boolean;
  onToggle: () => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${
        on ? "bg-emerald-500" : "bg-slate-300"
      }`}
      aria-pressed={on}
      aria-label={label}
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${
          on ? "right-0.5" : "left-0.5"
        }`}
      />
    </button>
  );
}

export function Modal({
  title,
  onClose,
  children,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <SurfaceDialog title={title} onClose={onClose} nested className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <div
        className={`max-h-[85vh] w-full ${wide ? "max-w-2xl" : "max-w-lg"} overflow-y-auto rounded-xl bg-white p-5 shadow-xl`}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="v7-icon-btn inline-flex min-h-11 min-w-11 items-center justify-center rounded-md"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        {children}
      </div>
    </SurfaceDialog>
  );
}
