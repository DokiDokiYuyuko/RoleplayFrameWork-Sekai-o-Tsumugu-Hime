import type { ReactNode } from 'react';
import { Link, NavLink } from 'react-router';
import { ArrowLeft, ArrowRight, Users, Globe2, PenTool, Sparkles, BookOpen } from 'lucide-react';
import { MoonOrnament } from '../features/stories/StoryHome';
export function LibraryIndex() {
  return (
    <div className="v7-page v7-library-home">
      <header className="library-masthead">
        <div className="v7-eyebrow">THE STORY ATELIER / 素材库</div>
        <div className="home-page-title">
          <MoonOrnament />
          <h1>故事的收藏室</h1>
          <span className="home-title-rule" aria-hidden="true" />
        </div>
        <p className="v7-lead">
          收好人物的名字、世界的秘密，以及每一段故事的起点。
        </p>
      </header>
      <div className="library-chapters">
        {[
          {
            to: "/library/characters",
            number: "01",
            title: "角色",
            en: "CHARACTERS",
            text: "让人物拥有声音，也拥有自己的来路。",
            detail: "人物设定 · 关系 · 开场白",
            Icon: Users,
          },
          {
            to: "/worlds",
            number: "02",
            title: "世界",
            en: "WORLDS",
            text: "为故事搭起天空，收藏它的秩序与秘密。",
            detail: "背景档案 · 生物资料 · 世界书",
            Icon: Globe2,
          },
          {
            to: "/library/scenarios",
            number: "03",
            title: "场景预设",
            en: "SCENARIOS",
            text: "把角色与世界放进同一个值得开始的瞬间。",
            detail: "地点 · 氛围 · 故事起点",
            Icon: PenTool,
          },
        ].map(({ to, number, title, en, text, detail, Icon }) => (
          <Link to={to} className="library-chapter" key={to}>
            <div className="library-chapter-top">
              <span>
                {number} / {en}
              </span>
              <Icon size={28} strokeWidth={1.2} aria-hidden="true" />
            </div>
            <div className="library-chapter-art" aria-hidden="true">
              <Icon strokeWidth={0.65} />
              <i />
              <b />
            </div>
            <h2>{title}</h2>
            <p>{text}</p>
            <footer>
              <span>{detail}</span>
              <ArrowRight size={19} aria-hidden="true" />
            </footer>
          </Link>
        ))}
      </div>
      <div className="library-footpaths">
        <Link to="/library/imports">
          <Sparkles size={21} />
          <span>
            <strong>智能导入</strong>
            <small>把旧设定整理成可审阅的素材</small>
          </span>
          <ArrowRight size={17} />
        </Link>
        <Link to="/library/lorebooks">
          <BookOpen size={21} />
          <span>
            <strong>世界书</strong>
            <small>管理关键词条目与独立世界书</small>
          </span>
          <ArrowRight size={17} />
        </Link>
      </div>
      <p className="library-closing">
        <span aria-hidden="true">✧</span>{" "}
        一切都准备好之后，回到故事，写下下一页。
      </p>
    </div>
  );
}

export function SectionPage({
  title,
  eyebrow,
  fullBleed = false,
  children,
}: {
  title: string;
  eyebrow: string;
  fullBleed?: boolean;
  children: ReactNode;
}) {
  if (fullBleed) return <>{children}</>;
  const library = eyebrow.startsWith("LIBRARY");
  return (
    <div className={`v7-section-page${library ? " v7-library-section" : ""}`}>
      <div className="v7-section-heading">
        {library && (
          <Link to="/library" className="library-back">
            <ArrowLeft size={15} />
            素材收藏室
          </Link>
        )}
        <span className="v7-eyebrow">{eyebrow}</span>
        <div className="section-title-line">
          {library && <MoonOrnament />}
          <h1>{title}</h1>
          <span aria-hidden="true" />
        </div>
        {library && (
          <p className="section-subtitle">
            {title === "角色"
              ? "收藏人物的来路、声音与故事。"
              : title === "世界书"
                ? "把世界的知识整理成随故事展开的条目。"
                : "将人物、世界与开场编织成一个起点。"}
          </p>
        )}
        {library && (
          <nav className="v7-library-tabs" aria-label="素材分类">
            <NavLink
              to="/library/characters"
              className={({ isActive }) => (isActive ? "is-active" : "")}
            >
              角色
            </NavLink>
            <NavLink
              to="/worlds"
              className={({ isActive }) => (isActive ? "is-active" : "")}
            >
              世界
            </NavLink>
            <NavLink
              to="/library/lorebooks"
              className={({ isActive }) => (isActive ? "is-active" : "")}
            >
              世界书
            </NavLink>
            <NavLink
              to="/library/scenarios"
              className={({ isActive }) => (isActive ? "is-active" : "")}
            >
              场景预设
            </NavLink>
          </nav>
        )}
      </div>
      <div className="v7-legacy-surface">{children}</div>
    </div>
  );
}

