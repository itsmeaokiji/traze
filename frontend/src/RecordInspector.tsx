import type { ReactNode } from 'react';
import { formatDate, formatNumber } from './format';
import type { Proof, Receipt } from './types';
import type { RouteRole } from './PublicationRoute';
import { CopyAction } from './CopyAction';
import { ExternalLink } from './ExternalLink';
import { bitcoinReference, recordedBitcoinSources, recordedHemiSource } from './evidenceLinks';
import { historicalSourceTimes, isRecorded } from './recordedEvidence';

const titles: Record<RouteRole, string> = { requested: 'Requested Hemi block', covering: 'Covering keystone', later: 'Later keystone', publication: 'Bitcoin publication' };

function Fact({ label, value, identifier = false }: { label: string; value?: ReactNode; identifier?: boolean }) {
  const canCopy = identifier && typeof value === 'string' && /^(?:0x)?[0-9a-f]{64}$/i.test(value);
  return <div className="inspector-fact"><dt>{label}</dt><dd className={identifier ? 'inspector-identifier' : undefined}><span className="inspector-value">{value === undefined || value === null || value === '' ? 'Unavailable' : value}</span>{canCopy && <CopyAction label={`Copy ${label}`} value={value as string} compact />}</dd></div>;
}
function Reference({ type, hash, children }: { type: 'block' | 'tx'; hash: string; children: ReactNode }) {
  const url = bitcoinReference(type, hash);
  return url ? <ExternalLink href={url}>{children}</ExternalLink> : null;
}

export function RecordInspector({ receipt, proof, role }: { receipt: Receipt; proof?: Proof; role: RouteRole }) {
  const cover = receipt.query.covering_keystone;
  const identity = receipt.context?.requested_block_identity;
  const recordedHash = receipt.query.block_hash;
  const inputHash = receipt.query.requested_block_hash;
  const sourceConfirmed = receipt.context?.current_view_confirmed === true;
  const recorded = isRecorded(receipt);
  const safeHemiSource = recordedHemiSource(receipt);
  const bitcoinSources = recordedBitcoinSources(receipt);
  const identityText = identity?.status === 'matches_current_rpc_view' ? 'Matched the Hemi RPC view at the recorded check'
    : identity?.status === 'different_current_rpc_block' ? 'Different RPC block hash at the recorded check'
      : identity?.status === 'not_rechecked' ? 'Current identity not rechecked'
        : identity?.status === 'unavailable' ? 'Unconfirmed at the recorded check' : 'Not separately recorded';
  const relationship = !proof ? 'No publication association was resolved in this result.'
    : proof.checkpoint.height === cover ? 'Encoded in the displayed publication transaction.'
      : 'Supported through the displayed later-keystone publication.';

  return <aside id="record-inspector" tabIndex={-1} className={`record-inspector inspector-${role === 'publication' ? 'bitcoin' : 'hemi'}`} aria-labelledby="record-inspector-title">
    <div className="inspector-heading"><p className="eyebrow">SELECTED RECORD</p><h3 id="record-inspector-title" aria-live="polite" aria-atomic="true">{titles[role]}</h3></div>
    <div className="inspector-content" key={role}>
    <p className="inspector-intro">Details from this receipt. Selecting a record does not recheck sources.</p>
    <dl className="inspector-facts">
      {role === 'requested' && <>
        <Fact label="Hemi block number" value={receipt.query.block_number !== null ? formatNumber(receipt.query.block_number) : 'Unresolved height'} />
        <Fact label="Recorded Hemi block hash" value={recordedHash} identifier />
        {inputHash && inputHash !== recordedHash && <Fact label="Requested hash" value={inputHash} identifier />}
        <Fact label="Block identity" value={identityText} />
        {identity?.current_hash && identity.current_hash !== (recordedHash || inputHash) && <Fact label="RPC block hash at the last check" value={identity.current_hash} identifier />}
        <Fact label="Covering keystone height" value={cover !== undefined ? formatNumber(cover) : 'Unresolved'} />
      </>}
      {role === 'covering' && <>
        <Fact label="Keystone height" value={cover !== undefined ? formatNumber(cover) : 'Unresolved'} />
        <Fact label="Keystone ID" value={receipt.query.covering_checkpoint?.keystone_id} identifier />
        <Fact label="Relationship to the displayed publication" value={relationship} />
      </>}
      {role === 'later' && proof && <>
        <Fact label="Published keystone height" value={formatNumber(proof.checkpoint.height)} />
        <Fact label="Published keystone ID" value={proof.checkpoint.keystone_id} identifier />
        <Fact label="Checked Hemi ancestry links" value={formatNumber(proof.ancestry.length - 1)} />
        <Fact label="Relationship" value="Encoded in the transaction; supplied Hemi parent-hash links support the requested block." />
      </>}
      {role === 'publication' && (proof ? <>
        <Fact label="Publication transaction ID" value={proof.txid} identifier />
        <Fact label="Containing Bitcoin block" value={formatNumber(proof.bitcoin_height)} />
        <Fact label="Bitcoin block hash" value={proof.bitcoin_block_hash} identifier />
        <Fact label="Published keystone height" value={formatNumber(proof.checkpoint.height)} />
      </> : <>
        <Fact label="Publication status" value="Unresolved" />
        <Fact label="Result scope" value={receipt.status === 'not_found_within_coverage' ? 'No applicable publication was found in the scanned range. Publications may exist outside this range.' : 'Publication evidence could not be resolved. This result does not establish publication absence.'} />
      </>)}
    </dl>
    <div className="inspector-source-links" aria-label="Record source links">
      {role !== 'publication' && (safeHemiSource ? <ExternalLink href={safeHemiSource}>Hemi RPC source</ExternalLink> : <span className="muted">Hemi source not recorded</span>)}
      {role === 'publication' && <>
        {proof && <><Reference type="tx" hash={proof.txid}>View transaction</Reference><Reference type="block" hash={proof.bitcoin_block_hash}>View Bitcoin block</Reference></>}
        {bitcoinSources.map(source => <ExternalLink key={source} href={source}>{source.includes('blockstream') ? 'Blockstream API' : 'mempool.space API'}</ExternalLink>)}
      </>}
    </div>
    <div className="inspector-observation"><span className="detail-label">{recorded ? 'Historical source observation time' : 'Recorded source-check time'}</span><p>{recorded ? historicalSourceTimes(receipt) : formatDate(receipt.context?.checked_at)}</p><p className={sourceConfirmed ? 'inspector-confirmed' : 'inspector-unconfirmed'}>{recorded ? 'Current source views have not been rechecked for this target.' : sourceConfirmed ? 'Source confirmation recorded for this operation.' : 'Source confirmation is incomplete for this operation.'}</p></div>
    </div>
  </aside>;
}
