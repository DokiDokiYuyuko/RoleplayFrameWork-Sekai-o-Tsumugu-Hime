/** Candidate metadata is the audit source; top-level IDs support existing stream envelopes. */
export function generationIdForMessage(message) {
  const variant = message?.active_variant != null ? message.variants?.[message.active_variant] : null;
  return variant?.generation_meta?.generation_id ?? variant?.generation_id
    ?? message?.generation_meta?.generation_id ?? message?.generation_id ?? null;
}
