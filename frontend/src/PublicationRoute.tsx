import type { ReactNode } from 'react';
import { ChainMark } from './ChainMark';
import { formatNumber } from './format';
import type { Proof, Receipt } from './types';
import { ExternalLink } from './ExternalLink';

export type RouteRole = 'requested' | 'covering' | 'later' | 'publication';
type RouteRecord = {
  role: RouteRole;
  chain: 'hemi' | 'bitcoin';
  label: string;
  value: ReactNode;
  note: string;
  resolved: boolean;
};

function RouteEdge({ observed, direction }: { observed: boolean; direction: 'incoming' | 'outgoing' }) {
  return <span className={`route-edge route-${direction}${observed ? '' : ' is-unresolved'}`} data-evidence={observed ? 'observed' : 'unresolved'} aria-hidden="true">
    <svg className="route-edge-horizontal" viewBox="0 0 100 4" preserveAspectRatio="none" focusable="false"><path d="M0 2H100" /></svg>
    <svg className="route-edge-vertical" viewBox="0 0 4 100" preserveAspectRatio="none" focusable="false"><path d="M2 0V100" /></svg>
  </span>;
}

export function PublicationRoute({ receipt, proof, selectedRole, onSelect }: { receipt: Receipt; proof?: Proof; selectedRole?: RouteRole; onSelect?: (role: RouteRole) => void }) {
  const cover = receipt.query.covering_keystone;
  const later = Boolean(proof && cover !== undefined && proof.checkpoint.height > cover);
  const records: RouteRecord[] = [
    {
      role: 'requested', chain: 'hemi', label: 'Requested Hemi block',
      value: receipt.query.block_number !== null ? formatNumber(receipt.query.block_number) : 'Unresolved height',
      note: receipt.query.header ? 'The Hemi block you searched for.' : 'The Hemi source could not return the requested block.',
      resolved: Boolean(receipt.query.header && receipt.query.block_number !== null),
    },
    {
      role: 'covering', chain: 'hemi', label: 'Covering keystone',
      value: cover !== undefined ? formatNumber(cover) : 'Unresolved',
      note: cover === undefined ? 'The covering keystone could not be resolved.' : proof && !later ? 'Encoded in the publication transaction.' : 'The keystone associated with the requested block.',
      resolved: cover !== undefined && Boolean(receipt.query.covering_checkpoint),
    },
  ];
  if (later && proof) records.push({
    role: 'later', chain: 'hemi', label: 'Later keystone', value: formatNumber(proof.checkpoint.height),
    note: 'Encoded in the transaction; checked ancestry supports the requested block.', resolved: true,
  });
  records.push({
    role: 'publication', chain: 'bitcoin', label: 'Bitcoin publication',
    value: proof ? /^[0-9a-f]{64}$/i.test(proof.bitcoin_block_hash)
      ? <ExternalLink href={`https://blockstream.info/block/${proof.bitcoin_block_hash}`} title="Open the containing Bitcoin block in a new tab">Block {formatNumber(proof.bitcoin_height)}</ExternalLink>
      : `Block ${formatNumber(proof.bitcoin_height)}` : 'Unresolved',
    note: proof ? 'Containing Bitcoin block. Transaction ID below.' : receipt.status === 'not_found_within_coverage' ? 'No applicable match in the scanned range.' : 'Publication evidence is incomplete.',
    resolved: Boolean(proof),
  });
  // Retained proof relationships do not depend on a fresh provider confirmation.
  // An unresolved search draws no solid publication route, even when Hemi records are known.
  const edgeObserved = (index: number) => Boolean(proof && records[index]?.resolved && records[index + 1]?.resolved);

  return <ol id="association" role="list" className={`association${later ? ' has-later-keystone' : ''}`} aria-label="Hemi to Bitcoin association" data-publication-status={proof ? 'observed' : 'unresolved'}>
    {records.map((record, index) => <li key={record.role} data-route-role={record.role} className={`route-record chain-${record.chain}${record.resolved ? '' : ' is-unresolved'}${selectedRole === record.role ? ' is-selected' : ''}`}>
      <div className="route-track">
        {index > 0 ? <RouteEdge direction="incoming" observed={edgeObserved(index - 1)} /> : <span className="route-edge-spacer" aria-hidden="true" />}
        {onSelect ? <button type="button" className="route-node" aria-label={`Inspect ${record.label}`} aria-pressed={selectedRole === record.role} aria-controls="record-inspector" onClick={() => onSelect(record.role)}><ChainMark chain={record.chain} /></button> : <span className="route-node"><ChainMark chain={record.chain} /></span>}
        {index < records.length - 1 ? <RouteEdge direction="outgoing" observed={edgeObserved(index)} /> : <span className="route-edge-spacer" aria-hidden="true" />}
      </div>
      <div className="route-copy"><span className="route-label"><span className="detail-label">{record.label}</span>{selectedRole === record.role && <span className="route-selected-tag">Selected</span>}</span><span className="step-value">{record.value}</span><p className="step-note">{record.note}</p></div>
    </li>)}
  </ol>;
}
