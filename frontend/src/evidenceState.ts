import type { Receipt } from './types';
import type { StatusTone } from './StatusIcon';
import { isRecorded } from './recordedEvidence';

// Display qualifications only. Stored evidence and publication selection are unchanged.
export function sourceState(receipt: Receipt): 'confirmed' | 'conflict' | 'incomplete' {
  const context = receipt.context;
  const observations = [...(context?.hemi_headers || []), ...(context?.bitcoin_blocks || []), ...(context?.bitcoin_secondary || [])];
  if (observations.some(item => item.same === false)
    || context?.requested_block_identity?.status === 'different_current_rpc_block'
    || receipt.issues?.some(issue => issue.code === 'source_conflict')) return 'conflict';
  return context?.current_view_confirmed === true ? 'confirmed' : 'incomplete';
}

export function sourceAgreementText(receipt: Receipt): string {
  const state = sourceState(receipt);
  if (isRecorded(receipt)) return state === 'conflict'
    ? 'Recorded source views conflicted; current views not rechecked' : 'Current source views not rechecked';
  return state === 'confirmed' ? 'Source views matched at the recorded check'
    : state === 'conflict' ? 'Source views conflicted at the recorded check'
      : 'Incomplete at the recorded check';
}

export function operationTimedOut(receipt: Receipt): boolean {
  return receipt.resources?.deadline_expired === true
    || receipt.termination?.reason === 'time_budget_exhausted'
    || receipt.issues?.some(issue => issue.code === 'time_budget_exhausted') === true
    // Refresh preserves the original search's stop reason; it is not a new timeout.
    || (receipt.mode !== 'live_metadata_refresh' && receipt.coverage?.stop_reason === 'time_budget_exhausted');
}

export function resultState(receipt: Receipt, hasProof: boolean): { title: string; summary: string; tone: StatusTone } {
  const refresh = receipt.mode === 'live_metadata_refresh';
  const timedOut = operationTimedOut(receipt);
  const source = sourceState(receipt);
  if (timedOut) return {
    title: `${hasProof ? 'Evidence retained · ' : ''}${refresh ? 'source recheck' : 'search'} timed out`.replace(/^\w/, letter => letter.toUpperCase()),
    summary: refresh ? 'The source recheck did not finish. Retained proof checks and original search coverage remain available.'
      : hasProof ? 'Matching evidence was retained before the search timed out. Review the checked coverage and source status.'
        : 'The search timed out. Checked coverage is partial; this result does not establish that a publication is absent.',
    tone: 'warning',
  };
  if (source === 'conflict') return {
    title: hasProof ? 'Evidence retained · source conflict' : 'Source conflict · result unconfirmed',
    summary: 'Recorded block data disagreed with the source views. Supplied-data proof checks are separate; this does not establish a chain reorganization.',
    tone: 'warning',
  };
  if (isRecorded(receipt) && hasProof) {
    const incomplete = Boolean(receipt.coverage?.gaps?.length || receipt.coverage?.unresolved_candidates?.length || receipt.coverage?.unsupported_payloads);
    return {
      title: receipt.coverage?.gaps?.length ? 'Recorded evidence · scan gaps' : 'Recorded publication evidence',
      summary: 'Matching evidence from retained data. Current source views have not been rechecked; earlier publications may exist outside the compiled coverage.',
      tone: incomplete ? 'warning' : 'neutral',
    };
  }
  if (receipt.status === 'incomplete' || source !== 'confirmed' || (receipt.status === 'found' && !hasProof)) return {
    title: refresh ? hasProof ? 'Evidence retained · source recheck incomplete' : 'Source recheck incomplete'
      : hasProof ? 'Evidence found · search incomplete' : 'Search incomplete',
    summary: refresh ? 'Some source checks did not finish. Retained evidence and original search coverage are preserved.'
      : hasProof ? 'Matching evidence was retained, but some checks could not finish. Review the search coverage and warnings.'
        : 'The search could not finish. This result does not establish that a publication is absent.',
    tone: 'warning',
  };
  return receipt.status === 'found' && hasProof ? {
    title: 'Publication found',
    summary: refresh ? 'Retained publication evidence with rechecked source views. Original search coverage is preserved; earlier publications may exist outside it.'
      : 'Earliest matching publication observed in this search. Earlier publications may exist outside the scanned data.',
    tone: 'pass',
  } : {
    title: 'No match in checked range',
    summary: 'No applicable publication was found in the scanned Bitcoin range. Publications may exist outside this range.',
    tone: 'neutral',
  };
}
