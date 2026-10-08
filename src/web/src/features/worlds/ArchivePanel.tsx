import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ArrowLeft, FileText, Plus, Search, Trash2 } from "lucide-react";
import { worldsClient as api } from './worldsClient';
import { compareNewestFirst } from "../../utils/timestamps";
import { ArchiveRecordEditor } from "./ArchiveRecordEditor";
import { BiologyImportDialog } from "./BiologyImportDialog";
import type {
  ArchiveInput,
  ArchiveKind,
  ArchiveRecord,
  World,
} from "../../types";

const BACKGROUND_SECTIONS = [
  ["overview", "总览"],
  ["history", "历史"],
  ["geography", "地理"],
  ["society", "社会与制度"],
  ["other", "其他"],
];
const BIOLOGY_CLASSES = [
  ["race", "种族"],
  ["species", "物种"],
  ["creature", "怪物 / 生物"],
  ["other", "其他"],
];
const BIO_FIELDS = [
  ["appearance", "外形与寿命"],
  ["habitat", "栖息环境"],
  ["culture", "社会与文化"],
  ["abilities", "能力"],
  ["limitations", "限制与弱点"],
];

function blank(kind: ArchiveKind): ArchiveInput {
  return {
    kind,
    subtype: "",
    title: "",
    aliases: [],
    tags: [],
    summary: "",
    body: "",
    visibility: "public",
    kind_data:
      kind === "background"
        ? { section: "overview" }
        : {
            classification: "race",
            appearance: "",
            habitat: "",
            culture: "",
            abilities: "",
            limitations: "",
          },
  };
}
function fromRecord(record: ArchiveRecord): ArchiveInput {
  return {
    kind: record.kind,
    subtype: record.subtype,
    title: record.title,
    aliases: [...record.aliases],
    tags: [...record.tags],
    summary: record.summary,
    body: record.body,
    visibility: record.visibility,
    kind_data: { ...record.kind_data },
  };
}
function labels(kind: ArchiveKind) {
  return kind === "background" ? BACKGROUND_SECTIONS : BIOLOGY_CLASSES;
}

