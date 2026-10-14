import type { Receipt } from './types';

export function recordedHemiSource(receipt: Receipt): string | undefined {
  const source = receipt.source_recovery?.hemi_rpc_used || receipt.sources?.hemi_rpc_used || receipt.sources?.hemi_rpc;
  return source && ['https://rpc.hemi.network/rpc', 'https://hemi.drpc.org'].includes(source) ? source : undefined;
}
export function recordedBitcoinSources(receipt: Receipt): string[] {
  return (receipt.sources?.bitcoin_apis || []).filter((source, index, all) => ['https://blockstream.info/api', 'https://mempool.space/api'].includes(source) && all.indexOf(source) === index);
}
export function bitcoinReference(type: 'block' | 'tx', hash?: string): string | undefined {
  return /^[0-9a-f]{64}$/i.test(hash || '') ? `https://blockstream.info/${type}/${hash}` : undefined;
}
