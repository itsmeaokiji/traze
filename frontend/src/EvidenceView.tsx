import { useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ApiError } from './api';
import { formatBytes, formatDate, formatNumber, selectedProof } from './format';
import type { Job, ReceiptWrapper, RecoveryEvent, WorkspaceState } from './types';
import { RecordWorkspace } from './RecordWorkspace';
import { PublicationChoices } from './PublicationChoices';
import { resultState, sourceState, sourceAgreementText, sourceDepthText } from './evidenceState';
import { StatusIcon } from './StatusIcon';
import { CopyAction } from './CopyAction';
import { ExternalLink } from './ExternalLink';
import { bitcoinReference } from './evidenceLinks';
import { evidenceSummary } from './evidenceSummary';
import { coverageIssuesText, searchCoverageText, searchStopText } from './coveragePresentation';
import { receiptIssues } from './receiptIssues';
import { historicalSourceTimes, isRecorded } from './recordedEvidence';
import { EvidenceDownload } from './EvidenceDownload';
import { FirstPublication } from './FirstPublication';
import { firstPublicationState } from './firstPublicationState';
import { ResultOverview } from './ResultOverview';
import { ReferenceFinality } from './ReferenceFinality';
import { browseReturn, browseReturnLabel } from './browseNavigation';
import { SearchRecovery, recoveryState } from './SearchRecovery';
import { IndexUpdateStatus } from './IndexUpdateStatus';
export { receiptIssues } from './receiptIssues';

