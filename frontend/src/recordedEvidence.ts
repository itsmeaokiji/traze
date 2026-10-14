import { formatDate } from './format';
import type { Receipt } from './types';

export function isRecorded(receipt: Receipt): boolean {
  return receipt.mode === 'offline_catalogue';
}

export function historicalSourceTimes(receipt: Receipt): string {
  const times = receipt.catalogue?.historical_source_times || [];
  return times.length ? times.map(formatDate).join('; ') : 'Not recorded';
}
