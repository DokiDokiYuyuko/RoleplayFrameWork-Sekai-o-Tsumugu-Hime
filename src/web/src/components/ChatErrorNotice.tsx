import { AlertCircle, X } from '../design-system/Icon';
import './chat-notices.css';

export function errorSummary(error: string): string {
  if (error.includes('Reasoning is mandatory')) return '该模型要求开启推理，当前请求未被接受。';
  if (/^409:/.test(error)) return error.replace(/^409:\s*/, '');
  return error.length > 160 ? `${error.slice(0, 160)}…` : error;
}

export function ChatErrorNotice({ error, onDismiss }: { error: string; onDismiss: () => void }) {
  const summary = errorSummary(error);
  return <div className="chat-error-notice" role="alert">
    <AlertCircle size={16} aria-hidden="true" />
    <div className="chat-error-content"><p>{summary}</p>
      {summary !== error && <details><summary>查看错误详情</summary><pre>{error}</pre></details>}
    </div>
    <button type="button" className="v7-icon-btn" aria-label="关闭错误提示" onClick={onDismiss}><X size={16} /></button>
  </div>;
}