function BitcoinLink({ type, hash, label }: { type: 'block' | 'tx'; hash?: string; label: ReactNode }) {
  const url = bitcoinReference(type, hash);
  return url
    ? <ExternalLink href={url}>{label}</ExternalLink>
    : <span>{label}</span>;
}
function Check({ label, value, note, status = 'unknown' }: { label: string; value: ReactNode; note?: string; status?: 'pass' | 'warning' | 'unknown' }) {
  return <div className="verification-row"><dt>{label}{note && <small>{note}</small>}</dt><dd className={`check-${status}`}><StatusIcon tone={status === 'unknown' ? 'neutral' : status} /><span>{value}</span></dd></div>;
}
export function EvidenceView({ wrapper, fresh, busy, onRecheck, onError, onNewLookup, onContinue, onOpenSaved, workspace, sourceOperationsEnabled = true }: { wrapper: ReceiptWrapper; fresh: boolean; busy: boolean; onRecheck: () => void; onError: (error: unknown) => void; onNewLookup?: () => void; onContinue?: () => void; onOpenSaved?: (id: string) => void; workspace?: WorkspaceState; sourceOperationsEnabled?: boolean }) {
  const [viewChoice, setViewChoice] = useState<{ receiptId: string; index: number } | null>(null);
  const choicesSummary = useRef<HTMLElement>(null);
  const summaryDetails = useRef<HTMLDetailsElement>(null);
  const summaryText = useRef<HTMLTextAreaElement>(null);
  const r = wrapper.receipt;
  const recorded = isRecorded(r);
  const defaultProof = (wrapper.selected_publication ? r.proofs.find(item => item.publication_id === wrapper.selected_publication) : undefined) || selectedProof(r);
  const defaultIndex = defaultProof ? r.proofs.indexOf(defaultProof) : undefined;
  const chosenIndex = viewChoice?.receiptId === wrapper.id ? viewChoice.index : undefined;
  const hasChoice = chosenIndex !== undefined && Number.isInteger(chosenIndex) && chosenIndex >= 0 && chosenIndex < r.proofs.length;
  const displayedIndex = hasChoice ? chosenIndex : defaultIndex;
  const proof = displayedIndex !== undefined ? r.proofs[displayedIndex] : undefined;
  const recordLink = wrapper.record_url ? new URL(wrapper.record_url, window.location.origin) : undefined;
  if (recordLink && proof?.publication_id) recordLink.searchParams.set('publication', proof.publication_id);
  const viewingAnother = hasChoice && chosenIndex !== defaultIndex;
  const sameTransaction = Boolean(viewingAnother && defaultProof && proof && proof.txid === defaultProof.txid && proof.bitcoin_block_hash === defaultProof.bitcoin_block_hash);
  const identity = r.context?.requested_block_identity;
  const requestedHash = r.query.requested_block_hash || r.query.block_hash;
  const sources = sourceState(r);
  const sourceConfirmed = sources === 'confirmed';
  const controlled = r.validation?.scope === 'controlled_provider_responses';
  const origin = recorded ? 'Stored evidence' : fresh ? r.mode === 'live_metadata_refresh' ? 'SOURCE RECHECK' : 'SEARCH RESULT' : 'SAVED RESULT';
  const coverage = r.coverage || {};
  const blocks = coverage.blocks || [];
  const coverageIssues = coverageIssuesText(r);
  const recoveryStopShown = sourceOperationsEnabled && !r.proofs.length && Boolean(wrapper.continuation || r.discovery);
  const { warnings, details: warningDetails } = receiptIssues(r, sourceOperationsEnabled, { primaryStopShown: recoveryStopShown, compact: true });
  const needsNewLookup = recorded || recoveryState(wrapper, workspace).needsNewLookup;
  const sourceAgreement = sourceAgreementText(r);
  const state = resultState(r, Boolean(proof));
  const firstHeight = firstPublicationState(wrapper, proof);
  const returnTo = browseReturn();
  const relatedHeight = proof?.checkpoint.height ?? r.query.covering_checkpoint?.height;
  const summary = viewingAnother
    ? `Viewing another matching proof record from this receipt. ${state.tone === 'pass' ? 'Earlier publications may exist outside the scanned data.' : state.summary}`
    : firstHeight.qualified ? 'Matching publication evidence with a conditional first-publication assessment in the recorded chain view. Review the supported rules and assumptions below.'
      : wrapper.selected_publication && proof ? `Viewing the linked matching publication. ${state.tone === 'pass' ? 'Earlier publications may exist outside the scanned data.' : state.summary}` : state.summary;
  const identityLabels = { matches_current_rpc_view: 'Matched at the last check', different_current_rpc_block: 'Different block hash at the last check', unavailable: 'Unconfirmed at the last check', not_rechecked: 'Current identity not rechecked' };
  const identitySummary = identity?.status === 'matches_current_rpc_view' ? 'Requested block identity: Matched the Hemi RPC view at the last check.'
    : identity?.status === 'different_current_rpc_block' ? 'At the last check, the Hemi RPC reported a different block hash at this height.'
      : recorded ? r.catalogue?.number_resolved_from_retained_view ? 'Block number resolved from the recorded Hemi view. The current block at this height has not been rechecked.' : 'Evidence is bound to the exact requested hash. Current chain membership has not been rechecked.'
        : 'Requested block identity could not be confirmed at the last check.';
  const readableSummary = evidenceSummary(wrapper, displayedIndex, fresh);
  const copyScope = `${wrapper.id}:${displayedIndex ?? 'unresolved'}`;
  const linkedSelection = Boolean(wrapper.selected_publication && wrapper.selected_publication_available);
  const selectionHelp = viewingAnother ? `This is a view selection. Download evidence JSON${sourceOperationsEnabled ? ' and Recheck sources use' : ' uses'} the original JSON evidence export. Copy summary follows the displayed proof record.`
    : firstHeight.qualified ? 'This selection carries the qualified first-publication assessment shown below.'
      : linkedSelection ? 'This view follows the publication identified in the record link. It is not a globally earliest-publication claim.'
        : 'Default selection follows the earliest observed applicable Bitcoin height.';
  function openManualSummary() { if (summaryDetails.current) summaryDetails.current.open = true; summaryText.current?.focus(); summaryText.current?.select(); }
  function chooseProof(index: number) {
    if (!Number.isInteger(index) || index < 0 || index >= r.proofs.length) return;
    setViewChoice(index === defaultIndex ? null : { receiptId: wrapper.id, index });
  }
  function resetProof() { setViewChoice(null); choicesSummary.current?.focus(); }
  const publicationWorkspace = <>
    {proof && r.proofs.length > 1 && <div className="publication-context">
      <div id="publication-view" className={`publication-view${viewingAnother ? ' is-manual' : ''}`}>
        <p id="publication-view-label">{viewingAnother ? sameTransaction ? 'Viewing another proof record' : 'Viewing another publication' : linkedSelection ? 'Linked publication' : 'Default selection'} <span>· Proof record {displayedIndex! + 1} of {r.proofs.length}</span></p>
        {viewingAnother && <button type="button" className="secondary" onClick={resetProof}>{linkedSelection ? 'Back to linked publication' : 'Back to default selection'}</button>}
      </div>
      <PublicationChoices receipt={r} defaultIndex={defaultIndex} displayedIndex={displayedIndex} onSelect={chooseProof} summaryRef={choicesSummary} defaultLabel={linkedSelection ? 'Linked publication' : 'Default selection'} selectionHelp={!viewingAnother && !firstHeight.qualified && !linkedSelection ? selectionHelp : undefined} />
      {(viewingAnother || firstHeight.qualified || linkedSelection) && <p className="field-help publication-selection-help">{selectionHelp}</p>}
    </div>}
    <RecordWorkspace key={`${wrapper.id}:${displayedIndex ?? 'unresolved'}`} receipt={r} proof={proof} copyScope={copyScope} copyDisabled={busy} onCopyError={onError} />
    {relatedHeight !== undefined && <p className="related-publications"><a id="related-publications-link" className="text-link" href={`/browse?keystone_height=${relatedHeight}`}>Browse publications for keystone {formatNumber(relatedHeight)} <span aria-hidden="true">→</span></a></p>}
  </>;
  return <section id="result" data-receipt-id={wrapper.id} className={`result result-${state.tone}`} aria-labelledby="result-heading">
    {returnTo && <a className="text-link browse-return" href={returnTo}>{browseReturnLabel(returnTo)}</a>}
    <div className="result-heading-row">
      <div><p id="result-origin" className="eyebrow">{origin}{controlled ? ' · CONTROLLED TEST DATA' : ''}</p><div className="result-title"><StatusIcon tone={state.tone} /><h2 id="result-heading" tabIndex={-1}>{state.title}</h2></div><ResultOverview wrapper={wrapper} proof={proof} defaultSelection={!viewingAnother} /><p id="result-summary">{summary}</p></div>
      <div className="result-actions">{wrapper.record_url && <CopyAction id="copy-record-link" label="Copy record link" className="secondary" value={recordLink!.href} disabled={busy} />}<CopyAction id="copy-summary" label="Copy summary" className="secondary summary-copy" value={readableSummary} scope={copyScope} disabled={busy} onManualCopy={openManualSummary} /><EvidenceDownload wrapper={wrapper} /></div>
    </div>
    {warnings.length > 0 && <div id="issues-section" className="issues-section"><h3><StatusIcon tone="warning" />Warnings</h3><ul id="issues-list">{warnings.map(message => <li key={message}>{message}</li>)}</ul>{Boolean(warningDetails?.length) && <details id="warning-details" className="limits-details"><summary>Warning details</summary><ul>{warningDetails!.map(message => <li key={message}>{message}</li>)}</ul></details>}</div>}
    {wrapper.selected_publication_available === false && <p className="coverage-issues" role="alert">The linked publication is unavailable in this indexed view. Other available evidence is shown below.</p>}
    {proof && publicationWorkspace}
    <dl id="evidence-assessment" className="evidence-assessment" aria-label="Evidence status">
      <div><dt>Proof checks</dt><dd><StatusIcon tone={wrapper.verification.proofs_checked > 0 ? 'pass' : 'neutral'} /><span>{wrapper.verification.proofs_checked > 0 ? `${formatNumber(wrapper.verification.proofs_checked)} proof record${wrapper.verification.proofs_checked === 1 ? '' : 's'} checked` : 'No publication proof to check'}</span></dd></div>
      <div><dt>Source checks</dt><dd><StatusIcon tone={sources === 'conflict' ? 'warning' : recorded ? 'neutral' : sourceConfirmed ? 'pass' : 'warning'} /><span>{sourceAgreement}</span></dd></div>
      <div><dt>Coverage for this result</dt><dd><StatusIcon tone={coverageIssues ? 'warning' : 'neutral'} /><span>{formatNumber(blocks.length)} Bitcoin block{blocks.length === 1 ? '' : 's'}{recorded ? ' from retained data' : r.mode === 'live_metadata_refresh' ? ' from the original search' : ' scanned'}{coverageIssues ? ` · ${coverageIssues}` : ''}</span></dd></div>
    </dl>
    <IndexUpdateStatus initial={wrapper.index_update} receiptId={wrapper.id} token={workspace?.csrf_token || ''} enabled={sourceOperationsEnabled && workspace?.serving_mode === 'discovery'} busy={busy} />
    <div className="result-context" aria-label="Source observations"><p id="observation-line" className="observation-line"><span className="context-label">Source observations</span>{recorded ? `Result generated: ${formatDate(r.completed_at)} · Historical source observations: ${historicalSourceTimes(r)}.` : <>Receipt completed: {formatDate(r.completed_at)} · Ordinary source check: {formatDate(r.context?.checked_at)}{controlled ? ' · Controlled test data.' : fresh || !sourceOperationsEnabled ? '' : ' · Use Recheck sources for a new check.'}{r.mode === 'live_metadata_refresh' ? ' Original scan coverage is preserved.' : ''}</>}</p>
    {requestedHash && identity && <p id="input-identity-line" className={`input-identity-line${identity.status !== 'matches_current_rpc_view' ? ' is-warning' : ''}`}>{identitySummary}</p>}</div>
    <div className="result-detail-grid">
      <section className="checks-section" aria-labelledby="checks-heading">
        <details id="verification-details" className="evidence-disclosure"><summary><h3 id="checks-heading">Verification details</h3></summary>
        <h4 className="check-group-heading">Proof checks</h4>
        <p className="section-help">Recomputed from the supplied Bitcoin headers and Hemi RPC data. Passing these checks does not confirm the current source view.</p>
        <dl id="verification-list" className="verification-list">
          {wrapper.verification.proofs_checked > 0 ? <>
            <Check label="Publication payload" value="Passed" note="The HEMI publication data matches the reconstructed keystone" status="pass" />
            <Check label="Transaction inclusion" value="Passed" note="The transaction ID and Merkle proof match the supplied Bitcoin block header" status="pass" />
            <Check label="Bitcoin header check" value="Passed" note="The header hash and proof of work were checked against the stated target" status="pass" />
            <Check label="Hemi ancestry checks" value="Passed" note="Parent-hash links connect the requested block to the published keystone in the supplied Hemi RPC data" status="pass" />
          </> : <Check label="Publication proof" value="None to check" note="Search coverage does not establish global publication absence" />}
        </dl>
        <h4 className="check-group-heading">{recorded ? 'Current source checks' : 'Recorded source checks'}</h4>
        <p className="section-help">{recorded ? 'No new source check was performed for this generated target. Historical source observations are listed above.' : `Sources last checked: ${formatDate(r.context?.checked_at)}. Opening saved evidence does not perform a new source check.`}</p>
        <dl id="source-check-list" className="verification-list">
          {(identity || r.query.input_type === 'hash') && <Check label="Requested Hemi block identity" value={identity ? identityLabels[identity.status] : 'Not recorded'} note={recorded ? 'Current identity was not compared with a live source for this retrieval' : 'Compared with the Hemi RPC view at the recorded check time'} status={recorded ? 'unknown' : identity?.status === 'matches_current_rpc_view' ? 'pass' : identity ? 'warning' : 'unknown'} />}
          <Check label="Source checks" value={sourceAgreement} note={recorded ? 'Historical observations retain their original target and scope' : 'Recorded checks of the Hemi RPC and Bitcoin API views; not full independent chain validation'} status={sources === 'conflict' ? 'warning' : recorded ? 'unknown' : sourceConfirmed ? 'pass' : 'warning'} />
        </dl>
        <details className="limits-details"><summary>About these checks</summary><p>Proof checks use the supplied Bitcoin headers and Hemi RPC data. Source checks describe the views returned at the recorded check time.</p><p>Traze does not validate the full Bitcoin header chain or independently execute Hemi blocks. Named reference calculations are shown separately when supported; deployed Hemi finality equivalence and PoP reward eligibility are not established.</p></details>
        </details>
      </section>
      <section className="coverage-section" aria-labelledby="coverage-heading">
        <div className="section-heading"><h3 id="coverage-heading">Coverage for this result</h3></div>
        <p id="coverage-summary" className="section-help">{searchCoverageText(r)}</p>
        {coverageIssues && <p id="coverage-issues" className="coverage-issues">{coverageIssues}. Scan gaps and unresolved associations are separate.</p>}
        {!recoveryStopShown && <p id="stop-reason" className="field-help">{searchStopText(r)}</p>}
        <details id="coverage-details" className="limits-details"><summary>Scanned Bitcoin blocks</summary>{recoveryStopShown && <p id="stop-reason" className="field-help">{searchStopText(r)}</p>}<div className="table-scroll"><table className="coverage-table"><thead><tr><th>Bitcoin block</th><th>Transactions</th><th title="Transaction outputs containing HEMI publication data; candidates may not be applicable to this block">Publication candidates</th></tr></thead><tbody id="coverage-body">
          {blocks.length ? blocks.map(block => <tr key={`${block.height}:${block.hash}`}><td><BitcoinLink type="block" hash={block.hash} label={formatNumber(block.height)} /></td><td>{formatNumber(block.transaction_count)}</td><td>{formatNumber(block.hemi_candidates)}</td></tr>) : <tr><td colSpan={3} className="muted">No complete Bitcoin block scan was recorded.</td></tr>}
        </tbody></table></div></details>
      </section>
    </div>
    {sourceOperationsEnabled && <section id="result-search-actions" className="result-search-actions" aria-labelledby="search-actions-heading">
      <h3 id="search-actions-heading">Search actions</h3>
      <div className="source-action-buttons">{needsNewLookup && onNewLookup && <button id="new-lookup-button" className="secondary" disabled={busy || workspace?.resources.source_operations_available === false} onClick={onNewLookup} title="Run the normal source search for this input, using any specified Bitcoin range.">Run new lookup</button>}<button id="refresh-button" className="secondary" disabled={busy || !r.query.header || !r.query.covering_checkpoint} onClick={onRecheck} title="Checks source views again. Original scan coverage is preserved.">Recheck sources <span aria-hidden="true">↻</span></button></div>
      <p className="field-help">{needsNewLookup && onNewLookup && 'Run new lookup starts a new source search. '}Recheck sources checks source views again; it does not expand search coverage. Source operations use the existing discovery allowance.</p>
      <SearchRecovery wrapper={wrapper} workspace={workspace} busy={busy} onContinue={onContinue} onOpenSaved={onOpenSaved} showNewLookup={false} />
    </section>}
    {!proof && publicationWorkspace}
    <FirstPublication wrapper={wrapper} proof={proof} />
    <ReferenceFinality wrapper={wrapper} proof={proof} />
    {(proof || wrapper.record_url) && <details id="record-details" className="limits-details"><summary>Result details</summary>
      {proof && <><p className="section-help">Checks for the displayed proof record. Exact identifiers are in Selected record details.</p><dl id="record-details-list" className="verification-list">
        <Check label="Checked Hemi ancestry links" value={formatNumber(proof.ancestry.length - 1)} />
        <Check label="Bitcoin depth from source checks" value={sourceDepthText(r, proof)} note="Requires confirmed source checks for this result and a recorded depth. This is not a Hemi finality value." />
        <Check label={firstHeight.qualified ? 'Unconditional global proof' : 'Globally earliest publication'} value="Not established" />
      </dl></>}
    {wrapper.record_url && <p className="record-view-note">The copied record link identifies the Hemi block and selected publication when available. Indexed evidence may change as collection progresses; Download evidence JSON preserves the original displayed snapshot.</p>}</details>}
    <details id="evidence-summary" ref={summaryDetails} className="limits-details evidence-summary"><summary>Readable evidence summary</summary><p className="section-help">Copy summary uses this text for the displayed proof record. Download evidence JSON exports the original evidence for offline verification.</p><label className="sr-only" htmlFor="evidence-summary-text">Evidence summary</label><textarea id="evidence-summary-text" ref={summaryText} readOnly value={readableSummary} rows={14} spellCheck={false} /></details>
  </section>;
}

