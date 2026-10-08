import { SceneImage, SceneImagePicker } from './SceneImagePicker';
import { Panel } from '../../design-system/Panel';
import { Button } from '../../design-system/Button';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { BookOpen, Globe2, Users, X } from '../../design-system/Icon';
import { Avatar } from '../../components/common';
import { resourceQueries } from '../resources/resourceQueries';
import { useChatStore } from '../../store/chatStore';
import { useActiveCharacters } from '../../store/useActiveCharacters';

/** A read-only view of the current story snapshot; management keeps its existing command owner. */
export function StoryContextPanel({ onClose, onOpenTools }: { onClose: () => void; onOpenTools: () => void }) {
  const scene = useChatStore((state) => state.activeScene);
  const session = useChatStore((state) => state.sessions.find((item) => item.id === state.currentSessionId));
  const loaded = useChatStore((state) => state.snapshotLoadedSessionId === state.currentSessionId);
  const groups = useChatStore((state) => state.sessionGroups);
  const characters = useActiveCharacters();
  const sourceWorldId = session?.source_world_id;
  const worlds = useQuery({ ...resourceQueries.worlds(), enabled: Boolean(sourceWorldId) });
  const world = worlds.data?.find((item) => item.id === sourceWorldId);
  const present = loaded ? characters.filter((character) => character.present && (!scene || scene.member_ids.includes(character.id))) : [];
  const presentGroups = loaded ? groups.filter((group) => group.status === 'active' && (!scene || group.scene_id === scene.id)) : [];

  return <Panel asChild><aside id="story-context" className="story-context-rail" aria-label="故事资料">
    <div className="story-context-heading"><span>故事资料</span><Button variant="ghost" type="button" className="mw-tool-button" aria-label="收起故事资料" onClick={onClose}><X size={16} /></Button></div>
    <section className="story-context-section">
      <h2><BookOpen size={18} /> 当前场景</h2>
      {loaded && scene && session ? <><SceneImage key={scene.id} scene={scene} /><h3>{scene.title}</h3>{scene.description && <p className="story-context-prose">{scene.description}</p>}<SceneImagePicker key={`${session?.id}:${scene.id}`} scene={scene} branchId={session!.id} revision={session?.branch_revision ?? 0} /></> : <p>{loaded ? '暂未设置场景' : '正在读取当前场景…'}</p>}
      <Button variant="ghost" type="button" className="story-context-detail mw-tool-button" onClick={onOpenTools}>查看导演与场景</Button>
    </section>
    <section className="story-context-section">
      <h2><Users size={18} /> 在场人物</h2>
      {present.map((character) => <div key={character.id} className="story-context-person">
        <Avatar name={character.card.name} color={character.color} size="sm" characterId={character.id} imageVersion={character.updated_at} />
        <div><strong>{character.card.name}</strong>{character.card.description && <small title={character.card.description}>{character.card.description}</small>}</div>
        <span className="story-context-state">{character.muted ? '静音' : '在场'}</span>
      </div>)}
      {presentGroups.map((group) => <div key={group.id} className="story-context-person"><Avatar name={group.label} color="emerald" size="sm" /><div><strong>{group.label}{group.count == null ? '' : ` ×${group.count}`}</strong>{group.current_state && <small>{group.current_state}</small>}</div><span className="story-context-state">群体</span></div>)}
      {present.length === 0 && presentGroups.length === 0 && <p>{loaded ? '当前没有在场人物' : '正在读取在场人物…'}</p>}
      <Button variant="ghost" type="button" className="story-context-detail mw-tool-button" onClick={onOpenTools}>管理人物与群体</Button>
    </section>
    <section className="story-context-section">
      <h2><Globe2 size={18} /> 世界资料</h2>
      {sourceWorldId ? <><h3>{world?.title ?? (worlds.isError ? '来源世界暂不可读取' : worlds.isPending ? '正在读取来源世界…' : '来源世界已不可用')}</h3>
        {session?.world_core_brief ? <p className="story-context-prose">{session.world_core_brief}</p> : <p>当前故事没有保存世界核心资料。</p>}
        {session?.source_world_revision != null && <small>故事采用的世界版本 · {session.source_world_revision}</small>}
        <Link className="story-context-detail" to={`/worlds/${encodeURIComponent(sourceWorldId)}`}>查看来源世界</Link>
      </> : <p>当前故事未关联世界</p>}
    </section>
  </aside></Panel>;
}
