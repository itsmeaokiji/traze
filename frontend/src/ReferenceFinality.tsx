import { useEffect, useState } from 'react';
import type { Proof, ReceiptWrapper } from './types';
import { formatDate, formatNumber, shortHash } from './format';
import { finalityView } from './finalityPresentation';
import { bitcoinReference } from './evidenceLinks';
import { ExternalLink } from './ExternalLink';

export function ReferenceFinality({ wrapper, proof }: { wrapper: ReceiptWrapper; proof?: Proof }) {
  const [download, setDownload] = useState<string>();
  const assessment = wrapper.finality;
  const view = finalityView(wrapper, proof);
  const showPanel = Boolean(assessment && (wrapper.receipt.proofs.length > 0 || view.kind !== 'unavailable'));
  useEffect(() => {
    if (!assessment || !showPanel || typeof URL.createObjectURL !== 'function') { setDownload(undefined); return; }
    const url = URL.createObjectURL(new Blob([JSON.stringify(assessment, null, 2)], { type: 'application/json' }));
    setDownload(url);
    return () => URL.revokeObjectURL(url);
  }, [assessment, showPanel]);
  if (!assessment || !showPanel) return null;
  const model = assessment.calculation;
  const reference = view.reference;
  const observation = assessment.input_binding?.retained_tip_observation?.observed_at || model?.source_context.inputs.observed_at;
  return <section id="reference-finality" className="reference-finality" aria-labelledby="reference-finality-heading">
    <div className="section-heading"><h3 id="reference-finality-heading">Reference finality calculation</h3><span className="finality-scope">{view.kind === 'block' ? 'Conditional block model' : view.kind === 'publication' ? 'Publication only' : 'Unavailable'}</span></div>
    <p id="finality-description" className="section-help">{view.description}</p>
    <p id="finality-qualification" className="field-help">Recorded model result. Deployed Hemi equivalence and current-chain finality are not established.</p>
    {view.values && model && (assessment.source_conflict || model.source_context.status !== 'recorded_consistent') && <p id="finality-source-warning" className="coverage-issues">Recorded source observations are {assessment.source_conflict || model.source_context.status === 'conflicting' ? 'conflicting' : 'incomplete'}; these numbers are publication-only arithmetic.</p>}
    <details id="reference-finality-disclosure" className="limits-details assessment-disclosure"><summary>Calculation details and export</summary>
    {view.values && model && reference && <>
      <dl className="finality-metrics" aria-label="Recorded reference model values">
        <div><dt>BFG reference effective confirmations</dt><dd id="finality-effective">{formatNumber(view.values.effective_confirmations)}</dd></div>
        <div><dt>BFG reference threshold</dt><dd id="finality-threshold">{view.values.super_finality ? 'Reached' : 'Not reached'}</dd><small>T − H ≥ 10</small></div>
        <div><dt>Inclusive Bitcoin depth</dt><dd id="finality-depth">{formatNumber(view.values.raw_bitcoin_confirmations)}</dd><small>T − H + 1</small></div>
      </dl>
      <p id="finality-reference" className="field-help">{view.kind === 'block' ? 'Block reference' : 'Inspected publication'}: Bitcoin {formatNumber(reference.bitcoin_height)} · Keystone {formatNumber(reference.keystone_number)} · <ExternalLink href={bitcoinReference('tx', reference.txid)!}>{shortHash(reference.txid)}</ExternalLink> · Output {reference.vout}.</p>
      {view.kind === 'block' && <p id="finality-selection-note" className="field-help">Inspecting another publication does not change this Hemi-block reference.</p>}
      <p id="finality-observation" className="field-help">Recorded Bitcoin tip: {formatNumber(model.bitcoin_tip.height)} · Source observation: {formatDate(observation)}. Opening this result does not refresh either value.</p>
      <details id="finality-ama" className="limits-details"><summary>Compare the AMA model</summary><p>The separately named AMA model uses T − H − 9. Its signed counter is <strong>{formatNumber(view.ama?.signed_counter)}</strong>. Its zero boundary differs from the pinned BFG reference threshold. Deployed compatibility remains unverified.</p></details>
    </>}
    {model && <details id="finality-details" className="limits-details"><summary>Calculation rules and assumptions</summary>
      <p>Profile: <code>{model.profile.id}</code>. Effective confirmations = T − H; reference threshold = 10. The complete BFG RPC handler is not reproduced.</p>
      <p>Recorded Bitcoin tip hash: <code>{model.bitcoin_tip.hash}</code>. Source consistency: {model.source_context.status}. {model.source_context.accepted_by_caller ? 'The server operator selected this exact source context.' : 'No matching source-context acceptance establishes a block calculation.'}</p>
      {model.first_height_evidence.context?.snapshot && <p>Separate Hemi snapshot: {formatNumber(model.first_height_evidence.context.snapshot.number)} · Observed {formatDate(model.first_height_evidence.context.observed_from)}–{formatDate(model.first_height_evidence.context.observed_through)}. The Hemi snapshot and Bitcoin tip are not a certified simultaneous cross-chain snapshot.</p>}
      <ul>{model.source_context.assumptions.map(item => <li key={item}>{item}</li>)}</ul>
      <p>Source-context checksum: <code>{model.source_context.sha256}</code>. Calculation receipt checksum: <code>{model.receipt_sha256}</code>.</p>
      <p>The calculation rechecks supplied publication evidence. It does not validate either chain's consensus or Hemi execution, establish unconditional global completeness, or verify PoP reward eligibility.</p>
      <p>Calculation JSON preserves all checked publication calculations and the separate block reference. Download the original receipt and any verification package separately to recompute the evidence.</p>
    </details>}
    <a id="download-calculation" className="download-link" href={download} aria-disabled={!download || undefined} download={`traze-finality-${wrapper.receipt.query.block_number ?? 'unresolved'}.json`}>Download calculation JSON <span aria-hidden="true">↓</span></a>
    </details>
  </section>;
}
