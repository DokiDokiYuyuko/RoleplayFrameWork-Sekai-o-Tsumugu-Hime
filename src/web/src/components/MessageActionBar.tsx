import { Button } from '../design-system/Button';
import { useState, type ReactNode, type RefObject } from 'react';
import { DropdownMenu } from 'radix-ui';
import { AudioLines, LoaderCircle, MoreHorizontal, Volume2 } from '../design-system/Icon';
import './message-actions.css';

export interface MessageAction {
  id: string;
  label: string;
  title?: string;
  icon?: ReactNode;
  onClick: () => void | Promise<unknown>;
  disabled?: boolean;
  danger?: boolean;
  active?: boolean;
  quick?: boolean;
}

export interface MessageSpeech {
  enabled: boolean;
  busy: boolean;
  playing: boolean;
  emotion: string;
  onEmotionChange: (value: string) => void;
  onSpeak: () => void | Promise<unknown>;
  onEnded: () => void;
  audioUrl: string | null;
  audioRef: RefObject<HTMLAudioElement>;
}

const emotions = [['', '自然'], ['excited', '开心'], ['sad', '悲伤'], ['angry', '生气'], ['whisper', '耳语'], ['surprised', '惊讶']];
const storyQuickOrder = ['copy', 'regenerate', 'edit'];
const commonQuickOrder = ['copy', 'regenerate'];

/** Common actions remain visible on pointer, touch and keyboard; other actions have a menu destination. */
export function MessageActionBar({ actions, speech, externalError, onDismissError, navigation, compactIcons = false }: {
  actions: MessageAction[]; speech?: MessageSpeech; externalError?: string | null;
  onDismissError?: () => void; navigation?: ReactNode; compactIcons?: boolean;
}) {
  const [feedback, setFeedback] = useState<{ error: boolean; text: string } | null>(null);
  const invoke = async (action: MessageAction) => {
    if (action.disabled) return;
    setFeedback(null);
    try {
      await action.onClick();
      if (action.id === 'copy') setFeedback({ error: false, text: '已复制' });
    } catch (cause) {
      setFeedback({ error: true, text: action.id === 'copy'
        ? '复制失败，可选中正文后手动复制。'
        : cause instanceof Error ? cause.message : '操作失败，请重试。' });
    }
  };
  const quickOrder = compactIcons ? storyQuickOrder : commonQuickOrder;
  const quickIds = new Set(quickOrder);
  const quick = quickOrder.flatMap((id) => actions.filter((action) => action.id === id && action.quick !== false));
  const more = actions.filter((action) => !quickIds.has(action.id) || action.quick === false);
  const speechLabel = speech?.playing ? '暂停朗读' : '朗读这条消息';
  const speechButton = speech?.enabled && <Button variant="ghost" type="button" className={compactIcons ? undefined : 'v7-message-quick-button mw-tool-button is-speech'} aria-label={speechLabel} title={speechLabel}
    disabled={speech.busy} onClick={() => void invoke({ id: 'speak', label: '朗读', disabled: speech.busy, onClick: speech.onSpeak })}>
    {speech.busy ? <LoaderCircle size={14} className="animate-spin" /> : speech.playing ? <Volume2 size={14} /> : <AudioLines size={14} />}
    <span>{compactIcons ? speech.busy ? '朗读生成中…' : speechLabel : speech.busy ? '生成中' : speech.playing ? '暂停' : '朗读'}</span>
  </Button>;
  return <div className="message-action-bar">
    <div className="message-tool-strip">
    <div className="v7-message-quick-actions">
      {quick.map((action) => <span key={action.id} className="message-action-hint-host" tabIndex={action.disabled ? 0 : undefined} aria-label={action.disabled ? `${action.label}${action.title ? `：${action.title}` : ''}` : undefined}>
        <Button variant="ghost" type="button" className={`v7-message-quick-button mw-tool-button${action.active ? " is-active" : ""}`}
        aria-pressed={action.active === undefined ? undefined : action.active}
        aria-label={action.label} aria-description={action.title} title={action.title ?? action.label} disabled={action.disabled} onClick={() => void invoke(action)}>
        {action.icon}{!compactIcons && <span>{action.id === 'copy' ? '复制' : action.label}</span>}
      </Button><span className="message-action-hint" role="tooltip">{action.label}{action.title ? ` · ${action.title}` : ''}</span></span>)}
      {!compactIcons && speechButton}
      {(more.length > 0 || speech) && <DropdownMenu.Root>
        <span className="message-action-hint-host"><DropdownMenu.Trigger type="button" className="v7-message-actions-toggle mw-tool-button" aria-label="消息操作" title="更多">
          {!compactIcons && '操作 '}<MoreHorizontal size={16} />
        </DropdownMenu.Trigger><span className="message-action-hint" role="tooltip">更多</span></span>
        <DropdownMenu.Portal><DropdownMenu.Content className="message-action-menu" sideOffset={5} collisionPadding={12} aria-label="消息操作">
          {more.map((action) => <DropdownMenu.Item key={action.id} asChild disabled={action.disabled}>
            <Button variant="ghost" type="button" aria-label={action.label} aria-description={action.title} title={action.title} disabled={action.disabled}
              className={`${action.danger ? 'is-danger' : ''} ${action.active ? 'is-active' : ''}`}
              onClick={() => void invoke(action)}>{action.icon}<span>{action.label}</span></Button>
          </DropdownMenu.Item>)}
          {compactIcons && speech?.enabled && <DropdownMenu.Item asChild disabled={speech.busy}>{speechButton}</DropdownMenu.Item>}
          {speech?.enabled && <DropdownMenu.Sub>
            <DropdownMenu.SubTrigger className="message-action-subtrigger">朗读语气 <span>›</span></DropdownMenu.SubTrigger>
            <DropdownMenu.Portal><DropdownMenu.SubContent className="message-action-menu" sideOffset={5} collisionPadding={12}>
              <DropdownMenu.RadioGroup value={speech.emotion} onValueChange={speech.onEmotionChange} aria-label="朗读语气">
                {emotions.map(([value, label]) => <DropdownMenu.RadioItem key={value} value={value} disabled={speech.busy} className="message-action-radio">
                  <span className="message-action-check"><DropdownMenu.ItemIndicator>✓</DropdownMenu.ItemIndicator></span>{label}
                </DropdownMenu.RadioItem>)}
              </DropdownMenu.RadioGroup>
            </DropdownMenu.SubContent></DropdownMenu.Portal>
          </DropdownMenu.Sub>}
          {speech && !speech.enabled && <DropdownMenu.Item asChild><a href="/settings/voice">启用朗读…</a></DropdownMenu.Item>}
        </DropdownMenu.Content></DropdownMenu.Portal>
      </DropdownMenu.Root>}
      {speech?.audioUrl && <audio ref={speech.audioRef} src={speech.audioUrl} onEnded={speech.onEnded} onPause={speech.onEnded} />}
    </div>
    {navigation && <div className="message-action-navigation">{navigation}</div>}
    </div>
    {feedback && <p className={`message-action-feedback${feedback.error ? ' is-error' : ''}`} role={feedback.error ? 'alert' : 'status'}>{feedback.text}</p>}
    {externalError && <div className="message-action-error" role="alert"><p className="message-action-feedback is-error">{externalError}</p>
      {onDismissError && <Button variant="ghost" type="button" onClick={onDismissError} aria-label="关闭消息操作错误">关闭</Button>}
    </div>}
  </div>;
}
