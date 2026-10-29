import type { ReactNode } from 'react';
import { ChainMark } from './ChainMark';
import { formatNumber, shortHash } from './format';
import type { Proof, Receipt } from './types';
import { ExternalLink } from './ExternalLink';
import { CopyAction } from './CopyAction';

export type RouteRole = 'requested' | 'covering' | 'later' | 'publication';
type RouteRecord = {
  role: RouteRole;
  chain: 'hemi' | 'bitcoin';
  label: string;
  value: ReactNode;
  note: ReactNode;
  resolved: boolean;
};

function RouteEdge({ observed, direction }: { observed: boolean; direction: 'incoming' | 'outgoing' }) {
  return <span className={`route-edge route-${direction}${observed ? '' : ' is-unresolved'}`} data-evidence={observed ? 'observed' : 'unresolved'} aria-hidden="true" />;
}

export function PublicationRoute({ receipt, proof, selectedRole, onSelect, copyScope, copyDisabled, onCopyError }: {
  receipt: Receipt; proof?: Proof; selectedRole?: RouteRole; onSelect?: (role: RouteRole) => void;
  copyScope?: string; copyDisabled?: boolean; onCopyError?: (error: Error) => void;
}) {
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
    role: 'publication', chain: 'bitcoin', label: proof ? 'Containing Bitcoin block' : 'Bitcoin publication',
    value: proof ? <span className="route-publication-value">{/^[0-9a-f]{64}$/i.test(proof.bitcoin_block_hash)
      ? <ExternalLink href={`https://blockstream.info/block/${proof.bitcoin_block_hash}`} title="Open the containing Bitcoin block in a new tab"><span className="sr-only">block </span>{' '}{formatNumber(proof.bitcoin_height)}</ExternalLink>
      : formatNumber(proof.bitcoin_height)}</span> : 'Unresolved',
    note: proof ? <><div className="route-transaction-reference"><span>Transaction</span>{/^[0-9a-f]{64}$/i.test(proof.txid)
      ? <><ExternalLink id="transaction-link" href={`https://blockstream.info/tx/${proof.txid}`} title="Open the publication transaction in a new tab">{shortHash(proof.txid)}</ExternalLink><CopyAction id="copy-txid" label="Copy transaction ID" value={proof.txid} scope={copyScope} disabled={copyDisabled} onError={onCopyError} compact /></>
      : <span>ID unavailable</span>}</div><span className="route-output" title="Transaction outputs are numbered from 0.">{Number.isInteger(proof.vout_index) && proof.vout_index! >= 0 ? <>Transaction output {formatNumber(proof.vout_index)}<span className="sr-only"> (zero-based index).</span></> : 'Transaction output not recorded'}</span></> : receipt.status === 'not_found_within_coverage' ? 'No applicable match in the scanned range.' : 'Publication evidence is incomplete.',
    resolved: Boolean(proof),
  });
  // Retained proof relationships do not depend on a fresh provider confirmation.
  // An unresolved search draws no solid publication route, even when Hemi records are known.
  const edgeObserved = (index: number) => Boolean(proof && records[index]?.resolved && records[index + 1]?.resolved);

  return <ol id="association" role="list" className={`association${later ? ' has-later-keystone' : ''}`} aria-label="Hemi to Bitcoin association" data-publication-status={proof ? 'observed' : 'unresolved'}>
    {records.map((record, index) => <li key={record.role} data-route-role={record.role} className={`route-record chain-${record.chain}${record.resolved ? '' : ' is-unresolved'}${selectedRole === record.role ? ' is-selected' : ''}`}>
      <div className="route-track">
        {index > 0 ? <RouteEdge direction="incoming" observed={edgeObserved(index - 1)} /> : <span className="route-edge-spacer" aria-hidden="true" />}
        {onSelect ? <button type="button" className="route-node" aria-label={`Inspect ${record.role === 'publication' ? 'Bitcoin publication' : record.label}`} aria-pressed={selectedRole === record.role} aria-controls="record-inspector" onClick={() => onSelect(record.role)}><ChainMark chain={record.chain} /></button> : <span className="route-node"><ChainMark chain={record.chain} /></span>}
        {index < records.length - 1 ? <RouteEdge direction="outgoing" observed={edgeObserved(index)} /> : <span className="route-edge-spacer" aria-hidden="true" />}
      </div>
      <div className="route-copy"><span className="route-label"><span className="detail-label">{record.label}</span>{selectedRole === record.role && <span className="route-selected-tag">Selected</span>}</span><span className="step-value">{record.value}</span><div className="step-note">{record.note}</div></div>
    </li>)}
  </ol>;
}
