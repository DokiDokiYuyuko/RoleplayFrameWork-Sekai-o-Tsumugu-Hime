import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import { CharacterEditorModal } from "../components/CharacterEditorModal";
import { LoadState } from "../components/LoadState";
import { readCharacterForEditing } from "../features/library/characterEditing";
import type { Character } from "../types";
import "../components/character-editor.css";

/** The route owns the read snapshot; the shared editor owns unsaved authoring input. */
export default function CharacterEditorPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const [snapshot, setSnapshot] = useState<{ id: string; character: Character } | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!id || id === "new") return;
    let alive = true;
    setError("");
    readCharacterForEditing(id).then(character => {
      if (!alive) return;
      setSnapshot({ id, character });
    }).catch(cause => {
      if (alive) setError(cause instanceof Error ? cause.message : "角色读取失败");
    });
    return () => { alive = false; };
  }, [id, attempt]);
  const creating = !id || id === "new";
  const character = snapshot && snapshot.id === id ? snapshot.character : undefined;
  const listPath = `/library/characters${search.size ? `?${search.toString()}` : ""}`;
  if (!creating && !character) return <div className="workspace-page character-editor-loading"><LoadState title={error ? "角色读取失败" : "正在展开角色手稿"} error={error || undefined} onRetry={error ? () => setAttempt(value => value + 1) : undefined} /><button type="button" className="v7-btn v7-btn-soft" onClick={() => navigate(listPath)}>返回角色库</button></div>;
  return <CharacterEditorModal key={creating ? "new-character" : id} presentation="page" character={character} onClose={() => navigate(listPath)} onCreated={created => navigate(`/library/characters/${encodeURIComponent(created.id)}${search.size ? `?${search.toString()}` : ""}`, { replace: true })} />;
}
