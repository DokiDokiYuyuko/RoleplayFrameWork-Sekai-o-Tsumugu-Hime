import { useState, type ReactNode } from "react";
import { Avatar, type AvatarSize } from "./Avatar";
import { Button } from "./Button";
import { Card, CardTitle } from "./Card";
import { Checkbox } from "./Checkbox";
import { Divider } from "./Divider";
import { EmptyState } from "./EmptyState";
import { Field } from "./Field";
import { FilterBar, FilterBarSearch, FilterBarSpacer } from "./FilterBar";
import { IconButton } from "./IconButton";
import { Copy, Eye, EyeOff, GitBranch, MoreHorizontal, Pencil, Plus, RefreshCw, Search, Trash2, Upload, Users } from "./Icon";
import { Menu } from "./Menu";
import { PageHeader } from "./PageHeader";
import { Panel } from "./Panel";
import { Radio } from "./Radio";
import { Segmented } from "./Segmented";
import { Select, type SelectOption } from "./Select";
import { Skeleton } from "./Skeleton";
import { Switch } from "./Switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./Tabs";
import { Tag, type TagTone } from "./Tag";
import { Textarea } from "./Textarea";
import { TextInput } from "./TextInput";
import { useToast } from "./Toast";
import { WritingPanel } from "./WritingPanel";
import "./KitGallery.css";

/*
 * Living documentation of the primitive kit: every primitive in each state, with synthetic content.
 * Route: /dev/ui-kit (not linked from any navigation). Compare against
 * document/design/ui-system-demo/index.html#gallery.
 * Button, IconButton, Tooltip and NavItem belong to the foundation layer and are not repeated here.
 */

function Section({ id, title, lead, children }: { id: string; title: string; lead?: string; children: ReactNode }) {
  return (
    <section className="kit-sec" aria-labelledby={id}>
      <h2 className="kit-sec__title" id={id}>{title}</h2>
      {lead && <p className="kit-sec__lead">{lead}</p>}
      {children}
    </section>
  );
}

function Row({ label, note, children }: { label: string; note?: string; children: ReactNode }) {
  return (
    <div className="kit-row">
      <div className="kit-row__label">{label}{note && <small>{note}</small>}</div>
      <div className="kit-row__items">{children}</div>
    </div>
  );
}

const SECTIONS: SelectOption[] = [
  { value: "story", label: "故事" },
  { value: "chat", label: "聊天" },
  { value: "create", label: "创作" },
];
const TAG_FILTER: SelectOption[] = [
  { value: "all", label: "全部" },
  { value: "guide", label: "向导" },
  { value: "gentle", label: "温柔" },
];
const SORT: SelectOption[] = [
  { value: "recent", label: "最近更新" },
  { value: "name", label: "姓名" },
  { value: "old", label: "最早创建", disabled: true },
];
const TONES: Array<[TagTone, string]> = [
  ["neutral", "中性"], ["accent", "鸢尾"], ["region", "分区"], ["success", "成功"], ["warning", "提醒"], ["danger", "危险"],
];
const AVATARS: Array<[AvatarSize, string]> = [[24, "澄"], [32, "岚"], [40, "朔"], [56, "绯"], [72, "霁"]];

