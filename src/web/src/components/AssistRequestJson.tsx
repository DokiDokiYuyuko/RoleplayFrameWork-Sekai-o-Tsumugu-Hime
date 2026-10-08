import { useQuery } from '@tanstack/react-query';
import { resourceQueries } from '../features/resources/resourceQueries';
import { useEffect, useState } from 'react';
import { ClipboardCopy, Download, RefreshCw } from 'lucide-react';
import { useChatStore } from '../store/chatStore';

/** HTTP boundary capture: the body shown here is the actual outbound JSON. */
export function AssistRequestJson() {
  const sessionId = useChatStore((state) => state.currentSessionId);
  const turn = useChatStore((state) => state.sessions.find((item) => item.id === state.currentSessionId)?.turn);
  const characters = useChatStore((state) => state.sessionCharacters);
  const list = useQuery({ ...resourceQueries.modelRequests(sessionId ?? ""), enabled: !!sessionId });
  const rows = list.data ?? [];
  const [recordId, setRecordId] = useState('');
  const detail = useQuery({ ...resourceQueries.modelRequest(sessionId ?? '', recordId), enabled: !!sessionId && !!recordId });
  const record = detail.data ?? null;
  const loading = list.isFetching;
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  useEffect(() => { setRecordId(''); if (sessionId) void list.refetch(); }, [sessionId, turn]);
  useEffect(() => { if (list.error) setError('无法读取模型请求记录'); }, [list.error]);
  const open = (id: string) => { setRecordId(id); setCopied(false); setError(''); };
  useEffect(() => { if (detail.error) setError('无法读取这条请求'); }, [detail.error]);
  const refresh = async () => { await list.refetch(); };
  const json = record ? JSON.stringify(record.body, null, 2) : '';
  const copy = async () => {
    if (!json) return;
    try { await navigator.clipboard.writeText(json); setCopied(true); }
    catch { setError('复制失败，请直接选中文本复制'); }
  };
  const download = () => {
    if (!record) return;
    const url = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `model-request-turn-${record.turn}-${record.id.slice(0, 8)}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };
  const characterName = (id: string) => characters.find((item) => item.id === id)?.card.name ?? id;

  return (
    <section className="v7-request-json" aria-label="实际模型请求 JSON">
      <div className="v7-request-json-head">
        <div>
          <h3>实际发送的 JSON</h3>
          <p>记录角色回复请求的完整消息、注入内容与模型参数；不含 API Key 请求头。</p>
        </div>
        <button type="button" onClick={() => void refresh()} disabled={loading || !sessionId} title="刷新请求列表" aria-label="刷新请求列表"><RefreshCw size={16} /></button>
      </div>
      {error && <p role="alert" className="v7-error">{error}</p>}
      {rows.length > 0 ? (
        <label className="v7-field">
          选择一次生成请求
          <select value={record?.id ?? ''} onChange={(event) => void open(event.target.value)}>
            <option value="" disabled>选择回合和角色</option>
            {rows.map((item) => <option key={item.id} value={item.id}>
              第 {item.turn} 回合 · {characterName(item.character_id)} · {item.engine.toUpperCase()} · {new Date(item.created_at).toLocaleString()}
            </option>)}
          </select>
        </label>
      ) : <p className="v7-muted">{loading ? '正在读取请求记录…' : '还没有实发请求记录。下一次角色生成后可在这里查看；旧回合无法还原成实发 JSON。'}</p>}
      {record && (
        <>
          {record.player_identity_source?.name && <p className="v7-muted">
            生成时控制的人物：{record.player_identity_source.name} · 人物 ID：{record.player_identity_source.person_id}
          </p>}
          <div className="v7-request-json-actions">
            <span>第 {record.turn} 回合 · {characterName(record.character_id)} · {record.engine.toUpperCase()}</span>
            <button type="button" onClick={() => void copy()}><ClipboardCopy size={14} />{copied ? '已复制' : '复制 JSON'}</button>
            <button type="button" onClick={download}><Download size={14} />下载</button>
          </div>
          <pre className="v7-request-json-code"><code>{json}</code></pre>
        </>
      )}
    </section>
  );
}
