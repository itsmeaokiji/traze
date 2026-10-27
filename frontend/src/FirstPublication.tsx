import type { Proof, ReceiptWrapper } from './types';
import { formatDate, formatNumber } from './format';
import { StatusIcon } from './StatusIcon';
import { firstPublicationState } from './firstPublicationState';

export function FirstPublication({ wrapper, proof }: { wrapper: ReceiptWrapper; proof?: Proof }) {
  if (!proof) return null;
  const state = firstPublicationState(wrapper, proof);
  const { assessment } = state;
  const interval = state.exact ? assessment?.result?.interval_evidence?.verified_interval : undefined;
  const tone = state.qualified ? 'pass' : assessment?.status === 'invalid' || state.conflict ? 'warning' : 'neutral';
  const important = state.qualified || state.conflict || assessment?.status === 'invalid' || state.another;
  const visibleIssues = !state.another && (state.conflict || assessment?.status === 'invalid');
  return <section id="first-publication" className={`first-publication first-publication-${tone}`} aria-labelledby="first-publication-heading">
    <div className="section-heading"><h3 id="first-publication-heading"><StatusIcon tone={tone} />{state.title}</h3>
      {state.qualified && proof && <span className="muted">Bitcoin {formatNumber(proof.bitcoin_height)}</span>}
    </div>
    {important && <p className="section-help">{state.description}</p>}
    {visibleIssues && Boolean(assessment?.issues.length) && <ul className="assessment-issues">{assessment!.issues.map((issue, index) => <li key={index}>{issue.message || issue.code}</li>)}</ul>}
    <details id="first-publication-details" className="limits-details assessment-disclosure"><summary>Assessment details</summary>
    {!important && <p className="section-help">{state.description}</p>}
    {state.qualified && state.snapshot && state.context?.snapshot && state.context.domain && <p className="field-help">Hemi snapshot: {formatNumber(state.context.snapshot.number)} · Eligible keystones: {formatNumber(state.context.domain.first_hemi_keystone)}–{formatNumber(state.context.domain.last_hemi_block)}. Future snapshots are outside this assessment.</p>}
    {interval && <p className="field-help">Complete Bitcoin interval: {formatNumber(interval.first_height)}–{formatNumber(interval.last_height)}. {state.qualified ? 'This assessment is separate from the search coverage and unresolved associations below.' : 'Pre-anchor exclusion requires separate justification.'}</p>}
    {!state.another && !visibleIssues && Boolean(assessment?.issues.length) && <ul className="assessment-issues">{assessment!.issues.map((issue, index) => <li key={index}>{issue.message || issue.code}</li>)}</ul>}
    <details className="limits-details"><summary>Assumptions and limits</summary>
      {state.context?.status === 'verified_context_binding' && state.context.snapshot && <>
        <p className="packet-times">Snapshot hash: <code className="assurance-hash">{state.context.snapshot.hash}</code><br />Hemi source observations: {formatDate(state.context.observed_from)}–{formatDate(state.context.observed_through)}. The block timestamp is reported metadata, not a verified wall-clock cutoff.</p>
        <p>Historical profile applicability and source branch validity are externally accepted. Historical execution and deployment are not independently certified. Bitcoin observations retain their original times; this Hemi snapshot does not refresh Bitcoin chain membership.</p>
        <p className="packet-times">Context checksum: <code className="assurance-hash">{state.context.sha256}</code>. The server must explicitly accept this exact context separately from the packet.</p>
      </>}
      {assessment?.source_context && state.exact && <p className="packet-times">Packet prepared: {assessment.source_context.packet_prepared_at ? formatDate(assessment.source_context.packet_prepared_at) : 'Not recorded (legacy snapshot)'}. Receipt completed: {formatDate(assessment.source_context.recorded_at)} · Ordinary source check: {formatDate(assessment.source_context.source_checked_at)}. Packet witnesses retain their own observation times in the verification package. Opening this result does not recheck sources.</p>}
      <p>The conclusion concerns the first applicable Bitcoin block height, including support through a later keystone. It does not identify broadcast order, a unique first transaction, or every co-first transaction.</p>
      {assessment?.assumptions.length ? <ul>{assessment.assumptions.map(item => <li key={item.id}>{item.description}</li>)}</ul> : <p>The first-publication checker requires a supported rule profile, complete interval evidence, authenticated history proofs and an explicit operator policy.</p>}
      <p>Policy: {assessment?.policy_id || 'Not recorded'}. {assessment?.accepted_by_operator ? 'Accepted by this server operator; never accepted from packet fields.' : 'External assumptions have not been accepted by this server operator.'}</p>
      <p>An unconditional independently established global proof is not asserted. These checks do not validate either chain's consensus, replay Hemi execution or derivation, establish current chain membership, or recalculate Bitcoin finality.</p>
    </details>
    </details>
    {state.download && <a id="download-first-publication" className="download-link" href={state.download} download>Download verification package <span aria-hidden="true">↓</span></a>}
  </section>;
}