function FieldSection() {
  const [reveal, setReveal] = useState(false);
  const [sectionValue, setSectionValue] = useState("story");
  return (
    <Section id="kit-field" title="Field · TextInput · Select · Textarea" lead="同高、同内边距、同边线。标签到控件 6px，控件到说明 4px，字段间 16px。">
      <div className="kit-grid">
        <Field label="角色名称" required hint="显示在消息与角色库中。"><TextInput defaultValue="向导·澄音" /></Field>
        <Field label="称呼" optional hint="留空则使用角色名称。"><TextInput placeholder="例如：澄音小姐" /></Field>
        <Field label="网关地址" error="地址需要以 https:// 开头。"><TextInput defaultValue="gateway.invalid" /></Field>
        <Field label="只读项" hint="只读，不可编辑。"><TextInput defaultValue="自动生成的编号 0042" readOnly /></Field>
        <Field label="禁用项"><TextInput defaultValue="不可用" disabled /></Field>
        <Field label="带图标的搜索"><TextInput leadingIcon={Search} placeholder="搜索角色、标签" /></Field>
        <Field label="尾部操作（显示 / 隐藏）">
          <TextInput
            type={reveal ? "text" : "password"}
            defaultValue="示例口令"
            trailing={<IconButton label={reveal ? "隐藏内容" : "显示内容"} icon={reveal ? EyeOff : Eye} size="sm" aria-pressed={reveal} onClick={() => setReveal((value) => !value)} />}
          />
        </Field>
        <Field label="故事分区 · Select" hint={`受控，当前值：${sectionValue}`}>
          <Select options={SECTIONS} value={sectionValue} onValueChange={setSectionValue} />
        </Field>
        <Field label="未选择时的占位"><Select options={SECTIONS} placeholder="请选择分区" /></Field>
        <Field label="禁用的选择器"><Select options={SECTIONS} defaultValue="chat" disabled /></Field>
        <Field label="校验失败的选择器" error="请选择一个分区。"><Select options={SECTIONS} placeholder="请选择分区" /></Field>
        <Field label="简介 · Textarea" className="kit-grid__wide" hint="自动增高，无拖拽手柄。">
          <Textarea rows={2} placeholder="用几句话介绍这个角色" defaultValue="她总是走在队伍最前面，提着一盏不会熄灭的星灯。" />
        </Field>
      </div>
      <h3 className="kit-sub">悬停、焦点与尺寸</h3>
      <div className="kit-grid">
        <div className="kit-force-hover"><Field label="悬停（强制）"><TextInput defaultValue="边线加深" /></Field></div>
        <div className="kit-force-focus"><Field label="焦点（强制）"><TextInput defaultValue="鸢尾边线加焦点环" /></Field></div>
        <Field label="三种尺寸" group>
          <div className="kit-stack">
            <TextInput size="sm" aria-label="小尺寸" defaultValue="sm 32" />
            <TextInput aria-label="中尺寸" defaultValue="md 36" />
            <TextInput size="lg" aria-label="大尺寸" defaultValue="lg 44" />
          </div>
        </Field>
        <Field label="安静样式 · Select 小尺寸前缀" group>
          <div className="kit-stack">
            <TextInput tone="quiet" size="sm" aria-label="安静输入框" defaultValue="quiet" />
            <Select tone="quiet" size="sm" width="auto" prefix="回复模式" options={SECTIONS} defaultValue="story" aria-label="回复模式" />
          </div>
        </Field>
      </div>
    </Section>
  );
}

function WritingSection() {
  return (
    <Section id="kit-writing" title="WritingPanel" lead="只有外框一条边，文本区无边，焦点环围住整个面板。">
      <div className="kit-narrow kit-stack">
        <WritingPanel
          reading
          showCount
          title="人物原稿"
          rows={3}
          defaultValue="澄音是旧渡口的引路人，说话轻，做事稳。她相信灯火能替人记住没说完的话。"
          tools={
            <>
              <IconButton label="复制全文" icon={Copy} size="sm" />
              <IconButton label="重新整理" icon={RefreshCw} size="sm" />
            </>
          }
        />
        <WritingPanel aria-label="没有标题的面板" placeholder="没有标题时，工具靠右。" tools={<IconButton label="清空" icon={Trash2} size="sm" />} footer={<span>Ctrl + Enter 发送</span>} />
        <WritingPanel title="禁用的面板" disabled defaultValue="不可编辑的内容" />
      </div>
    </Section>
  );
}

function ChoiceSection() {
  const [streaming, setStreaming] = useState(true);
  return (
    <Section id="kit-check" title="Checkbox · Radio · Switch">
      <div className="kit-cols">
        <div className="kit-col"><h3 className="kit-sub">Checkbox</h3>
          <Checkbox label="未选中" />
          <Checkbox label="已选中" defaultChecked />
          <Checkbox label="禁用" disabled />
          <Checkbox label="选中且禁用" defaultChecked disabled />
          <Checkbox label="校验失败" aria-invalid="true" />
        </div>
        <div className="kit-col"><h3 className="kit-sub">Radio</h3>
          <div className="kit-col" role="radiogroup" aria-label="配色">
            <Radio name="kit-color" label="鸢尾" defaultChecked />
            <Radio name="kit-color" label="青蓝" />
            <Radio name="kit-color" label="蔷薇" />
            <Radio name="kit-color-off" label="禁用" disabled />
          </div>
        </div>
        <div className="kit-col"><h3 className="kit-sub">Switch</h3>
          <Switch label={`流式输出（受控：${streaming ? "开" : "关"}）`} checked={streaming} onChange={(event) => setStreaming(event.target.checked)} />
          <Switch label="自动整理记忆" defaultChecked />
          <Switch label="禁用" disabled />
          <Switch label="开启且禁用" defaultChecked disabled />
        </div>
      </div>
    </Section>
  );
}

