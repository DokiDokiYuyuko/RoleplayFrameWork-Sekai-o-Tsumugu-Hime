import type { ArchiveInput, ArchiveKind } from "../../types";

const BACKGROUND_SECTIONS = [["overview", "总览"], ["history", "历史"], ["geography", "地理"], ["society", "社会与制度"], ["other", "其他"]];
const BIOLOGY_CLASSES = [["race", "种族"], ["species", "物种"], ["creature", "怪物 / 生物"], ["other", "其他"]];
const BIO_FIELDS = [["appearance", "外形与寿命"], ["habitat", "栖息环境"], ["culture", "社会与文化"], ["abilities", "能力"], ["limitations", "限制与弱点"]];

function splitList(value: string): string[] {
  return value.split(/[,，、\n]/).map((item) => item.trim()).filter(Boolean);
}

export function ArchiveRecordEditor({ kind, value, onChange, onSave, onCancel, busy = false, saveLabel = "保存档案", visibilityLocked = false, bodyHelp }: {
  kind: ArchiveKind;
  value: ArchiveInput;
  onChange: (value: ArchiveInput) => void;
  onSave: () => void;
  onCancel: () => void;
  busy?: boolean;
  saveLabel?: string;
  visibilityLocked?: boolean;
  bodyHelp?: string;
}) {
  const change = (patch: Partial<ArchiveInput>) => onChange({ ...value, ...patch });
  const changeData = (key: string, text: string) =>
    change({ kind_data: { ...value.kind_data, [key]: text } });
  const classes = kind === "background" ? BACKGROUND_SECTIONS : BIOLOGY_CLASSES;
  return (
    <div className="worlds-editor">
      <div className="worlds-editor-grid">
        <label className="worlds-field">名称
          <input value={value.title} onChange={(event) => change({ title: event.target.value })} placeholder={kind === "background" ? "例如：示例世界" : "例如：示例生物"} />
        </label>
        <label className="worlds-field">类别
          <select value={kind === "background" ? value.kind_data.section ?? "other" : value.kind_data.classification ?? "other"}
            onChange={(event) => changeData(kind === "background" ? "section" : "classification", event.target.value)}>
            {classes.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </label>
      </div>
      <div className="worlds-editor-grid">
        <label className="worlds-field">别名 <small>用逗号分隔</small>
          <input value={value.aliases.join("，")} onChange={(event) => change({ aliases: splitList(event.target.value) })} />
        </label>
        <label className="worlds-field">标签 <small>用逗号分隔</small>
          <input value={value.tags.join("，")} onChange={(event) => change({ tags: splitList(event.target.value) })} />
        </label>
      </div>
      <label className="worlds-field">短摘要
        <textarea rows={3} value={value.summary} onChange={(event) => change({ summary: event.target.value })} placeholder="便于在目录中快速找到这篇资料" />
      </label>
      {kind === "biology" && <div className="worlds-bio-fields"><h3>生物资料</h3>
        <div className="worlds-editor-grid">{BIO_FIELDS.map(([key, label]) =>
          <label key={key} className="worlds-field">{label}<textarea rows={3} value={value.kind_data[key] ?? ""} onChange={(event) => changeData(key, event.target.value)} /></label>
        )}</div>
      </div>}
      <label className="worlds-field worlds-body-field">完整正文 <small>{bodyHelp || "智能导入会保留原稿。公开背景设定每轮提供给模型；公开生物设定在名称、别名或标签被提及时提供，短摘要和结构字段也会参与注入。"}</small>
        <textarea rows={18} value={value.body} onChange={(event) => change({ body: event.target.value })} placeholder="在这里保存完整设定原稿…" />
      </label>
      <label className="worlds-visibility"><input type="checkbox" checked={value.visibility === "private"} disabled={visibilityLocked} onChange={(event) => change({ visibility: event.target.checked ? "private" : "public" })} /> 作者私密资料 <small>{visibilityLocked ? "私有来源或私有原档案要求保留作者可见范围" : "不会进入角色提示词；导出世界文件时仍会包含"}</small></label>
      <div className="worlds-editor-actions">
        <button className="v7-btn v7-btn-soft" onClick={onCancel}>取消</button>
        <button className="v7-btn v7-btn-primary" disabled={busy || !value.title.trim()} onClick={onSave}>{busy ? "保存中…" : saveLabel}</button>
      </div>
    </div>
  );
}
