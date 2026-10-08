import test from "node:test";
import assert from "node:assert/strict";
import { load } from "./storySupport.mjs";
const assets = load("../src/features/library/assetList.ts");
const pages = load("../src/features/library/characterPagination.ts", { "./assetList": assets });

test("six-item pages cover all records and clamp invalid or deleted last pages", () => {
  const items = Array.from({ length: 13 }, (_, i) => `synthetic-${i}`);
  assert.deepEqual([1,2,3].flatMap(page => pages.paginateCharacters(items,page).items), items);
  assert.equal(pages.paginateCharacters(items,99).page,3);
  assert.equal(pages.paginateCharacters(items.slice(0,6),3).page,1);
  assert.equal(pages.paginateCharacters([],2).page,1);
  for (const value of ["0","-1","1.5","NaN","9999999999999999999999"]) assert.equal(pages.readCharacterPage(new URLSearchParams({ characterPage:value })),1);
});
test("filter and sort reset pagination while navigation preserves filters", () => {
  const params = new URLSearchParams("q=keeper&sort=name&characterPage=3&other=kept");
  assert.equal(pages.characterPageParams(params,2).get("q"),"keeper");
  const next = pages.characterFilterParams(params,{ tag:"test" });
  assert.equal(next.get("characterPage"),null); assert.equal(next.get("sort"),"name"); assert.equal(next.get("other"),"kept");
  assert.equal(params.get("characterPage"),"3");
});
function harness(search = "", appearance = {}) {
  const slots=[]; let cursor=0; let params=new URLSearchParams(search); let tree;
  const events=[];
  const react={ useState(initial) { const i=cursor++; if (!(i in slots)) slots[i]=typeof initial === "function" ? initial():initial; return [slots[i],value=>{ slots[i]=typeof value === "function" ? value(slots[i]):value; }]; },
    useRef(initial) { const i=cursor++; if (!(i in slots)) slots[i]={current:initial}; return slots[i]; }, useEffect() {}, useLayoutEffect() {} };
  const selection=load("../src/features/library/AssetSelectionBar.tsx", { react,"./assetList":assets,"./library-tools.css":{}, "../../design-system":{Button:"button"} });
  const characters=Array.from({length:13},(_,i)=>({id:`synthetic-${i}`,revision:1,updated_at:`2026-01-${String(20-i).padStart(2,"0")}`,aliases:[],present:true,muted:false,llm:{model:"synthetic"},talkativeness:.5,card:{name:`角色${i}`,description:"合成测试资料",tags:[i<7?"a":"b"],personality:"",traits:"",appearance:"",scenario:""}}));
  const store={characters,loadStatus:"ready",loadError:null,load:async()=>{},updateCharacter:async()=>{},removeCharacter:async()=>{}};
  const useCharacterStore=()=>store; useCharacterStore.getState=()=>store;
  const controls={Button:"button",Checkbox:"checkbox",Select:"select",TextInput:"input",Portrait:"portrait",Avatar:"avatar",buttonClass:()=>"ui-btn"};
  const {default:Page}=load("../src/pages/CharactersPage.tsx",{
    react,"react-router":{useNavigate:()=>path=>events.push(["navigate",path]),useSearchParams:()=>[params,fn=>{params=typeof fn==="function"?fn(params):fn;}]},
    "@tanstack/react-query":{useQuery:()=>({data:[]})},"../design-system":controls,"../design-system/Icon":{Plus:"icon",Pencil:"icon",MoreHorizontal:"icon",X:"icon",Search:"icon"},
    "../features/library/characterPagination":pages,"../features/library/assetList":assets,"./characters-library.css":{},"../appearance/PageArt":{PageArt:"art"},"../appearance/Ornament":{Ornament:"ornament"},"../design-system/PublicArt":{PublicArt:"public-art"},"../design-system/internal/useAppearanceAttribute":{useAppearanceAttribute:(name,fallback)=>appearance[name] ?? fallback},
    "../features/library/characterListPosition":{rememberCharacterListPosition:(_list,search,id)=>events.push(["remember",search,id]),restoreCharacterListPosition(){},characterListScrollOwner:()=>({scrollTo(){}})},
    "../features/resources/resourceQueries":{resourceQueries:{worlds:()=>({})}},"../api/characterImport":{characterImport:{}},"../features/library/libraryClient":{libraryClient:{}},"../api/Api":{exportBundleUrl:"/synthetic-export"},"../components/common":{Modal:"modal"},
    "../store/characterStore":{useCharacterStore},"../store/lorebookStore":{useLorebookStore:{}},"../components/LoadState":{LoadState:"load"},"../components/ImportCompatibilityReport":{ImportCompatibilityReport:"report"},
    "../features/library/AssetListToolbar":{AssetListToolbar:"toolbar",useAssetListFilters:()=>({filters:assets.readAssetFilters(params),search:params.toString()}),...assets},
    "../features/library/AssetSelectionBar":{AssetSelectionBar:"selection",useAssetSelection:selection.useAssetSelection},"../features/library/SelectedBundleExportDialog":{SelectedBundleExportDialog:"export"},"../features/library/tagBatch":{applyTagBatch:async()=>[]}
  });
  const visit=(node,predicate,found=[])=>{ if (!node||typeof node!=="object")return found; if(Array.isArray(node)){for(const n of node)visit(n,predicate,found);return found;} if(predicate(node))found.push(node);visit(node.props?.children,predicate,found);return found; };
  return {render(){cursor=0;tree=Page();tree.ref.current={};},find(predicate){const values=visit(tree,predicate);assert.ok(values.length);return values[0];},child(node){cursor=node.type?.name==="CharacterPortraitPreview"?3000:2000;return node.type(node.props);},all(predicate){return visit(tree,predicate);},cards(){return visit(tree,n=>n.props?.["data-testid"]==="character-card").map(n=>n.key);},events,get search(){return params.toString();}};
}
test("production page renders six records, selects across pages and filters without losing selection",()=>{
  const h=harness();h.render();assert.equal(h.cards().length,6);
  h.find(n=>n.type==="button"&&n.props.children==="批量选择").props.onClick();h.render();
  h.find(n=>n.type==="checkbox"&&n.props["aria-label"]==="选择角色 角色0").props.onChange();h.render();
  h.find(n=>n.type==="button"&&n.props.children==="下一页").props.onClick();h.render();assert.equal(h.cards().length,6);assert.equal(new URLSearchParams(h.search).get("characterPage"),"2");
  h.find(n=>n.type==="checkbox"&&n.props["aria-label"]==="选择角色 角色6").props.onChange();h.render();
  assert.deepEqual(h.find(n=>n.type==="selection").props.selected,["synthetic-0","synthetic-6"]);
  h.find(n=>n.type==="selection").props.onToggleVisible();h.render();assert.equal(h.find(n=>n.type==="selection").props.selected.length,7);
  h.find(n=>n.type==="toolbar").props.onChange({tag:"b"});h.render();assert.equal(new URLSearchParams(h.search).get("characterPage"),null);assert.equal(h.cards().length,6);assert.equal(h.find(n=>n.type==="selection").props.selected.length,7);
});
test("editor route carries page/filter identity and remounted list returns to the same page",()=>{
  const h=harness("characterPage=2&sort=name");h.render();const ids=h.cards();
  const edit=h.find(n=>n.props?.["data-character-edit"]);edit.props.onClick();
  assert.ok(h.events[0][1].includes("characterPage=2"));assert.ok(h.events[1][1].includes("characterPage=2"));
  const restored=harness(h.events[1][1].split("?")[1]);restored.render();assert.deepEqual(restored.cards(),ids);
});