function TabsSection() {
  const [mode, setMode] = useState("reply");
  return (
    <Section id="kit-tabs" title="Tabs · Segmented">
      <div className="kit-cols">
        <div className="kit-col kit-col--wide"><h3 className="kit-sub">Tabs（页内分区，方向键切换）</h3>
          <Tabs defaultValue="profile">
            <TabsList aria-label="示例分区">
              <TabsTrigger value="profile">设定</TabsTrigger>
              <TabsTrigger value="relation">关系</TabsTrigger>
              <TabsTrigger value="memory">记忆</TabsTrigger>
              <TabsTrigger value="off" disabled>禁用</TabsTrigger>
            </TabsList>
            <TabsContent value="profile">基础设定：姓名、身份与外貌。</TabsContent>
            <TabsContent value="relation">关系：与其他角色的态度与称呼。</TabsContent>
            <TabsContent value="memory">记忆：重要事件与长期印象。</TabsContent>
          </Tabs>
        </div>
        <div className="kit-col"><h3 className="kit-sub">Segmented（模式切换）</h3>
          <Segmented
            aria-label="回复模式"
            value={mode}
            onValueChange={setMode}
            options={[{ value: "reply", label: "角色回复" }, { value: "free", label: "自由续写" }, { value: "watch", label: "旁观" }]}
          />
          <Segmented
            size="sm"
            aria-label="小尺寸分段"
            defaultValue="m"
            options={[{ value: "s", label: "小" }, { value: "m", label: "中" }, { value: "l", label: "大", disabled: true }]}
          />
          <Segmented
            size="lg"
            aria-label="大尺寸分段"
            defaultValue="a"
            options={[{ value: "a", label: "全部" }, { value: "b", label: "进行中" }]}
          />
        </div>
      </div>
    </Section>
  );
}

function TagSection() {
  const [tags, setTags] = useState(["向导", "温柔", "旧渡口"]);
  return (
    <Section id="kit-tag" title="Tag · Badge">
      <div className="kit-rows">
        <Row label="色调" note="高 24 · 12px">
          {TONES.map(([tone, text]) => <Tag key={tone} tone={tone}>{text}</Tag>)}
        </Row>
        <Row label="带状态点">
          <Tag tone="success" dot strong>已连接</Tag>
          <Tag tone="warning" dot strong>未保存的修改</Tag>
          <Tag tone="danger" dot strong>连接失败</Tag>
        </Row>
        <Row label="可删除" note="× 为 16px，点击区为整个标签高度">
          {tags.map((tag) => (
            <Tag key={tag} tone="accent" onRemove={() => setTags((current) => current.filter((item) => item !== tag))} removeLabel={`移除标签 ${tag}`}>{tag}</Tag>
          ))}
          {tags.length === 0 && <Button size="sm" onClick={() => setTags(["向导", "温柔", "旧渡口"])}>恢复示例标签</Button>}
        </Row>
      </div>
    </Section>
  );
}

function CardSection() {
  return (
    <Section id="kit-card" title="Card · Panel">
      <div className="kit-cardrow">
        <Card><CardTitle>静止卡片</CardTitle><p className="kit-p">圆角 12、内边距 16、边线 subtle。没有金色按钮，也没有角花。</p></Card>
        <Card interactive asChild>
          <a href="#kit-card" onClick={(event) => event.preventDefault()}><CardTitle>可点击卡片（链接）</CardTitle><p className="kit-p">悬停时边线变为 default 并升到 e2。键盘聚焦可见。</p></a>
        </Card>
        <div className="kit-force-card"><Card interactive><CardTitle>悬停（强制）</CardTitle><p className="kit-p">展示悬停状态的静态样子。</p></Card></div>
        <Panel><CardTitle>Panel</CardTitle><p className="kit-p">圆角 16、内边距 24，用于较大的内容块。</p></Panel>
        <Panel compact><CardTitle>Panel · compact</CardTitle><p className="kit-p">内边距 20。</p></Panel>
      </div>
    </Section>
  );
}

function MenuToastSection() {
  const toast = useToast();
  const [last, setLast] = useState("尚未选择");
  const pick = (text: string) => () => setLast(text);
  return (
    <Section id="kit-menu" title="Menu · Toast" lead="菜单为 Radix DropdownMenu：方向键、Home/End、首字母、Escape 与焦点返回。危险项自动排在末尾并加分隔线。">
      <div className="kit-cols">
        <div className="kit-col"><h3 className="kit-sub">Menu</h3>
          <div className="kit-inline">
            <Menu
              trigger={<IconButton label="更多操作" icon={MoreHorizontal} />}
              items={[
                { id: "delete", label: "删除此条", icon: Trash2, tone: "danger", onSelect: pick("删除此条") },
                { id: "copy", label: "复制消息", icon: Copy, onSelect: pick("复制消息") },
                { id: "regen", label: "重新生成", icon: RefreshCw, onSelect: pick("重新生成") },
                { id: "edit", label: "编辑", icon: Pencil, onSelect: pick("编辑") },
                { id: "branch", label: "从此处分支", icon: GitBranch, disabled: true },
              ]}
            />
            <span className="kit-p" role="status">最近选择：{last}</span>
          </div>
        </div>
        <div className="kit-col"><h3 className="kit-sub">Toast（礼貌播报，不抢焦点）</h3>
          <div className="kit-inline">
            <Button onClick={() => toast.show({ message: "已保存到本地", tone: "success" })}>成功</Button>
            <Button onClick={() => toast.show({ message: "已复制到剪贴板", tone: "info" })}>提示</Button>
            <Button onClick={() => toast.show({ message: "连接失败，请稍后重试", tone: "danger" })}>错误</Button>
          </div>
        </div>
      </div>
    </Section>
  );
}

