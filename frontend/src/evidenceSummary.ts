import { searchCoverageText, searchStopText } from './coveragePresentation';
import { bitcoinReference, recordedBitcoinSources, recordedHemiSource } from './evidenceLinks';
import { resultState, sourceState } from './evidenceState';
import { formatDate, formatNumber, selectedProof } from './format';
import { receiptIssues } from './receiptIssues';
import type { ReceiptWrapper } from './types';
import { historicalSourceTimes, isRecorded } from './recordedEvidence';
import { firstPublicationState } from './firstPublicationState';
import { finalitySummary } from './finalityPresentation';

export function evidenceSummary(wrapper: ReceiptWrapper, displayedIndex: number | undefined, fresh: boolean): string {
  const r = wrapper.receipt;
  const recorded = isRecorded(r);
  const defaultProof = selectedProof(r);
  const defaultIndex = defaultProof ? r.proofs.indexOf(defaultProof) : undefined;
  const index = displayedIndex !== undefined && Number.isInteger(displayedIndex) && displayedIndex >= 0 && displayedIndex < r.proofs.length ? displayedIndex : defaultIndex;
  const proof = index !== undefined ? r.proofs[index] : undefined;
  const firstHeight = firstPublicationState(wrapper, proof);
  const manual = Boolean(proof && index !== defaultIndex);
  const state = resultState(r, Boolean(proof));
  const source = sourceState(r);
  const cover = r.query.covering_keystone ?? r.query.covering_checkpoint?.height;
  const requestedHash = r.query.requested_block_hash || r.query.block_hash;
  const identity = r.context?.requested_block_identity;
  const identities = { matches_current_rpc_view: 'Matched the Hemi RPC view at the recorded check', different_current_rpc_block: 'Different RPC block hash at the recorded check', unavailable: 'Unconfirmed at the recorded check', not_rechecked: 'Current identity not rechecked for the generated target' };
  const origin = recorded ? 'Recorded evidence' : fresh ? r.mode === 'live_metadata_refresh' ? 'Source recheck' : 'Search result' : 'Saved result';
  const lines = [
    'Traze publication evidence', '',
    `Result: ${state.title}`,
    `Origin: ${origin}${r.validation?.scope === 'controlled_provider_responses' ? ' · Controlled test data; not a live chain observation' : ''}`,
    `Operation recorded: ${recorded ? 'Retained-data retrieval; no new source check' : r.mode === 'live_metadata_refresh' ? 'Source recheck; original search coverage preserved' : 'Publication search'}`,
    '',
    `Hemi input: ${r.query.requested_block_hash || (r.query.block_number !== null ? `block ${formatNumber(r.query.block_number)}` : 'not recorded')}`,
    `Requested Hemi block: ${r.query.block_number !== null ? formatNumber(r.query.block_number) : 'Unresolved height'}`,
    `Requested Hemi block hash: ${requestedHash || 'Not recorded'}`,
    `Block identity: ${identity ? identities[identity.status] : 'Not separately recorded'}`,
  ];
  if (r.query.block_hash && r.query.block_hash !== requestedHash) lines.push(`Recorded Hemi block hash: ${r.query.block_hash}`);
  if (identity?.current_hash && identity.current_hash !== requestedHash) lines.push(`RPC block hash at the recorded check: ${identity.current_hash}`);
  lines.push(`Covering keystone: ${cover !== undefined ? formatNumber(cover) : 'Unresolved'}`, `Covering keystone ID: ${r.query.covering_checkpoint?.keystone_id || 'Not recorded'}`, '');
  if (proof) {
    lines.push(
      `View: ${manual ? 'Manually selected' : 'Receipt default'} proof record ${index! + 1}`,
      `Original receipt default: ${defaultIndex !== undefined ? `proof record ${defaultIndex + 1}` : 'Not resolved'}`,
      `Published keystone: ${formatNumber(proof.checkpoint.height)}`,
      `Published keystone ID: ${proof.checkpoint.keystone_id}`,
      `Association: ${cover === undefined ? 'Covering-keystone relationship is unresolved' : proof.checkpoint.height === cover ? 'Covering keystone encoded in the displayed publication transaction' : 'Requested block supported through the displayed later-keystone publication and supplied Hemi ancestry'}`,
      `Publication transaction ID: ${proof.txid}`,
      `Containing Bitcoin block: ${formatNumber(proof.bitcoin_height)}`,
      `Bitcoin block hash: ${proof.bitcoin_block_hash}`,
    );
    if (manual) lines.push('Manual selection changes the view only. The JSON export and source recheck use the original receipt.');
  } else lines.push('Publication association: Unresolved. No applicable publication transaction is displayed.', state.summary);
  lines.push('',
    `Proof checks: ${wrapper.verification.proofs_checked > 0 ? `${formatNumber(wrapper.verification.proofs_checked)} proof record${wrapper.verification.proofs_checked === 1 ? '' : 's'} passed supplied-data checks` : 'No publication proof to check'}`,
    `Source confirmation: ${recorded ? 'Current source views not rechecked' : source === 'confirmed' ? 'Confirmed at the recorded check' : source === 'conflict' ? 'Conflicting data at the recorded check' : 'Incomplete at the recorded check'}`,
    `Result recorded: ${formatDate(r.completed_at)}`,
    `Sources last checked: ${formatDate(r.context?.checked_at)}`, '',
    ...(recorded ? [`Historical source observations: ${historicalSourceTimes(r)}`, ...(r.catalogue?.number_resolved_from_retained_view ? ['Block number resolved from retained metadata; current canonical identity is unconfirmed.'] : [])] : []),
    searchCoverageText(r),
    `Fully scanned Bitcoin blocks: ${formatNumber(r.coverage?.blocks?.length || 0)}`,
    searchStopText(r),
  );
  const { warnings } = receiptIssues(r, false);
  if (warnings.length) lines.push('', 'Warnings:', ...warnings.map(warning => `- ${warning}`));
  const references: string[] = [];
  const hemi = recordedHemiSource(r);
  if (hemi) references.push(`Hemi RPC source: ${hemi}`);
  if (proof) {
    const transaction = bitcoinReference('tx', proof.txid);
    const block = bitcoinReference('block', proof.bitcoin_block_hash);
    if (transaction) references.push(`Bitcoin transaction: ${transaction}`);
    if (block) references.push(`Bitcoin block: ${block}`);
  }
  references.push(...recordedBitcoinSources(r).map(url => `Bitcoin API source: ${url}`));
  lines.push('', 'Source references:', ...(references.length ? references : ['No supported source URL was recorded.']), '',
    ...(firstHeight.qualified ? [
      `First-publication assessment: ${firstHeight.title}, at Bitcoin ${formatNumber(proof?.bitcoin_height)}.`,
      'This is conditional on operator-accepted chain, execution, derivation, history and computational assumptions in the recorded view.',
      ...(firstHeight.snapshot && firstHeight.context?.snapshot && firstHeight.context.domain ? [
        `Hemi snapshot: ${formatNumber(firstHeight.context.snapshot.number)} (${firstHeight.context.snapshot.hash}).`,
        `Eligible same-branch keystones: ${formatNumber(firstHeight.context.domain.first_hemi_keystone)}–${formatNumber(firstHeight.context.domain.last_hemi_block)}; future snapshots excluded.`,
        `Accepted context checksum: ${firstHeight.context.sha256}.`,
        'Historical profile and validating-source branch premises are external; historical execution is not independently certified. Bitcoin observations were not refreshed.',
      ] : []),
      'Unconditional global proof: Not established.',
    ] : ['Globally earliest publication: Not established. Publications may exist outside the checked coverage.',
      `First-publication assessment: ${firstHeight.title}.`]),
    ...finalitySummary(wrapper, proof),
    'Proof checks use supplied Bitcoin headers and Hemi RPC data. Full Bitcoin header-chain validation, independent Hemi execution, deployed Hemi finality equivalence, and PoP reward eligibility are outside these proof checks.',
    'This summary reflects recorded evidence. Copying or opening it does not recheck sources. Provider backend independence is not established.',
    `JSON evidence: ${wrapper.filename} (original receipt; download separately for offline verification).`,
  );
  return lines.join('\n');
}
