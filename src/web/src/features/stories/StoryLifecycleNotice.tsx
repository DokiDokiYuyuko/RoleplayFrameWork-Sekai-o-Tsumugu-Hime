import { useStoryNotice } from './storyNotices';
export function StoryLifecycleNotice() { const { message, dismiss } = useStoryNotice(); return message ? <aside className="story-command-recovery" role="status"><p>{message}</p><button type="button" onClick={dismiss}>知道了</button></aside> : null; }