function PageSection() {
  const [tag, setTag] = useState("all");
  const [sort, setSort] = useState("recent");
  const [query, setQuery] = useState("");
  return (
    <Section id="kit-page" title="PageHeader · FilterBar">
      <div className="kit-stack" data-region="character">
        <PageHeader
          headingLevel={2}
          icon={Users}
          title="角色库"
          meta="12 位角色"
          actions={
            <>
              <Button icon={Upload}>导入角色卡</Button>
              <Button variant="primary" icon={Plus}>新建角色</Button>
              <IconButton label="更多" icon={MoreHorizontal} />
            </>
          }
        />
        <FilterBar aria-label="筛选角色">
          <FilterBarSearch aria-label="搜索角色" placeholder="搜索姓名、标签、简介" value={query} onChange={(event) => setQuery(event.target.value)} />
          <Select prefix="标签" width="auto" aria-label="按标签筛选" options={TAG_FILTER} value={tag} onValueChange={setTag} />
          <FilterBarSpacer />
          <Select prefix="排序" width="auto" aria-label="排序方式" options={SORT} value={sort} onValueChange={setSort} />
        </FilterBar>
      </div>
    </Section>
  );
}

function MiscSection() {
  return (
    <Section id="kit-misc" title="EmptyState · Skeleton · Avatar · Divider">
      <div className="kit-cols">
        <div className="kit-col kit-col--wide"><h3 className="kit-sub">EmptyState（插画由装饰层按强度显示，这里为默认的图标形态）</h3>
          <Card>
            <EmptyState
              icon={Users}
              art="/brand/moonweave/v1/characters-enchanted-library.webp"
              title="还没有角色"
              description="新建或导入一位角色，他们会出现在这里。"
              action={<Button icon={Plus}>新建角色</Button>}
            />
          </Card>
          <h3 className="kit-sub">EmptyState · compact</h3>
          <EmptyState variant="compact" icon={Users} title="场景里还没有人物" description="从角色库添加后会显示在这里。" />
        </div>
        <div className="kit-col"><h3 className="kit-sub">Skeleton</h3>
          <div className="kit-skel" aria-busy="true">
            <Skeleton shape="circle" width={40} height={40} />
            <div><Skeleton width={140} /><Skeleton width={220} className="kit-skel__gap" /></div>
          </div>
          <Skeleton width="100%" height={64} />
          <h3 className="kit-sub">Avatar 24 / 32 / 40 / 56 / 72</h3>
          <div className="kit-inline">
            {AVATARS.map(([size, name]) => <Avatar key={size} size={size} name={name} />)}
          </div>
          <h3 className="kit-sub">图片加载失败时回落到首字</h3>
          <div className="kit-inline">
            <Avatar size={40} name="澄音" src="/kit-gallery-missing.png" label="澄音" />
            <Avatar size={56} name="岚" src="/kit-gallery-missing.png" />
          </div>
        </div>
        <div className="kit-col"><h3 className="kit-sub">Divider</h3>
          <Divider />
          <Divider label="场景切换 · 旧渡口" />
        </div>
      </div>
    </Section>
  );
}

export default function KitGallery() {
  return (
    <>
      <main className="kit-gallery" data-region="neutral">
        <h1 className="kit-title">组件样张（正式组件）</h1>
        <p className="kit-lead">
          本页用 design-system 里的真实组件复现设计样张的原语部分，内容均为合成数据。与 ui-system-demo 的组件样张逐项对照：
          状态、尺寸、键盘与读屏名称。Button、IconButton、Tooltip、NavItem 属于基础层，这里只作为配套控件出现。
        </p>
        <FieldSection />
        <WritingSection />
        <ChoiceSection />
        <TabsSection />
        <TagSection />
        <CardSection />
        <MenuToastSection />
        <PageSection />
        <MiscSection />
      </main>
    </>
  );
}