test("reviewed card artwork follows rich, card ornament toggle and live dark theme",()=>{
  const probe=(appearance)=>{const h=harness("",appearance);h.render();const art=h.find(n=>n.type?.name==="CharacterCardDecoration");return art.type(art.props);};
  assert.equal(probe({"data-ornament":"subtle"}),null);
  assert.equal(probe({"data-ornament":"none"}),null);
  assert.equal(probe({"data-ornament":"rich","data-card-ornaments":"false"}),null);
  assert.equal(probe({"data-ornament":"rich"}).props.path,"cards/nameplate-left.webp");
  assert.equal(probe({"data-ornament":"rich","data-color-scheme":"dark"}).props.path,"cards/nameplate-left-night.webp");
});

test("library full-body and avatar bind separate sources, fallback identities and matching previews",()=>{
  for (const state of ["both","avatar-only","full-body-only","neither","failure"]) {
    const h=harness();h.render();
    const images=h.all(n=>n.type?.name==="CharacterImage").filter(n=>n.props.character.id==="synthetic-0");
    assert.equal(images.length,2);
    for (const node of images) {
      const kind=node.props.kind; const loaded=state==="both"||(state==="avatar-only"&&kind==="avatar")||(state==="full-body-only"&&kind==="full-body");
      // Distinct child hook scope models each independently mounted image.
      const isolated=harness();isolated.render();const child=isolated.all(n=>n.type?.name==="CharacterImage"&&n.props.kind===kind)[0];
      let button=isolated.child(child);
      const walk=(n,p)=>{if(!n||typeof n!=="object")return;if(Array.isArray(n)){for(const item of n){const hit=walk(item,p);if(hit)return hit;}return;}if(p(n))return n;return walk(n.props?.children,p);};
      const media=walk(button,n=>n.type==="portrait"||n.type==="avatar");
      assert.ok(media.props.src.includes(`/${kind}?`));
      if(kind==="full-body")assert.equal(media.props.showInitial,false);
      else { assert.equal(media.props.dense,undefined); assert.equal(media.props.size,56); }
      media.props[loaded?"onLoad":"onError"]({target:{src:media.props.src}});button=isolated.child(child);
      assert.equal(button.props.disabled,!loaded);assert.equal(button.props["data-image-state"],loaded?"ready":"missing");
      if(loaded){button.props.onClick();isolated.render();const modal=isolated.find(n=>n.type==="modal");assert.ok(modal.props.title.endsWith(kind==="full-body"?"全身立绘":"头像"));const preview=modal.props.children;assert.equal(preview.props.kind,kind);const content=isolated.child(preview);assert.ok(walk(content,n=>n.type==="img").props.src.includes(`/${kind}?`));}
    }
  }
});


test("Portrait optional initial preserves other callers and retains plate without library badge",()=>{
  const {Portrait}=load("../src/design-system/Portrait.tsx",{
    react:{forwardRef:fn=>fn,useState:value=>[value,()=>{}]},"./PublicArt":{PublicArt:"plate"},"./internal/useAppearanceAttribute":{useAppearanceAttribute:()=>"art"},"./internal/cx":{cx:(...values)=>values.join(" ")},"./primitives/portrait.css":{}
  });
  const children=(node)=>{if(!node||typeof node!=="object")return [];if(Array.isArray(node))return node.flatMap(children);return [node,...children(node.props?.children)];};
  const normal=children(Portrait({name:"合成角色"},null));assert.ok(normal.some(n=>n.props?.className==="ui-portrait__initial"));
  const library=children(Portrait({name:"合成角色",showInitial:false},null));assert.equal(library.some(n=>n.props?.className==="ui-portrait__initial"),false);assert.ok(library.some(n=>n.type==="plate"));
});
