import { useState, type FormEvent } from "react";

type TextValue = string | number | readonly string[] | undefined;

/**
 * Current text of a controlled or uncontrolled text control. Auto-grow and character counts need it
 * even when the caller does not pass `value`. Bind `track` to the control's `onInput`.
 */
export function useTextValue(value: TextValue, defaultValue: TextValue) {
  const controlled = value !== undefined;
  const [draft, setDraft] = useState(String(defaultValue ?? ""));
  const text = controlled ? String(value) : draft;
  const track = (event: FormEvent<HTMLTextAreaElement>) => {
    if (!controlled) setDraft(event.currentTarget.value);
  };
  return { text, track };
}
