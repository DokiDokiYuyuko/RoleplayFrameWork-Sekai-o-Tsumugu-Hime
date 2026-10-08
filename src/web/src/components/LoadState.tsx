import { useEffect, useState } from 'react';
import { LoaderCircle, RefreshCw } from 'lucide-react';
import { getLoadDiagnostics } from '../utils/loadDiagnostics';

export function LoadState({ title, error, onRetry }: { title: string; error?: string; onRetry?: () => void }) {
  const [slow, setSlow] = useState(false);
  useEffect(() => { const timer = setTimeout(() => setSlow(true), 3000); return () => clearTimeout(timer); }, [title]);
  return <section className="page-load-state" role={error ? 'alert' : 'status'} aria-live="polite">
    <div className="page-load-state__heading">{error ? <RefreshCw size={23} /> : <LoaderCircle size={23} className="page-load-spinner" />}<h2>{title}</h2></div>
    <p>{error || (slow ? '读取比平时慢。页面会给出超时提示，你也可以返回后重新打开。' : '正在读取页面资料…')}</p>
    {onRetry && <button type="button" className="v7-btn v7-btn-soft" onClick={onRetry}>重新读取</button>}
    {(slow || error) && <details><summary>加载诊断</summary><pre>{JSON.stringify(getLoadDiagnostics(), null, 2)}</pre></details>}
  </section>;
}
