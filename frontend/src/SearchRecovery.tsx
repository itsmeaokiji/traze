import { formatBytes, formatNumber } from './format';
import type { ReceiptWrapper, WorkspaceState } from './types';
import { searchStopText } from './coveragePresentation';

export function DiscoveryAllowance({ workspace }: { workspace?: WorkspaceState }) {
  if (!workspace || workspace.serving_mode !== 'discovery') return null;
  const resources = workspace.resources;
  return <p id="discovery-allowance" className="field-help discovery-allowance">
    {resources.available ? <>Discovery allowance: {formatBytes(resources.usable_response_bytes)} available for new requests on this installation.
      {' '}Ceiling: {formatBytes(resources.session_limit_bytes)}{typeof resources.total_response_bytes === 'number' ? ` · ${formatBytes(resources.total_response_bytes)} counted` : ''}.
      {' '}Each operation allows up to {workspace.limits.bitcoin_body_attempts} Bitcoin block download attempts, {workspace.limits.response_mb_per_lookup} MB of response data,
      {' '}{formatNumber(workspace.limits.http_requests ?? 600)} requests and {formatNumber((workspace.limits.overall_seconds ?? 600) / 60)} minutes.</>
      : 'Discovery allowance could not be verified. Saved evidence remains readable.'}
    {' '}The cumulative allowance is shared across searches and continuations; a new operation does not renew the cumulative allowance.
  </p>;
}

export function recoveryState(wrapper: ReceiptWrapper, workspace?: WorkspaceState) {
  const continuation = wrapper.continuation;
  const allowance = workspace?.resources;
  const available = continuation?.available && allowance?.source_operations_available !== false;
  const reason = allowance?.source_operations_available === false && continuation?.available ? allowance.blocked_reason : continuation?.reason;
  return { available, reason, needsNewLookup: !available && ['source_view_changed', 'continuation_cache_unavailable', 'cache_corrupt', 'continuation_unavailable'].includes(reason || '') };
}

export function SearchRecovery({ wrapper, workspace, busy, onContinue, onOpenSaved, onNewLookup, showNewLookup = true }: {
  wrapper: ReceiptWrapper; workspace?: WorkspaceState; busy: boolean; onContinue?: () => void;
  onOpenSaved?: (id: string) => void; onNewLookup?: () => void; showNewLookup?: boolean;
}) {
  const continuation = wrapper.continuation;
  const discovery = wrapper.receipt.discovery;
  if (!continuation && !discovery) return null;
  if (!discovery && wrapper.receipt.status === 'found' && wrapper.receipt.proofs.length > 0
    && !continuation?.available && continuation?.reason === 'continuation_unavailable') return null;
  const allowance = workspace?.resources;
  const { available, reason, needsNewLookup } = recoveryState(wrapper, workspace);
  const blocked: Record<string, string> = {
    cumulative_response_bytes: 'The remaining allowance is too small to reserve another full Bitcoin block. Saved results remain available. The operator can review the configured ceiling while preserving all counted usage.',
    allowance_unavailable: 'The discovery allowance could not be verified. Saved results remain available.',
    source_operation_active: 'Another source operation is active. Reopen this saved result after it finishes.',
    restored_state_requires_review: 'Restored accounting needs operator review before any new source requests.',
    source_view_changed: 'A source view changed at the last check. This cursor cannot be continued; start a new lookup to investigate the current view.',
    original_extent_processed: 'The original search extent has been processed. Earlier segments keep their own coverage and observation times. This does not establish global publication absence.',
    segment_history_limit: 'This search has reached its retained segment limit. Review the collected evidence before starting another lookup.',
    continuation_changed: 'This saved search cursor could not be validated. No continuation was started.',
    continuation_cache_unavailable: 'Retained Bitcoin bodies are missing for this older search. A new lookup is needed to reconstruct its progress.',
    cache_corrupt: 'Retained Bitcoin data could not be validated. A new lookup is needed.',
    continuation_unavailable: 'This result has no validated continuation cursor. A new lookup is needed before coverage can expand.',
  };
  if (continuation?.reason === 'matching_evidence_available' && !discovery) return null;
  return <section id="search-recovery" className="search-recovery" aria-labelledby="recovery-heading">
    <h3 id="recovery-heading">{discovery ? `Search segment ${discovery.segment}` : 'Continue this search'}</h3>
    {!wrapper.receipt.proofs.length && <p id="recovery-stop">{searchStopText(wrapper.receipt)}</p>}
    {discovery && <p>This result's coverage belongs to this segment. Earlier scans remain historical and have not been refreshed.
      {' '}<a className="text-link" href={`/?receipt=${discovery.parent_receipt_id}`}>Open previous segment</a></p>}
    {available && continuation?.bitcoin_range && <>
      <p>Next segment: Bitcoin <strong>{formatNumber(continuation.bitcoin_range[0])}–{formatNumber(continuation.bitcoin_range[1])}</strong>.
        {' '}Continue from the first unfinished height, using the same Hemi block hash and cumulative allowance.</p>
      {continuation.search_phase === 'estimated_window' && <p className="field-help" id="continuation-window">This segment stays within the initial estimated window. Its unfinished heights are checked before any forward extension.</p>}
      {continuation.search_phase === 'forward_extension' && <p className="field-help" id="continuation-window">The initial estimated window has been processed. This segment searches beyond that estimate; it does not establish global publication absence.</p>}
      <button id="continue-search" className="primary" disabled={busy || !onContinue} onClick={onContinue}>Continue search</button>
      <p className="field-help">This starts one bounded operation. It does not run automatically, renew earlier limits or refresh earlier coverage. Recheck sources only updates source observations.</p>
    </>}
    {!available && reason === 'newer_result_available' && continuation?.latest_receipt_id && <>
      <p>This search has a newer segment. Open it to see the latest progress.</p>
      <button className="secondary" disabled={busy || !onOpenSaved} onClick={() => onOpenSaved?.(continuation.latest_receipt_id!)}>Open latest segment</button>
    </>}
    {!available && reason && blocked[reason] && <p id="continuation-unavailable">{blocked[reason]}</p>}
    {showNewLookup && needsNewLookup && onNewLookup && <button className="secondary" disabled={busy || allowance?.source_operations_available === false} onClick={onNewLookup}>Run new lookup</button>}
    {allowance?.minimum_body_reservation_bytes !== undefined && reason === 'cumulative_response_bytes' && <p className="field-help">The next full-block request needs a {formatBytes(allowance.minimum_body_reservation_bytes)} reservation, in addition to the preserved safety margin and any metadata requests. Repeating the search does not restore allowance.</p>}
  </section>;
}
