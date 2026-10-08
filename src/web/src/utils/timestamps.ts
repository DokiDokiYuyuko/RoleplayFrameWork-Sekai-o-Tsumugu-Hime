/** Normalize API timestamps that may be ISO strings or Unix seconds/milliseconds. */
export function timestampValue(value: string | number | null | undefined): number {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value < 100_000_000_000 ? value * 1000 : value;
  }
  if (typeof value === 'string') {
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? 0 : parsed;
  }
  return 0;
}

export function compareNewestFirst(
  a: string | number | null | undefined,
  b: string | number | null | undefined,
): number {
  return timestampValue(b) - timestampValue(a);
}
