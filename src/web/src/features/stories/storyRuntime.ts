import { commandReceipt } from '../../utils/commandReceipt';
import type { Session } from '../../types';
import { useChatStore, storyBranchEpoch } from '../../store/chatStore';
import { toView } from '../../api/adapters';
import type { GroupActor, Scene, Message, CharacterView } from '../../types';
import type { StoryView } from './storyView';
import { installSessionMetadata } from './storySessionMetadata';

/** Tools update the current branch through one guarded runtime boundary. */
export const storyRuntime = {
  capture(branchId: string) {
    const epoch = storyBranchEpoch();
    return () => useChatStore.getState().currentSessionId === branchId && storyBranchEpoch() === epoch;
  },
  clearDeletedBranches(ids: readonly string[]) {
    const state = useChatStore.getState();
    if (!state.currentSessionId || !ids.includes(state.currentSessionId)) return;
    state.disconnect();
    useChatStore.setState({ currentSessionId: null, messages: [], locatedMessageId: null });
  },
  setLocation(messageId: string | null) { useChatStore.setState({ locatedMessageId: messageId }); },
  applyCharacter(branchId: string, character: CharacterView) {
    if (useChatStore.getState().currentSessionId !== branchId) return;
    useChatStore.setState(state => ({ sessionCharacters: state.sessionCharacters.map(item => item.id === character.id
      && (character.revision ?? 0) >= (item.revision ?? 0) ? character : item) }));
  },
  optimisticSessionPatch(branchId: string, patch: Partial<Session>) {
    const ownsBranch = this.capture(branchId);
    const previous = useChatStore.getState().sessions.find(session => session.id === branchId);
    this.applySessionPatch(branchId, patch);
    return () => {
      if (!previous || !ownsBranch()) return;
      useChatStore.setState(state => ({ sessions: state.sessions.map(session => {
        if (session.id !== branchId || session.branch_revision !== previous.branch_revision) return session;
        const rollback = Object.fromEntries(Object.entries(patch).filter(([key, value]) => session[key as keyof Session] === value)
          .map(([key]) => [key, previous[key as keyof Session]]));
        return { ...session, ...rollback };
      }) }));
    };
  },
  applySessionPatch(branchId: string, patch: Partial<Session>) {
    const state = useChatStore.getState(), receipt = commandReceipt(patch);
    if (state.currentSessionId !== branchId) return;
    const session = state.sessions.find(session => session.id === branchId);
    if (receipt && receipt.branch_revision < (session?.branch_revision ?? 0)) return;
    useChatStore.setState(state => installSessionMetadata(state, branchId, patch, receipt?.branch_revision));
  },
  sceneImageRevision(branchId: string, sceneId: string) {
    const state = useChatStore.getState();
    if (state.currentSessionId !== branchId) return 0;
    return Math.max(state.sessions.find(session => session.id === branchId)?.branch_revision ?? 0,
      state.sessionFieldRevisions?.[`scene.image:${sceneId}`] ?? 0);
  },
  applySceneImage(branchId: string, scene: Scene) {
    const state = useChatStore.getState(), receipt = commandReceipt(scene);
    if (state.currentSessionId !== branchId || state.activeScene?.id !== scene.id) return false;
    const revision = this.sceneImageRevision(branchId, scene.id);
    if (!receipt || receipt.branch_revision < revision) return false;
    useChatStore.setState(current => ({ activeScene: { ...current.activeScene!, builtin_image_id: scene.builtin_image_id ?? null },
      sessionFieldRevisions: { ...current.sessionFieldRevisions, [`scene.image:${scene.id}`]: receipt.branch_revision } }));
    return true;
  },
  applyRoster(branchId: string, groups: GroupActor[], scenes: { scenes: Scene[]; active_scene_id: string | null }) {
    if (useChatStore.getState().currentSessionId !== branchId) return;
    useChatStore.setState({ sessionGroups: groups, activeScene: scenes.scenes.find(scene => scene.id === scenes.active_scene_id) ?? null });
  },
  applyView(branchId: string, view: StoryView) {
    if (useChatStore.getState().currentSessionId !== branchId) return;
    const previous = useChatStore.getState().sessions.find(session => session.id === branchId);
    if ((view.session.branch_revision ?? 0) < (previous?.branch_revision ?? 0)) return;
    useChatStore.setState(state => ({ sessionCharacters: view.characters.map(toView), sessionLorebooks: view.lorebooks,
      sessionGroups: view.groups, activeScene: view.scenes.find(scene => scene.id === view.active_scene_id) ?? null,
      ...installSessionMetadata(state, branchId, view.session, view.session.branch_revision) }));
  },
  applyMessage(branchId: string, message: Message) {
    if (useChatStore.getState().currentSessionId !== branchId || message.session_id !== branchId) return;
    const receipt = commandReceipt(message);
    const event = { type: 'message.updated' as const, message, delivery_source: 'http',
      branch_revision: receipt?.branch_revision, outbox_event_id: receipt ? `command:${receipt.command_id}:${message.id}` : undefined };
    useChatStore.getState().handleSseEvent(event);
  },
};