function ArchiveBody({ text }: { text: string }) {
  if (!text)
    return <div className="worlds-reader-body">这篇资料还没有正文。</div>;
  return (
    <div className="worlds-reader-body">
      {text.split(/\r?\n/).map((line, index) => {
        const heading = /^(#{1,4})\s+(.+)$/.exec(line);
        if (heading)
          return (
            <h4 className={`worlds-markdown-h${heading[1].length}`} key={index}>
              {heading[2]}
            </h4>
          );
        if (/^\s*[-*]\s+/.test(line))
          return (
            <p className="worlds-markdown-list" key={index}>
              • {line.replace(/^\s*[-*]\s+/, "")}
            </p>
          );
        if (/^>\s?/.test(line))
          return (
            <blockquote key={index}>{line.replace(/^>\s?/, "")}</blockquote>
          );
        return line.trim() ? (
          <p key={index}>{line}</p>
        ) : (
          <div className="worlds-markdown-gap" key={index} />
        );
      })}
    </div>
  );
}

export function ArchivePanel({
  world,
  onChange,
  onError,
}: {
  world: World;
  onChange: (next: World) => void;
  onError: (message: string) => void;
}) {
  const navigate = useNavigate();
  const { kind: rawKind, recordId } = useParams();
  const kind: ArchiveKind = rawKind === "biology" ? "biology" : "background";
  const record = world.archive_records.find(
    (item) => item.id === recordId && item.kind === kind,
  );
  const isNew = recordId === "new";
  const [query, setQuery] = useState("");
  const [form, setForm] = useState<ArchiveInput>(() =>
    record ? fromRecord(record) : blank(kind),
  );
  const [editing, setEditing] = useState(isNew);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [draftNotice, setDraftNotice] = useState(false);
  const draftKey = `mrp:world-draft:${world.id}:${recordId ?? kind}`;
  useEffect(() => {
    const initial = record ? fromRecord(record) : blank(kind);
    const stored = localStorage.getItem(draftKey);
    if (stored) {
      try {
        const draft = JSON.parse(stored) as ArchiveInput;
        if (draft.kind === kind) {
          setForm(draft);
          setEditing(true);
          setDirty(true);
          setDraftNotice(true);
          return;
        }
      } catch {
        /* Ignore a damaged local draft. */
      }
    }
    setForm(initial);
    setEditing(isNew);
    setDirty(false);
    setDraftNotice(false);
  }, [world.id, recordId, kind]);
  useEffect(() => {
    if (!dirty) return;
    const event = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", event);
    return () => window.removeEventListener("beforeunload", event);
  }, [dirty]);
  const move = (path: string) => {
    if (
      dirty &&
      !window.confirm(
        "当前条目有未保存的修改。离开后仍可从本机草稿恢复，确定离开吗？",
      )
    )
      return;
    navigate(path);
  };
  const change = (patch: Partial<ArchiveInput>) => {
    setForm((old) => {
      const next = { ...old, ...patch };
      localStorage.setItem(draftKey, JSON.stringify(next));
      return next;
    });
    setDirty(true);
  };
  const rows = useMemo(
    () =>
      world.archive_records
        .filter(
          (item) =>
            item.kind === kind &&
            `${item.title} ${item.aliases.join(" ")} ${item.tags.join(" ")} ${item.summary} ${item.body}`
              .toLocaleLowerCase()
              .includes(query.toLocaleLowerCase()),
        )
        .sort((a, b) => compareNewestFirst(a.updated_at, b.updated_at)),
    [world.archive_records, kind, query],
  );
  const save = async () => {
    if (!form.title.trim()) {
      onError("请填写条目名称");
      return;
    }
    setBusy(true);
    onError("");
    try {
      const payload = { ...form, title: form.title.trim() };
      const next = record
        ? await api.patchArchive(world.id, record.id, world.revision, payload)
        : await api.createArchive(world.id, world.revision, payload);
      onChange(next);
      localStorage.removeItem(draftKey);
      setDirty(false);
      setDraftNotice(false);
      setEditing(false);
      if (!record) {
        const created = next.archive_records[next.archive_records.length - 1];
        navigate(`/worlds/${world.id}/archive/${kind}/${created.id}`, {
          replace: true,
        });
      }
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (
      !record ||
      !window.confirm(`删除「${record.title}」？这会删除世界档案中的原稿。`)
    )
      return;
    setBusy(true);
    onError("");
    try {
      const next = await api.deleteArchive(world.id, record.id, world.revision);
      onChange(next);
      localStorage.removeItem(draftKey);
      setDirty(false);
      navigate(`/worlds/${world.id}/archive/${kind}`, { replace: true });
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : "删除失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="worlds-archive">
      <div className="worlds-subtabs">
        <button
          className={kind === "background" ? "active" : ""}
          onClick={() => move(`/worlds/${world.id}/archive/background`)}
        >
          背景设定{" "}
          <small>
            {
              world.archive_records.filter((item) => item.kind === "background")
                .length
            }
          </small>
        </button>
        <button
          className={kind === "biology" ? "active" : ""}
          onClick={() => move(`/worlds/${world.id}/archive/biology`)}
        >
          生物设定{" "}
          <small>
            {
              world.archive_records.filter((item) => item.kind === "biology")
                .length
            }
          </small>
        </button>
      </div>
      <div className={`worlds-archive-layout ${recordId ? "has-detail" : ""}`}>
        <aside className="worlds-archive-list">
          <div className="worlds-list-head">
            <div>
              <span className="v7-eyebrow">WORLD ARCHIVE</span>
              <h2>{kind === "background" ? "背景文章" : "生物资料卡"}</h2>
            </div>
            <div className="worlds-list-actions">
              {kind === "biology" && <BiologyImportDialog world={world} onImported={onChange} />}
              <button className="worlds-import-button" title="AI 整理世界档案" aria-label="AI 整理世界档案"
                onClick={() => move(`/worlds/${world.id}/organize?category=archives`)}>
                AI 整理
              </button>
              <button className="worlds-create-button"
                aria-label="新建条目"
                title="新建条目"
                onClick={() => move(`/worlds/${world.id}/archive/${kind}/new`)}
              >
                <Plus size={18} />
              </button>
            </div>
          </div>
          <label className="worlds-search">
            <Search size={16} />
            <input
              aria-label="搜索档案"
              placeholder="搜索标题、标签与正文"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <div className="worlds-record-list">
            {rows.map((item) => (
              <button
                key={item.id}
                className={recordId === item.id ? "active" : ""}
                onClick={() =>
                  move(`/worlds/${world.id}/archive/${kind}/${item.id}`)
                }
              >
                <strong>{item.title}</strong>
                <span>
                  {item.summary || item.body.slice(0, 65) || "暂无内容"}
                </span>
                <small>
                  {item.visibility === "private" ? "作者私密 · " : ""}
                  {labels(kind).find(
                    ([value]) =>
                      value ===
                      (kind === "background"
                        ? item.kind_data.section
                        : item.kind_data.classification),
                  )?.[1] ?? "其他"}
                </small>
              </button>
            ))}
            {!rows.length && (
              <p className="worlds-muted">
                {query ? "没有找到匹配的条目。" : "这里还没有资料。"}
              </p>
            )}
          </div>
        </aside>
        <section className="worlds-record-detail">
          {!recordId ? (
            <div className="worlds-record-empty">
              <FileText size={34} />
              <h2>
                {kind === "background"
                  ? "写下完整的世界背景"
                  : "整理世界里的生命"}
              </h2>
              <p>
                {kind === "background"
                  ? "可以先把已有的 5000 字原稿完整粘贴成一篇“总览”。以后再拆历史、地理等文章。"
                  : "一个种族、物种或怪物用一张资料卡，结构字段和完整正文可以并存。"}
              </p>
              <button
                className="v7-btn v7-btn-primary"
                onClick={() => move(`/worlds/${world.id}/archive/${kind}/new`)}
              >
                <Plus size={16} />{" "}
                {kind === "background" ? "新建背景文章" : "新建生物卡"}
              </button>
            </div>
          ) : !record && !isNew ? (
            <div className="worlds-record-empty">
              这条档案不存在。
              <Link to={`/worlds/${world.id}/archive/${kind}`}>返回目录</Link>
            </div>
          ) : (
            <>
              <div className="worlds-mobile-back">
                <button
                  onClick={() => move(`/worlds/${world.id}/archive/${kind}`)}
                >
                  <ArrowLeft size={16} /> 返回目录
                </button>
              </div>
              <div className="worlds-record-head">
                <div>
                  <span className="v7-eyebrow">
                    {kind === "background"
                      ? "BACKGROUND / 背景设定"
                      : "BIOLOGY / 生物设定"}
                  </span>
                  <h2>{isNew ? "新建条目" : record?.title}</h2>
                </div>
                <div className="worlds-record-actions">
                  {record && !editing && <button className="v7-btn v7-btn-soft" onClick={() => move(`/worlds/${world.id}/organize?category=archives&archive=${encodeURIComponent(record.id)}`)}>AI 整理此档案</button>}
                  {record && !editing && (
                    <button
                      className="v7-btn v7-btn-soft"
                      onClick={() => setEditing(true)}
                    >
                      编辑
                    </button>
                  )}
                  {record && (
                    <button
                      className="worlds-danger"
                      title="删除条目"
                      aria-label="删除条目"
                      disabled={busy}
                      onClick={() => void remove()}
                    >
                      <Trash2 size={17} />
                    </button>
                  )}
                </div>
              </div>
              {draftNotice && (
                <div className="worlds-draft-note">
                  已恢复这台设备上的未提交草稿。保存后会清除草稿。
                </div>
              )}
              {editing ? (
                <ArchiveRecordEditor
                  kind={kind}
                  value={form}
                  onChange={(next) => change(next)}
                  onSave={() => void save()}
                  onCancel={() => {
                    if (dirty && !window.confirm("放弃本次修改？")) return;
                    localStorage.removeItem(draftKey);
                    setDirty(false);
                    if (isNew) navigate(`/worlds/${world.id}/archive/${kind}`);
                    else { setForm(fromRecord(record!)); setEditing(false); }
                  }}
                  busy={busy}
                />
              ) : (
                record && (
                  <article className="worlds-reader">
                    <div className="worlds-reader-meta">
                      <span>
                        {labels(kind).find(
                          ([value]) =>
                            value ===
                            (kind === "background"
                              ? record.kind_data.section
                              : record.kind_data.classification),
                        )?.[1] ?? "其他"}
                      </span>
                      {record.visibility === "private" && <span>作者私密</span>}
                      {record.tags.map((tag) => (
                        <span key={tag}>{tag}</span>
                      ))}
                    </div>
                    {record.aliases.length > 0 && (
                      <p>
                        <strong>别名：</strong>
                        {record.aliases.join("、")}
                      </p>
                    )}
                    {record.summary && (
                      <p className="worlds-reader-summary">{record.summary}</p>
                    )}
                    {kind === "biology" &&
                      BIO_FIELDS.filter(([key]) => record.kind_data[key]).map(
                        ([key, label]) => (
                          <div className="worlds-reader-field" key={key}>
                            <h3>{label}</h3>
                            <p>{record.kind_data[key]}</p>
                          </div>
                        ),
                      )}
                    <h3>完整设定</h3>
                    <ArchiveBody text={record.body} />
                    <div className="worlds-reader-foot">
                      档案原文只供阅读。需要角色按关键词获得这部分知识时，请到世界书写运行词条。
                    </div>
                    {record.visibility === "public" && (
                      <button
                        className="v7-btn v7-btn-soft"
                        onClick={() => move(`/worlds/${world.id}/organize?category=lorebook&source_archive=${encodeURIComponent(record.id)}`)}
                      >
                        提炼为世界书词条
                      </button>
                    )}
                  </article>
                )
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

