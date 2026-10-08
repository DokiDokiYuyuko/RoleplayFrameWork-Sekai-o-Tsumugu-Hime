import { libraryClient } from "./libraryClient";
import { toView } from "../../api/adapters";
import { useCharacterStore } from "../../store/characterStore";

/** Refresh library media revision before the editor takes its independent draft snapshot. */
export async function readCharacterForEditing(id: string) {
  const character = toView(await libraryClient.getCharacter(id));
  useCharacterStore.getState().upsert(character);
  return useCharacterStore.getState().characters.find(value => value.id === id) ?? character;
}
