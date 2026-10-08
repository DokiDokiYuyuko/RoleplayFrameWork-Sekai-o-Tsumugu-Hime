import { useCharacterStore } from './characterStore';
import { useChatStore } from './chatStore';

/** Active-session snapshot first; regular library resources are the legacy fallback. */
export function useActiveCharacters() {
  const library = useCharacterStore((state) => state.characters);
  const sessionCharacters = useChatStore((state) => state.sessionCharacters);
  const currentSessionId = useChatStore((state) => state.currentSessionId);
  const session = useChatStore((state) =>
    state.sessions.find((item) => item.id === state.currentSessionId),
  );
  if (currentSessionId && sessionCharacters.length > 0) return sessionCharacters;
  const ids = new Set(session?.character_ids ?? []);
  return library.filter((character) => ids.has(character.id));
}
