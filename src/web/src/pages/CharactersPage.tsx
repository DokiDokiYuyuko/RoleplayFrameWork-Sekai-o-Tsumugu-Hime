import "./characters-library.css";
import { Button, Checkbox, Select, TextInput, Portrait, Avatar, buttonClass } from "../design-system";
import { paginateCharacters, readCharacterPage, characterPageParams, characterFilterParams } from "../features/library/characterPagination";
import type { AssetListFilters } from "../features/library/assetList";
import { Plus, Pencil, MoreHorizontal, X, Search } from "../design-system/Icon";
import { PageArt } from "../appearance/PageArt";
import { Ornament } from "../appearance/Ornament";
import { PublicArt } from "../design-system/PublicArt";
import { useAppearanceAttribute } from "../design-system/internal/useAppearanceAttribute";
import { characterListScrollOwner, rememberCharacterListPosition, restoreCharacterListPosition } from "../features/library/characterListPosition";
import { resourceQueries } from '../features/resources/resourceQueries';
import { characterImport } from "../api/characterImport";
import { useLayoutEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { libraryClient as api } from '../features/library/libraryClient';
import { exportBundleUrl } from "../api/Api";
import { Modal } from "../components/common";
import { useCharacterStore } from "../store/characterStore";
import { useLorebookStore } from "../store/lorebookStore";
import type { BundleReport, Character, CharacterView } from "../types";
import { LoadState } from "../components/LoadState";
import {
  ImportCompatibilityReport,
  type ImportCompatibility,
} from "../components/ImportCompatibilityReport";
import {
  AssetListToolbar,
  useAssetListFilters,
  filterAndSortAssets,
  collectTags,
} from "../features/library/AssetListToolbar";
import {
  AssetSelectionBar,
  useAssetSelection,
} from "../features/library/AssetSelectionBar";
import { SelectedBundleExportDialog } from "../features/library/SelectedBundleExportDialog";
import {
  applyTagBatch,
  type TagBatchResult,
} from "../features/library/tagBatch";

export default function CharactersPage() {
  const navigate = useNavigate();
  const listElement = useRef<HTMLDivElement>(null);
  const restoredSearch = useRef<string | null>(null);
  const { characters, updateCharacter, removeCharacter } = useCharacterStore();
  const { loadStatus, loadError, load } = useCharacterStore();
  const [confirming, setConfirming] = useState<string | null>(null); // 二次确认中的角色 id
  const [report, setReport] = useState<BundleReport | null>(null); // F10.3 迁移包导入报告
  const [compatibility, setCompatibility] =
    useState<ImportCompatibility | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [notice, setNotice] = useState("");
  const { filters, search } = useAssetListFilters();
  const [params, setParams] = useSearchParams();
  const setFilters = (patch: Partial<AssetListFilters>) => setParams(current => characterFilterParams(current, patch), { replace: true });
  const { data: worlds = [] } = useQuery({
    ...resourceQueries.worlds(),
  });
  const selection = useAssetSelection(
    characters.map((character) => character.id),
  );
  const [exporting, setExporting] = useState(false);
  const [portrait, setPortrait] = useState<{ character: CharacterView; kind: "full-body" | "avatar" } | null>(null);
  const [batchTag, setBatchTag] = useState("");
  const [batchRemove, setBatchRemove] = useState(false);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchResults, setBatchResults] = useState<TagBatchResult[]>([]);
  const [batchError, setBatchError] = useState("");
  const [avatarVersion] = useState(() => Date.now());
  const filteredCharacters = filterAndSortAssets(
    characters,
    filters,
    (character) => ({
      id: character.id,
      name: character.card.name,
      description: character.card.description,
      aliases: character.aliases,
      tags: character.card.tags,
      worldId: character.source_world_id,
      updatedAt: character.updated_at,
    }),
  );
  const pagination = paginateCharacters(filteredCharacters, readCharacterPage(params));
  const pageCharacters = pagination.items;
  const pageIds = pageCharacters.map(character => character.id);
  const visibleIds = pageIds.join("\u0000");
  const changePage = (page: number) => {
    setParams(current => characterPageParams(current, page));
    if (listElement.current) characterListScrollOwner(listElement.current).scrollTo({ top: 0, left: 0, behavior: "auto" });
  };
  useLayoutEffect(() => {
    if (restoredSearch.current === search || !listElement.current || (!characters.length && loadStatus !== "ready")) return;
    restoreCharacterListPosition(listElement.current, search);
    restoredSearch.current = search;
  }, [search, visibleIds, loadStatus, characters.length]);
  const openCharacterEditor = (id: string | null) => {
    if (listElement.current) {
      rememberCharacterListPosition(listElement.current, search, id);
      characterListScrollOwner(listElement.current).scrollTo({ top: 0, left: 0, behavior: "auto" });
    }
    navigate(`/library/characters/${id ? encodeURIComponent(id) : "new"}${search ? "?" + search : ""}`);
  };

  const changeSelectedTags = async () => {
    if (batchBusy || !selection.selected.length) return;
    const selected = new Set(selection.selected);
    const targets = characters
      .filter((character) => selected.has(character.id))
      .map((character) => ({
        id: character.id,
        name: character.card.name,
        revision: character.revision ?? 1,
        tags: [...character.card.tags],
      }));
    setBatchBusy(true);
    setBatchError("");
    setBatchResults([]);
    try {
      const results = await applyTagBatch(
        targets,
        batchTag,
        batchRemove,
        (id, tags, revision) =>
          updateCharacter(id, { card: { tags }, expected_revision: revision }),
      );
      setBatchResults(results);
      selection.setSelected(
        results.filter((result) => !result.ok).map((result) => result.id),
      );
      if (results.some((result) => !result.ok))
        await load().catch(() => undefined);
    } catch (cause) {
      setBatchError(cause instanceof Error ? cause.message : "批量修改失败");
    } finally {
      setBatchBusy(false);
    }
  };

  const fileInput = useRef<HTMLInputElement>(null);


  const flashNotice = (text: string) => {
    setNotice(text);
    if (!text.includes("失败")) setTimeout(() => setNotice(current => current === text ? "" : current), 3000);
  };

  const doDelete = async (c: Character) => {
    setConfirming(null);
    try {
      await removeCharacter(c.id);
      flashNotice(`已删除角色「${c.card.name}」`);
    } catch {
      flashNotice(`删除失败：${c.card.name}`);
    }
  };

  const handleFiles = async (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      // F10.3 迁移包：zip 走整包导入（角色/头像/世界书 + 逐项报告）
      if (file.name.toLowerCase().endsWith(".zip")) {
        try {
          const rep = await api.importBundle(file);
          setReport(rep);
          await useCharacterStore.getState().load();
          await useLorebookStore.getState().load();
          flashNotice(
            `迁移包导入：${rep.characters.length} 个角色 · ${rep.lorebooks.length} 本书`,
          );
        } catch {
          setReport(null);
          flashNotice(`迁移包导入失败：${file.name}`);
        }
        continue;
      }
      try {
        if (!/\.(png|json)$/i.test(file.name))
          throw new Error("只支持 PNG 或 JSON 角色卡");
        setCompatibility(await characterImport.preview(file));
        const created = await characterImport.import(file);
        await Promise.all([
          useCharacterStore.getState().load(),
          useLorebookStore.getState().load(),
        ]);
        flashNotice(`已导入角色「${created.card.name}」`);
      } catch (cause) {
        flashNotice(
          `导入失败：${file.name} · ${cause instanceof Error ? cause.message : "文件无效"}`,
        );
      }
    }
  };

  return (
    <div className="workspace-page character-library-page" ref={listElement}>
      <div className="workspace-body space-y-6">
        <div className="character-library-heading moonweave-frame" data-region="character">
          <Ornament tone="rose" />
          <PageArt kind="characters" className="character-library-art" />
          <div className="character-library-title">
            <h1 className="text-sm font-semibold text-slate-700">角色档案</h1>
            <p className="text-xs text-slate-400">
              {characters.length} 个角色 · 新建空白角色，或导入角色卡（PNG /
              JSON）
            </p>
          </div>
          <div className="character-library-actions">
            <Button type="button" skin="secondary" onClick={() => fileInput.current?.click()}>导入角色卡</Button>
            <div className="character-library-create">            <Button
              type="button"
              onClick={() => openCharacterEditor(null)}
              data-testid="character-create"
              variant="primary" skin="primary"
            >
              <Plus size={16} aria-hidden="true" /> 新建角色
            </Button></div>
            <details className="character-library-tools"><summary className="mw-menu-trigger" aria-label="角色库工具"><MoreHorizontal size={18} aria-hidden="true" /></summary><div>            <Button
              type="button"
              onClick={() => navigate("/library/imports?target=character")}
              
            >
              从设定生成
            </Button>
            <Button type="button"  onClick={selection.start}>批量选择</Button>
</div></details>
          </div>
        </div>

        {notice && <p role={notice.includes("失败") ? "alert" : "status"} className={notice.includes("失败") ? "v7-error" : "character-library-notice"}>{notice}</p>}
        <AssetListToolbar
          label="角色"
          filters={filters}
          onChange={setFilters}
          tags={collectTags(
            characters.map((character) => ({ tags: character.card.tags })),
          )}
          count={filteredCharacters.length}
          worlds={worlds}
        />
        {selection.selecting && <AssetSelectionBar
          selecting={selection.selecting}
          selected={selection.selected}
          visible={pageIds}
          onStart={selection.start}
          onExit={selection.exit}
          onClear={selection.clear}
          onToggleVisible={() =>
            selection.toggleVisible(
              pageIds,
            )
          }
          busy={batchBusy}
        >
          <Button
            type="button"
            disabled={batchBusy || !selection.selected.length}
            onClick={() => setExporting(true)}
          >
            导出所选
          </Button>
          <TextInput
            aria-label="批量角色标签"
            placeholder="填写标签"
            maxLength={120}
            value={batchTag}
            disabled={batchBusy}
            onChange={(event) => setBatchTag(event.target.value)}
          />
          <Select aria-label="批量标签操作" value={batchRemove ? "remove" : "add"} disabled={batchBusy}
            onValueChange={(value) => setBatchRemove(value === "remove")}
            width="auto" options={[{ value: "add", label: "添加标签" }, { value: "remove", label: "移除标签" }]} />
          <Button
            type="button"
            skin={false}
            aria-busy={batchBusy}
            disabled={
              batchBusy || !batchTag.trim() || !selection.selected.length
            }
            onClick={() => void changeSelectedTags()}
          >
            {batchBusy
              ? "逐项保存中…"
              : `应用到所选 ${selection.selected.length} 项`}
          </Button>
        </AssetSelectionBar>}
        {batchError && (
          <p role="alert" className="v7-error">
            {batchError}
          </p>
        )}
        {batchResults.length > 0 && (
          <div className="asset-batch-results">
            <p role="status">
              已保存 {batchResults.filter((result) => result.ok).length}{" "}
              项，失败 {batchResults.filter((result) => !result.ok).length}{" "}
              项。失败项仍被选中。
            </p>
            {batchResults
              .filter((result) => !result.ok)
              .map((result) => (
                <p className="is-error" key={result.id}>
                  {result.name}：{result.error}
                </p>
              ))}
          </div>
        )}

        <details className="v7-library-import">
          <summary>导入与私密迁移</summary>
          <a
            href={exportBundleUrl}
            data-testid="bundle-export"
            className={buttonClass({ size: "sm" })}
            title="私密迁移：全部角色、头像、角色创作原稿与世界书；不包含世界、故事或全身图，来源世界需重新指定"
          >
            下载私密迁移包（含角色创作原稿）
          </a>
          <p className="asset-export-scope">
            私密迁移保留全部角色、头像、角色创作原稿与世界书。世界、故事与全身图不在此包内，来源世界关系需重新指定。对外分享请使用“导出所选”。
          </p>
          <ImportDropzone
            dragOver={dragOver}
            notice={notice}
            onDragState={(v) => setDragOver(v)}
            onFiles={handleFiles}
            onPick={() => fileInput.current?.click()}
            fileInputRef={fileInput}
          />
        </details>
        <ImportCompatibilityReport
          report={compatibility}
          title="最近一次角色卡导入行为核对"
        />

        {report && (
          <div
            className="rounded-xl border border-slate-200 bg-white p-4"
            data-testid="bundle-report"
          >
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-700">
                迁移包导入报告：{report.characters.length} 个角色 ·{" "}
                {report.lorebooks.length} 本书
                {report.errors.length > 0 && (
                  <span className="ml-2 text-rose-500">
                    {report.errors.length} 条失败
                  </span>
                )}
              </h3>
              <Button
                type="button"
                aria-label="关闭迁移包导入报告"
                onClick={() => setReport(null)}
                
              >
                <X size={16} aria-hidden="true" />
              </Button>
            </div>
            {report.characters.length > 0 && (
              <ul className="mt-2 flex flex-wrap gap-1.5 text-[11px] text-slate-500">
                {report.characters.map((c) => (
                  <li
                    key={c.id}
                    className="rounded-full bg-slate-100 px-2 py-0.5"
                  >
                    角色 · {c.name}
                  </li>
                ))}
              </ul>
            )}
            {report.characters.flatMap((character) =>
              (character.warnings ?? []).map((warning) => (
                <p
                  key={`${character.id}:${warning}`}
                  className="asset-export-scope"
                >
                  {character.name}：{warning}
                </p>
              )),
            )}
            {report.lorebooks.length > 0 && (
              <ul className="mt-1.5 flex flex-wrap gap-1.5 text-[11px] text-slate-500">
                {report.lorebooks.map((b) => (
                  <li
                    key={b.id}
                    className="rounded-full bg-indigo-50 px-2 py-0.5 text-indigo-600"
                  >
                    世界书 · {b.name}
                  </li>
                ))}
              </ul>
            )}
            {report.errors.length > 0 && (
              <ul className="mt-1.5 space-y-0.5 text-[11px] text-rose-500">
                {report.errors.map((e, i) => (
                  <li key={i}>
                    {e.filename}：{e.error}
                  </li>
                ))}
              </ul>
            )}
            {[...report.characters, ...report.lorebooks].map((item) => (
              <ImportCompatibilityReport
                key={item.id}
                report={item.import_report}
                title={`${item.name} · 导入行为核对`}
              />
            ))}
          </div>
        )}

        {loadError && (
          <LoadState
            title="角色库读取失败"
            error={loadError}
            onRetry={() => void load().catch(() => undefined)}
          />
        )}
        {characters.length === 0 &&
          (loadStatus === "loading" || loadStatus === "idle") && (
            <LoadState title="正在读取角色库" />
          )}
        {characters.length === 0 && loadStatus === "ready" && (
          <div className="rounded-xl border-2 border-dashed border-slate-300 bg-white py-12 text-center">
            <p className="text-sm text-slate-500">角色库还是空的</p>
            <Button
              type="button"
              onClick={() => openCharacterEditor(null)}
              variant="primary" skin="primary"
            >
              <Plus size={16} aria-hidden="true" /> 新建第一个角色
            </Button>
          </div>
        )}

        {characters.length > 0 && filteredCharacters.length === 0 && (
          <div className="v7-empty">
            没有找到匹配的角色。试试其他名称或关键词。
          </div>
        )}

        <div className="workspace-card-grid character-library-grid">
          {pageCharacters.map((c) => (
            <div
              key={c.id}
              data-testid="character-card"
              className="character-dossier"
            >
              {selection.selecting && (
                <Checkbox className="asset-selection-check" label="选择" aria-label={`选择角色 ${c.card.name}`}
                  disabled={batchBusy} checked={selection.selected.includes(c.id)} onChange={() => selection.toggle(c.id)} />
              )}
              <div className="character-card-identity character-dossier-body">
                <CharacterImage kind="full-body" key={`${c.id}-${c.revision}-${avatarVersion}`} character={c} imageVersion={`${avatarVersion}-${c.revision ?? 1}`} onPreview={() => setPortrait({ character: c, kind: "full-body" })} />
                <div className="character-dossier-copy min-w-0">
                  <div className="character-dossier-identity">
                    <div className="character-dossier-title-group">
                    <h3 className="truncate font-semibold text-slate-800">
                      {c.card.name}
                    </h3>
                    {c.muted && (
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                        已静音
                      </span>
                    )}
                    {!c.present && (
                      <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-600">
                        离席
                      </span>
                    )}
                  <CharacterCardDecoration kind="nameplate" />
                  {c.aliases.length > 0 && (
                    <p className="truncate text-xs text-slate-400">
                      别名：{c.aliases.join(" / ")}
                    </p>
                  )}
                  {c.card.tags.length > 0 && <div className="asset-tags-row">
                    {c.card.tags.slice(0, 3).map((tag) => (
                      <Button
                        type="button" variant="ghost" size="sm" className="asset-tag-chip"
                        key={tag}
                        onClick={() => setFilters({ tag })}
                      >
                        {tag}
                      </Button>
                    ))}
                    {c.card.tags.length > 3 && <details className="character-extra-tags"><summary>+{c.card.tags.length - 3}</summary><div>{c.card.tags.slice(3).map(tag => <Button key={tag} variant="ghost" size="sm" onClick={() => setFilters({ tag })}>{tag}</Button>)}</div></details>}
                  </div>}
                    </div>
                    <CharacterImage kind="avatar" key={`${c.id}-${c.revision}-${avatarVersion}-avatar`} character={c} imageVersion={`${avatarVersion}-${c.revision ?? 1}`} onPreview={() => setPortrait({ character: c, kind: "avatar" })} />
                  </div>
                  <div className="character-dossier-reading" tabIndex={0} role="region" aria-label={`${c.card.name}的资料`}>
                  {c.card.description?.trim() && <p className="character-dossier-description">
                    {c.card.description}
                  </p>}
                  <dl className="character-dossier-facts">
                    {[
                      ["性格", c.card.personality],
                      [c.card.traits_label?.trim() || "能力与特质", c.card.traits],
                      ["外貌", c.card.appearance],
                      ["背景", c.card.scenario],
                    ].filter(([, value]) => value?.trim()).map(([label, value], index) => (
                      <div key={index}><dt>{label}</dt><dd>{value}</dd></div>
                    ))}
                  </dl>
                  {c.source_world_id && <Button
                    type="button"
                    variant="ghost" size="sm" className="asset-source-world"
                    onClick={() =>
                      setFilters({ world: c.source_world_id ?? "unassigned" })
                    }
                  >
                    来源世界：
                    {worlds.find((world) => world.id === c.source_world_id)?.title ?? "世界已不可用"}
                  </Button>}
                  </div>

              <div className="character-dossier-footer">
                {c.updated_at && Number.isFinite(Date.parse(c.updated_at)) && <time className="character-dossier-updated" dateTime={c.updated_at}>更新于 {new Date(c.updated_at).toLocaleDateString("zh-CN")}</time>}
                <Button
                  type="button"
                  data-character-edit={c.id}
                  onClick={() => openCharacterEditor(c.id)}
                  size="sm" className="character-dossier-edit"
                >
                  <Pencil size={14} aria-hidden="true" /> 编辑
                </Button>
                <details className="character-card-actions"><summary className="mw-menu-trigger" aria-label={`更多角色操作：${c.card.name}`}><MoreHorizontal size={18} aria-hidden="true" /></summary><div>
                <details className="character-card-more"><summary>完整角色资料</summary>
                  <p>{c.card.description}</p><p>别名：{c.aliases.join(" / ") || "—"}</p>
                  <dl>{[["性格", c.card.personality], [c.card.traits_label?.trim() || "能力与特质", c.card.traits], ["外貌", c.card.appearance], ["背景", c.card.scenario]].filter(([, value]) => value?.trim()).map(([label, value], index) => <div key={index}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
                  <ImportCompatibilityReport report={c.card.import_report} />
                </details>
                <details className="character-card-more">
                <summary>运行偏好与模型</summary>
                <div className="mt-3 flex items-center gap-2 text-xs">
                  <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-slate-600">
                    {c.llm.model}
                  </span>
                  <span className="text-slate-400">
                    max_tokens={String(c.llm.sampling?.max_tokens ?? "—")}
                  </span>
                </div>

                <div className="mt-3">
                  <div className="mb-1 flex justify-between text-[10px] text-slate-400">
                    <span>主动性 talkativeness</span>
                    <span>{Math.round(c.talkativeness * 100)}%</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-indigo-400"
                      style={{ width: `${c.talkativeness * 100}%` }}
                    />
                  </div>
                </div>

                {/* R38 主动性开关（库级默认，新会话继承；会话内可在聊天页成员面板改） */}
                <div className="mt-3 flex flex-col gap-1 text-[10px] text-slate-400">
                  {(
                    [
                      ["interject_enabled", "主动插话", c.interject_enabled],
                      ["followup_enabled", "接话", c.followup_enabled],
                    ] as const
                  ).map(([field, label, on]) => (
                    <Checkbox key={field} label={`${label}（默认${on ? "开" : "关"}）`} checked={on}
                      onChange={async (e) => { await updateCharacter(c.id, { [field]: e.target.checked }); }} />
                  ))}
                </div>
              </details>

                {confirming === c.id ? (
                  <>
                    <Button
                      type="button"
                      onClick={() => void doDelete(c)}
                      data-testid="character-delete-confirm"
                      variant="danger"
                    >
                      确认删除
                    </Button>
                    <Button
                      type="button"
                      onClick={() => setConfirming(null)}
                      
                    >
                      取消
                    </Button>
                  </>
                ) : (
                  <Button
                    type="button"
                    onClick={() => setConfirming(c.id)}
                    data-testid="character-delete"
                    title="删除角色（卡片 / 头像 / 该角色记忆）"
                    variant="danger"
                  >
                    删除
                  </Button>
                )}
                </div></details>
              </div>
              {confirming === c.id && (
                <p className="mt-2 text-[10px] leading-relaxed text-rose-500">
                  将删除该角色的卡片、头像与该角色的全部记忆；已开会话中的角色副本不受影响。
                </p>
              )}
                </div>
              </div>
            </div>
          ))}
        </div>
        {pagination.total > 0 && <nav className="character-library-pagination" aria-label="角色库分页">
          <span role="status">第 {pagination.page} / {pagination.pages} 页 · {pagination.start + 1}–{pagination.end} / {pagination.total} 个角色</span>
          <Button size="sm" disabled={batchBusy || pagination.page === 1} onClick={() => changePage(pagination.page - 1)}>上一页</Button>
          <Button size="sm" disabled={batchBusy || pagination.page === pagination.pages} onClick={() => changePage(pagination.page + 1)}>下一页</Button>
        </nav>}
      </div>

      {portrait && <Modal wide title={`${portrait.character.card.name}的${portrait.kind === "full-body" ? "全身立绘" : "头像"}`} onClose={() => setPortrait(null)}><CharacterPortraitPreview character={portrait.character} kind={portrait.kind} imageVersion={`${avatarVersion}-${portrait.character.revision ?? 1}`} /></Modal>}

      {exporting && (
        <SelectedBundleExportDialog
          characters={characters
            .filter((character) => selection.selected.includes(character.id))
            .map((character) => ({
              id: character.id,
              expected_revision: character.revision ?? 1,
            }))}
          lorebooks={[]}
          onClose={() => setExporting(false)}
        />
      )}

    </div>
  );
}

/** Decoration remains separate from user portraits and follows live appearance switches. */
function CharacterCardDecoration({ kind }: { kind: "nameplate" | "edge" }) {
  const rich = useAppearanceAttribute("data-ornament", "subtle") === "rich";
  const enabled = useAppearanceAttribute("data-card-ornaments", "true") !== "false";
  const night = useAppearanceAttribute("data-color-scheme", "light") === "dark";
  if (!rich || !enabled) return null;
  return <PublicArt path={`cards/${kind === "nameplate" ? "nameplate-left" : "portrait-edge"}${night ? "-night" : ""}.webp`}
    className={`character-card-decoration character-card-decoration--${kind}`} aria-hidden="true" />;
}

function characterImageUrl(id: string, kind: "full-body" | "avatar", version: string) {
  return `/api/v1/characters/${encodeURIComponent(id)}/${kind}?v=${version}`;
}

function CharacterImage({ character, kind, imageVersion, onPreview }: { character: CharacterView; kind: "full-body" | "avatar"; imageVersion: string; onPreview: () => void }) {
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const source = characterImageUrl(character.id, kind, imageVersion);
  const fullBody = kind === "full-body";
  const label = fullBody ? "全身立绘" : "头像";
  const handlers = {
    onLoad: (event: React.SyntheticEvent<HTMLSpanElement>) => { if ((event.target as HTMLImageElement).src?.includes(`/${kind}?`)) setReady(true); },
    onError: (event: React.SyntheticEvent<HTMLSpanElement>) => { if ((event.target as HTMLImageElement).src?.includes(`/${kind}?`)) { setFailed(true); setReady(false); } },
  };
  return <Button type="button" className={fullBody ? "character-dossier-portrait" : "character-dossier-avatar"} skin={false} disabled={!ready} onClick={onPreview}
    aria-label={ready ? `查看${character.card.name}的${label}` : `${character.card.name}的${fullBody ? "立绘占位" : "首字头像"}`}
    title={ready ? `查看原始${label}` : `尚无可查看的${label}`}
    data-image-kind={kind} data-image-state={ready ? "ready" : failed ? "missing" : "loading"}>
    {fullBody ? <><Portrait name={character.card.name} src={source} tone="rose" showInitial={false} {...handlers} /><CharacterCardDecoration kind="edge" /></>
      : <Avatar name={character.card.name} src={source} size={56} {...handlers} />}
    {ready && <span className="character-image-zoom" aria-hidden="true"><Search size={16} /></span>}
  </Button>;
}

function CharacterPortraitPreview({ character, kind, imageVersion }: { character: CharacterView; kind: "full-body" | "avatar"; imageVersion: string }) {
  const [failed, setFailed] = useState(false);
  const label = kind === "full-body" ? "全身立绘" : "头像";
  return <div className="character-portrait-preview">{failed ? <p role="status">{label}读取失败，请关闭后重新读取角色库。</p> : <><img src={characterImageUrl(character.id, kind, imageVersion)} alt={`${character.card.name}的原始${label}`} onError={() => setFailed(true)} /><p>按原比例展示</p></>}</div>;
}

function ImportDropzone({
  dragOver,
  notice,
  onDragState,
  onFiles,
  onPick,
  fileInputRef,
}: {
  dragOver: boolean;
  notice: string;
  onDragState: (v: boolean) => void;
  onFiles: (files: FileList) => void;
  onPick: () => void;
  fileInputRef: React.RefObject<HTMLInputElement>;
}) {
  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          onDragState(true);
        }}
        onDragLeave={() => onDragState(false)}
        onDrop={(e) => {
          e.preventDefault();
          onDragState(false);
          if (e.dataTransfer.files.length > 0) onFiles(e.dataTransfer.files);
        }}
        role="button"
        tabIndex={0}
        aria-label="导入角色卡或迁移包"
        onClick={(event) => {
          if (event.target !== fileInputRef.current) onPick();
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onPick();
          }
        }}
        className="character-import-dropzone" data-drag-over={dragOver || undefined}
      >
        <p>拖入角色卡或迁移包导入（PNG / JSON / ZIP）</p>
        <p className="mt-1 text-xs">
          PNG 解析 tEXt 块（ccv3 优先），JSON 支持平铺与 data 包裹；ZIP =
          本应用导出的迁移包
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept=".png,.json,.zip"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files && e.target.files.length > 0)
              onFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
      {notice && (
        <p className="mt-2 text-center text-xs text-indigo-500">{notice}</p>
      )}
    </div>
  );
}
