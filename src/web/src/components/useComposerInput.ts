import { useEffect, useRef } from 'react';
import type { KeyboardEvent, RefObject } from 'react';

/** Some phone keyboards report the send action as beforeinput, without Enter. */
export function useComposerInput(inputRef: RefObject<HTMLTextAreaElement>, submit: () => void) {
  const composing = useRef(false);
  const shiftEnter = useRef(false);
  const latestSubmit = useRef(submit);
  latestSubmit.current = submit;

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    const beforeInput = (event: InputEvent) => {
      if (event.inputType !== 'insertParagraph' && event.inputType !== 'insertLineBreak') return;
      if (event.isComposing || composing.current || shiftEnter.current) return;
      if (!event.cancelable) return;
      event.preventDefault();
      latestSubmit.current();
    };
    input.addEventListener('beforeinput', beforeInput);
    return () => input.removeEventListener('beforeinput', beforeInput);
  }); // The textarea can mount after its conversation finishes loading.

  return {
    onCompositionStart: () => { composing.current = true; },
    onCompositionEnd: () => { composing.current = false; },
    onBlur: () => { composing.current = false; shiftEnter.current = false; },
    onKeyUp: () => { shiftEnter.current = false; },
    onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => {
      shiftEnter.current = event.key === 'Enter' && event.shiftKey;
      if (event.nativeEvent.isComposing || composing.current || event.nativeEvent.keyCode === 229) return;
      if ((event.key === 'Enter' || event.nativeEvent.keyCode === 13) && !event.shiftKey) {
        event.preventDefault();
        latestSubmit.current();
      }
    },
  };
}
