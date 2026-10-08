import { useState } from 'react';
import { Link } from 'react-router';
import { ChevronDown, Pencil, Plus, Trash2 } from 'lucide-react';
import { Avatar, Button, TextInput } from '../../design-system';
import type { SimpleChatSummary } from '../../types';
import { chatTitle, chatDateGroup, chatRelativeTime, modelShortName, modelFamily } from './simpleChatPresentation.js';

export function SimpleChatList({ list, chatId, loading, query, onQuery, onCreate, onRename, onDelete, busy, error, onRetry }: {
  list: SimpleChatSummary[]; chatId?: string; loading: boolean; query: string;
  onQuery: (value: string) => void; onCreate: () => void;
  onRename: (item: SimpleChatSummary) => void; onDelete: (item: SimpleChatSummary) => void; busy: boolean; error?: string; onRetry: () => void;
}) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const visible = list.filter(item => chatTitle(item.title).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const sorted = [...visible].sort((a, b) => (Date.parse(b.updated_at) || 0) - (Date.parse(a.updated_at) || 0));
  return <aside className="sc-list" aria-label="聊天列表">
    <div className="sc-list-head"><div><h1>简单聊天</h1><p>直接与模型对话</p></div>
      <Button size="sm" disabled={busy} onClick={onCreate} aria-label="新建聊天"><Plus size={16} /> 新建</Button>
    </div>
    <TextInput className="sc-list-search" aria-label="搜索聊天" placeholder="搜索聊天标题" value={query} onChange={event => onQuery(event.target.value)} />
    {error && <div className="sc-list-error" role="alert">{error}<Button size="sm" onClick={onRetry}>重新读取</Button></div>}
    <div className="sc-list-scroll">
      {['今天', '近 7 天', '更早'].map(group => {
        const items = sorted.filter(item => chatDateGroup(item.updated_at) === group);
        if (!items.length) return null;
        return <section className="sc-list-group" key={group}>
          <Button className="sc-group-title" variant="ghost" size="sm" aria-expanded={!collapsed[group]} onClick={() => setCollapsed(current => ({ ...current, [group]: !current[group] }))}>
            <ChevronDown size={14} className={collapsed[group] ? 'is-collapsed' : ''} /><span>{group}</span><span className="sc-group-thread" /><span>{items.length}</span>
          </Button>
          {!collapsed[group] && items.map(item => <div className={`sc-list-row ${chatId === item.id ? 'is-active' : ''}`} key={item.id}>
            <Link to={`/simple-chats/${item.id}`} className="sc-list-item" aria-current={chatId === item.id ? 'page' : undefined}>
              <Avatar className={`sc-model-mark sc-model-${modelFamily(item.model)}`} name={modelShortName(item.model)} size={32} dense />
              <span className="sc-list-text"><span className="sc-list-title"><strong>{chatTitle(item.title)}</strong><time dateTime={item.updated_at}>{chatRelativeTime(item.updated_at)}</time></span>
                <small><span>{modelShortName(item.model)}</span> · {item.message_count} 条</small></span>
            </Link>
            <div className="sc-list-tools">
              <Button variant="ghost" size="sm" disabled={busy} aria-label={`重命名 ${chatTitle(item.title)}`} onClick={() => onRename(item)}><Pencil size={14} /></Button>
              <Button variant="ghost" size="sm" disabled={busy} aria-label={`删除 ${chatTitle(item.title)}`} onClick={() => onDelete(item)}><Trash2 size={14} /></Button>
            </div>
          </div>)}
        </section>;
      })}
      {loading && <p role="status">正在读取聊天列表…</p>}
      {!loading && !visible.length && <div className="sc-list-empty">{list.length ? '没有匹配的聊天' : '还没有聊天，点击右上角新建。'}</div>}
    </div>
    <footer className="sc-list-total">{query.trim() ? `找到 ${visible.length} 段 · ` : ''}共 {list.length} 段聊天</footer>
  </aside>;
}
