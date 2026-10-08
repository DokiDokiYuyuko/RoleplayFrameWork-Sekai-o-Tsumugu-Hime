import { createContext, useContext } from "react";

/** Wiring a Field hands to the control it wraps. Explicit props on the control win. */
export interface FieldControlWiring {
  id?: string;
  describedBy?: string;
  invalid?: boolean;
  required?: boolean;
}

export const FieldContext = createContext<FieldControlWiring | null>(null);

export interface ControlAria {
  id?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean | "true" | "false" | "grammar" | "spelling";
  "aria-required"?: boolean | "true" | "false";
}

/** Merges the surrounding Field's id and ARIA wiring into a control's own props. */
export function useFieldControl(own: ControlAria): ControlAria {
  const field = useContext(FieldContext);
  const describedBy = [own["aria-describedby"], field?.describedBy].filter(Boolean).join(" ") || undefined;
  return {
    id: own.id ?? field?.id,
    "aria-describedby": describedBy,
    "aria-invalid": own["aria-invalid"] ?? (field?.invalid ? true : undefined),
    "aria-required": own["aria-required"] ?? (field?.required ? true : undefined),
  };
}
