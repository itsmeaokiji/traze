import type { PublicationSummary } from './types';

export interface TimelineGroup {
  hash: string;
  height: number | null;
  heightConflict: boolean;
  records: PublicationSummary[];
}

// A payload identifier includes the encoded height. Retain both when selecting
// display records so contradictory summaries cannot silently merge identities.
export function timelineKeystoneKey(record: PublicationSummary): string | undefined {
  return record.state !== 'unsupported' && record.keystone_id !== null && record.keystone_height !== null
    ? `${record.keystone_height}:${record.keystone_id}` : undefined;
}

export function timelineGroups(records: PublicationSummary[]): TimelineGroup[] {
  const blocks = new Map<string, PublicationSummary[]>();
  for (const record of records) {
    const group = blocks.get(record.bitcoin_block_hash) || [];
    group.push(record);
    blocks.set(record.bitcoin_block_hash, group);
  }
  return [...blocks].map(([hash, items]) => {
    const heights = new Set(items.map(item => item.bitcoin_height).filter(height => height !== null));
    const conflict = items.some(item => item.height_conflict) || heights.size > 1;
    const height = !conflict && heights.size === 1 && items.every(item => item.bitcoin_height !== null)
      ? [...heights][0] : null;
    const ordered = [...items].sort((a, b) => (a.keystone_height ?? Infinity) - (b.keystone_height ?? Infinity)
      || (a.keystone_id || '').localeCompare(b.keystone_id || '') || a.id.localeCompare(b.id));
    return { hash, height, heightConflict: conflict, records: ordered };
  }).sort((a, b) => a.height === null ? b.height === null ? 0 : 1 : b.height === null ? -1 : b.height - a.height);
}