function recoveryEventLabel(event: RecoveryEvent): string {
  const labels: Record<string, string> = {
    hemi_primary_unavailable: 'Primary Hemi RPC unavailable',
    hemi_fallback_activated: `Alternate Hemi RPC activated after rechecking ${event.revalidated_observations ?? 0} earlier observations`,
    hemi_source_rejected: 'Hemi source rejected', hemi_alternate_unavailable: 'Alternate Hemi source unavailable',
    hemi_observation_changed: 'Hemi critical data changed during this operation', timestamp_primary_unavailable: 'Primary timestamp lookup unavailable',
    primary_timestamp_hint: 'Primary timestamp hint checked', timestamp_fallback_hint: 'Blockstream timestamp recovery hint checked',
  };
  return `${labels[event.kind] || event.kind}: ${event.source || ''}${event.reason || event.message ? ` · ${event.reason || event.message}` : ''}`;
}
export function Diagnostics({ workspace, result, activity, error, running }: { workspace?: WorkspaceState; result?: ReceiptWrapper; activity?: Job; error?: Error; running: boolean }) {
  const r = result?.receipt;
  const metrics = r?.resources || {};
  const recovery = r?.source_recovery;
  const events = [...(recovery?.hemi_events || []), ...(recovery?.timestamp_events || [])];
  const raw = r ? receiptIssues(r).raw : [];
  const coverage = r?.coverage;
  const hemi = recovery?.hemi_rpc_used || r?.sources?.hemi_rpc_used || r?.sources?.hemi_rpc;
  const timestampRoute = recovery?.timestamp_recovery_used ? 'Blockstream metadata recovery' : recovery?.timestamp_events?.length ? 'primary timestamp API' : 'not needed for this operation';
  return <details id="developer-diagnostics" className="developer-diagnostics"><summary>Developer diagnostics</summary>
    <p id="session-budget">{!workspace ? 'Workspace response-data allowance: loading…' : workspace.resources.available
      ? `Workspace response-data allowance: ${formatBytes(workspace.resources.total_response_bytes)} used / ${formatBytes(workspace.resources.session_limit_bytes)}. Per-search limits: ${workspace.limits.response_mb_per_lookup} MB response data · ${workspace.limits.bitcoin_body_attempts} Bitcoin block download attempts · ${workspace.limits.ancestry_links} Hemi ancestry links.`
      : 'Workspace response-data allowance unavailable. Existing accounting is preserved.'}</p>
    <p>These limits apply to this local installation and its recorded operations. Response-data accounting excludes TLS/TCP overhead and may retain conservative reservations for interrupted requests.</p>
    {error instanceof ApiError && <p id="operation-error" className="input-identity-line">{error.diagnostic}</p>}
    {activity && <div id="job-activity"><h3>Latest operation activity</h3><ol id="progress-events" className="activity-list">{activity.events.map((event, index) => <li key={index}>{event.message}</li>)}</ol></div>}
    {r && !running && <div id="result-diagnostics">
      <p id="result-resources" className="result-resources">{r.mode === 'live_metadata_refresh' ? 'Source recheck' : 'Search'} resources at the recorded operation: {formatBytes(metrics.response_bytes_this_run)} counted response data · {formatNumber(metrics.bitcoin_body_attempts)} Bitcoin block download attempts · {formatNumber(metrics.cache_body_reads)} cached Bitcoin blocks rechecked · {formatNumber(metrics.http_requests)} HTTP requests · {Number.isFinite(metrics.elapsed_seconds) ? metrics.elapsed_seconds?.toFixed(1) : '—'}s elapsed. Operation limits: {formatBytes(metrics.limits?.response_bytes)} response data · {formatNumber(metrics.limits?.bitcoin_body_attempts)} Bitcoin block download attempts · {formatNumber(metrics.limits?.requests)} requests · {formatNumber(metrics.limits?.overall_seconds)}s.</p>
      <p id="coverage-diagnostics" className="input-identity-line">{formatNumber(coverage?.gaps?.length || 0)} scan gaps · {formatNumber(coverage?.unresolved_candidates?.length || 0)} unresolved publication candidates · {formatNumber(coverage?.unsupported_payloads || 0)} unsupported payloads. Recorded search stop: {coverage?.stop_reason || 'not recorded'}. Distinct Bitcoin API endpoints crosschecked: {r.context?.distinct_bitcoin_api_crosscheck === true ? 'yes' : 'not established'}.</p>
      {hemi && <p id="source-route-line" className="input-identity-line">Hemi RPC used at the recorded check: {hemi}. Bitcoin API configuration: {r.sources?.bitcoin_apis?.join(', ') || 'not recorded'}. Timestamp route: {timestampRoute}. Provider backend independence is not established.</p>}
      {events.length > 0 && <details id="source-recovery-details" className="limits-details"><summary>Source recovery details</summary><ul id="source-recovery-events" className="activity-list">{events.map((event, index) => <li key={index}>{recoveryEventLabel(event)}</li>)}</ul></details>}
      {raw.length > 0 && <details id="raw-issues" className="limits-details"><summary>Raw diagnostic messages</summary><ul id="raw-issues-list" className="activity-list">{raw.map((issue, index) => <li key={index}>{issue.code ? `${issue.code}: ` : ''}{issue.message || 'No diagnostic message recorded'}</li>)}</ul></details>}
    </div>}
  </details>;
}
