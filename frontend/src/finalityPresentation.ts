import type { FinalityAssessment, FinalityPublication, FinalityValues, Proof, ReceiptWrapper } from './types';
import { formatDate, formatNumber } from './format';

export const FINALITY_SCHEMA = 'traze-web-finality-assessment-v1';
export const BFG_PROFILE = 'bfg-v2-464bcdf0-reference';
export function samePublication(value: FinalityPublication, proof: Proof): boolean {
  return value.bitcoin_height === proof.bitcoin_height && value.bitcoin_hash === proof.bitcoin_block_hash.toLowerCase()
    && value.txid === proof.txid.toLowerCase() && value.keystone_number === proof.checkpoint.height
    && value.tx_index === proof.tx_index && value.vout === proof.vout_index;
}

export function finalityReason(value?: FinalityAssessment): string {
  const code = value?.issues[0]?.code;
  if (code === 'finality_publication_missing') return 'No positive publication evidence is available to calculate.';
  if (code === 'finality_tip_missing') return 'A recorded Bitcoin tip height and hash are required. This result does not contain them.';
  if (code === 'finality_tip_conflict' || value?.status === 'conflicting') return 'Recorded Bitcoin tips conflict. No calculation is displayed.';
  if (code === 'finality_busy') return 'Calculation capacity is busy. The publication evidence remains available.';
  if (code === 'time_budget_exhausted' || code === 'read_capacity' || code === 'finality_packet_budget') return 'The calculation could not finish within its work limits. Publication evidence remains separate.';
  return 'A supported calculation is unavailable for this evidence. This is an unknown result, not zero finality.';
}

export function finalityView(wrapper: ReceiptWrapper, proof?: Proof): {
  kind: 'block' | 'publication' | 'unavailable'; values?: FinalityValues; reference?: FinalityPublication;
  ama?: FinalityValues; description: string;
} {
  const value = wrapper.finality;
  const model = value?.calculation;
  if (!value || value.schema !== FINALITY_SCHEMA || !value.recorded_context_only || value.network_requests !== 0
    || !model || model.schema !== 'traze-offline-finality-calculation-v1' || model.profile.id !== BFG_PROFILE
    || model.assurance.deployed_finality_equivalence_verified || model.assurance.globally_first_proven
    || model.query.hash !== wrapper.receipt.query.block_hash?.toLowerCase() || model.query.number !== wrapper.receipt.query.block_number) {
    return { kind: 'unavailable', description: finalityReason(value) };
  }
  const block = model.block_calculation;
  const comparison = value.comparison?.profile.id === 'ama-signed-nine-block-model' && value.comparison.same_checked_inputs ? value.comparison : undefined;
  if (block.status === 'calculated_under_accepted_recorded_assumptions' && block.values && block.reference_publication
    && !value.source_conflict
    && model.first_height_evidence.status === 'conditional_reference_verified'
    && model.source_context.accepted_by_caller && model.source_context.status === 'recorded_consistent'
    && value.operator_source_acceptance === 'exact_context_selected' && value.input_binding?.kind === 'original_receipt') {
    return { kind: 'block', values: block.values, reference: block.reference_publication,
      ama: comparison?.block_values || undefined,
      description: 'Conditional Hemi-block model using the checked first-height reference and separately accepted recorded-source assumptions.' };
  }
  const selected = proof && model.publication_calculations.find(item => samePublication(item.publication, proof));
  if (!selected) return { kind: 'unavailable', description: 'The inspected publication has no matching checked calculation.' };
  return { kind: 'publication', values: selected.values, reference: selected.publication,
    ama: comparison?.publication_calculations.find(item => proof && samePublication(item.publication, proof))?.values,
    description: 'Publication-only arithmetic from recorded inputs. A Hemi-block reference has not been established under the required conditions.' };
}

export function finalitySummary(wrapper: ReceiptWrapper, proof?: Proof): string[] {
  if (!wrapper.finality) return [];
  const view = finalityView(wrapper, proof);
  const model = wrapper.finality.calculation;
  const lines = ['', 'Reference finality calculation:', view.description];
  if (view.values && model && view.reference) {
    lines.push(`Scope: ${view.kind === 'block' ? 'Conditional Hemi-block model' : 'Inspected publication only'}`,
      `Profile: ${model.profile.id}`,
      `Reference publication: Bitcoin ${formatNumber(view.reference.bitcoin_height)}, transaction ${view.reference.txid}, output ${view.reference.vout}, keystone ${formatNumber(view.reference.keystone_number)}.`,
      `Recorded Bitcoin tip: ${formatNumber(model.bitcoin_tip.height)} (${model.bitcoin_tip.hash}).`,
      `Recorded source observation: ${formatDate(wrapper.finality.input_binding?.retained_tip_observation?.observed_at || model.source_context.inputs.observed_at)}.`,
      `BFG reference effective confirmations: ${formatNumber(view.values.effective_confirmations)}.`,
      `BFG reference threshold: ${view.values.super_finality ? 'Reached' : 'Not reached'} (T - H >= 10).`,
      `Inclusive Bitcoin depth: ${formatNumber(view.values.raw_bitcoin_confirmations)}.`,
      `Recorded source consistency: ${wrapper.finality.source_conflict ? 'conflicting retained source views' : model.source_context.status}.`);
    if (view.ama) lines.push(`Signed-counter comparison: ${formatNumber(view.ama.signed_counter)} (T - H - 9).`);
    if (view.kind === 'block') lines.push('Inspecting another publication does not change this Hemi-block reference.');
    const snapshot = model.first_height_evidence.context;
    if (snapshot?.snapshot) lines.push(`Separate Hemi snapshot: ${formatNumber(snapshot.snapshot.number)}, observed ${formatDate(snapshot.observed_from)}–${formatDate(snapshot.observed_through)}. These observations are not a certified simultaneous cross-chain snapshot.`);
    lines.push(`Calculation receipt checksum: ${model.receipt_sha256}.`);
  }
  lines.push('Recorded model result; no fresh source check. Full BFG handler behavior, deployed Hemi equivalence and current-chain finality are not established.',
    'Calculation JSON is a separate assessment; original receipt and any verification package are required to recompute its evidence.');
  return lines;
}
