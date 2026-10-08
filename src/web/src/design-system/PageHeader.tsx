import type { ComponentType, HTMLAttributes, ReactNode } from "react";
import type { IconProps } from "./Icon";
import { PublicArt } from "./PublicArt";
import { cx } from "./internal/cx";
import "./primitives/icon.css";
import "./primitives/pageheader.css";

export interface PageHeaderProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  title: ReactNode;
  /** One line under the title: counts, status, location. */
  meta?: ReactNode;
  art?: string;
  /** Glyph in the region-coloured mark before the title. */
  icon?: ComponentType<IconProps>;
  /** At most one primary, two secondary and one more-menu (spec section 5). */
  actions?: ReactNode;
  /** Heading level of the title. Defaults to h1; use h2 when the page has its own h1. */
  headingLevel?: 1 | 2;
}

/** Page title block: optional icon mark, title, meta line, and an actions slot on the right. */
export function PageHeader({ title, meta, art, icon: Glyph, actions, headingLevel = 1, className, ...rest }: PageHeaderProps) {
  const Heading = headingLevel === 1 ? "h1" : "h2";
  return (
    <div className={cx("ui-pageheader", className)} {...rest}>
      {art && <PublicArt path={art} className="ui-pageheader__art" />}
      {Glyph && <span className="ui-pageheader__mark" aria-hidden="true"><Glyph className="ui-icon ui-icon--lg" /></span>}
      <div className="ui-pageheader__text">
        <Heading className="ui-pageheader__title">{title}</Heading>
        {meta && <p className="ui-pageheader__meta">{meta}</p>}
      </div>
      {actions && <div className="ui-pageheader__actions">{actions}</div>}
    </div>
  );
}
