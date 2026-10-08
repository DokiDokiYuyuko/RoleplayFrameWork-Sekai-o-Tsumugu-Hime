export interface CommandReceipt { command_id: string; branch_revision: number }
const receiptKey = Symbol('story-command-receipt');
export function attachCommandReceipt<T>(value: T, receipt?: CommandReceipt): T {
  if (receipt && value && typeof value === 'object') Object.defineProperty(value, receiptKey, { value: receipt });
  return value;
}
export function commandReceipt(value: unknown): CommandReceipt | undefined {
  return value && typeof value === 'object' ? (value as { [receiptKey]?: CommandReceipt })[receiptKey] : undefined;
}
