import type { Receipt } from './types';

const numberFormat = new Intl.NumberFormat('en-US');
const dateFormat = new Intl.DateTimeFormat('en-PH', { dateStyle: 'medium', timeStyle: 'medium', timeZone: 'Asia/Manila' });
export const formatNumber = (value?: number | null) => typeof value === 'number' && Number.isFinite(value) ? numberFormat.format(value) : '—';
export const formatBytes = (value?: number) => Number.isFinite(value) ? `${((value as number) / 1e6).toFixed(2)} MB` : '—';
export const shortHash = (hash: string) => `${hash.slice(0, 12)}…${hash.slice(-8)}`;
export function formatDate(value?: string): string {
  return !value || Number.isNaN(Date.parse(value)) ? 'not recorded' : `${dateFormat.format(new Date(value))} PHT`;
}
export const receiptInput = (receipt: Receipt): string => String(receipt.query.requested_block_hash ?? receipt.query.block_number ?? '');
export function selectedProof(receipt: Receipt) {
  const index = receipt.selection?.current_proof_index ?? receipt.selection?.best_observed_proof_index;
  return typeof index === 'number' && Number.isInteger(index) ? receipt.proofs[index] : undefined;
}
export function hemiSource(receipt?: Receipt): string {
  const source = receipt?.source_recovery?.hemi_rpc_used || receipt?.sources?.hemi_rpc_used || receipt?.sources?.hemi_rpc;
  return source && ['https://rpc.hemi.network/rpc', 'https://hemi.drpc.org'].includes(source) ? source : 'https://rpc.hemi.network/rpc';
}
