import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { commandRecovery } from './commandRecovery';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import type { IncompleteCommand } from './commandRecovery';
import { useChatStore } from '../../store/chatStore';
import { commandReviewContext } from './commandReviewContext';
import '../../components/chat-notices.css';

/** Explicit review resets identity only; the user still starts the next operation themselves. */
export function CommandRecoveryNotice() {
  const incomplete = useSyncExternalStore(commandRecovery.subscribe, commandRecovery.incomplete);
  const [review, setReview] = useState<IncompleteCommand | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const currentBranch = useChatStore(state => state.currentSessionId);
  const sessions = useChatStore(state => state.sessions);
  useEffect(() => {
    if (review && !commandReviewContext(review, currentBranch, sessions)) setReview(null);
  }, [review, currentBranch, sessions]);
  const available = incomplete.find(command => commandReviewContext(command, currentBranch, sessions));
  if (!available) return null;
  const label = commandReviewContext(review ?? available, currentBranch, sessions) ?? '先前操作';
  return createPortal(<>
    <div role="alert" className="chat-error-notice" style={{ position: 'fixed', zIndex: 80, bottom: 16, right: 16, maxWidth: 'min(420px, calc(100vw - 32px))' }}>
      <div className="chat-error-content"><p>{label}未完整提交。重复点击会检查原操作，不会重新生成。</p>
        <button ref={trigger} className="v7-btn v7-btn-soft" onClick={() => setReview(available)}>已核对结果，准备新尝试</button>
      </div>
    </div>
    <ConfirmDialog open={Boolean(review)} title="开始一次新的尝试？"
      description={`请先核对${label}的结果。确认后，下一次点击原操作按钮会作为新操作执行，可能再次调用模型；本次确认不会自动提交。`}
      returnFocus={trigger.current}
      confirmLabel="我已核对，允许新尝试" onClose={() => setReview(null)} onConfirm={() => {
        if (review && commandReviewContext(review, currentBranch, sessions)) commandRecovery.reviewNewAttempt(review.path, review.method, review.id);
        setReview(null);
      }} />
  </>, document.body);
}
