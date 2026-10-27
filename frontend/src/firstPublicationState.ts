import type { Proof, ReceiptWrapper } from './types';
import { sourceState } from './evidenceState';

export const FIRST_HEIGHT_POLICY = 'hemi-full-state-first-height-assumptions-v1';
export const SNAPSHOT_POLICY = 'hemi-snapshot-first-height-assumptions-v1';
export const SNAPSHOT_RESULT = 'traze-snapshot-verification-result-v3';

export function firstPublicationState(wrapper: ReceiptWrapper, proof?: Proof) {
  const assessment = wrapper.first_publication;
  const publication = assessment?.publication;
  const query = wrapper.receipt.query;
  const exact = Boolean(assessment && proof && publication
    && assessment.query.number === query.block_number && assessment.query.hash === query.block_hash
    && publication.bitcoin_height === proof.bitcoin_height && publication.bitcoin_hash === proof.bitcoin_block_hash
    && publication.txid === proof.txid && publication.keystone_number === proof.checkpoint.height
    && publication.tx_index === proof.tx_index && publication.vout === proof.vout_index);
  const result = assessment?.result;
  const interval = result?.interval_evidence;
  const intervalExact = Boolean(exact && interval && interval.status === 'verified_interval_evidence'
    && interval.query.number === query.block_number && interval.query.hash === query.block_hash
    && interval.publication.txid === publication?.txid && interval.publication.vout === publication?.vout
    && interval.publication.tx_index === publication?.tx_index && interval.publication.bitcoin_hash === publication?.bitcoin_hash
    && interval.publication.bitcoin_height === publication?.bitcoin_height
    && interval.publication.keystone_number === publication?.keystone_number);
  const conflict = sourceState(wrapper.receipt) === 'conflict' || assessment?.status === 'source_conflict';
  const context = result?.context_evidence;
  const snapshot = result?.schema === SNAPSHOT_RESULT || assessment?.policy_id === SNAPSHOT_POLICY || Boolean(context);
  const policy = snapshot ? SNAPSHOT_POLICY : FIRST_HEIGHT_POLICY;
  const contextBound = Boolean(result?.schema === SNAPSHOT_RESULT && context?.status === 'verified_context_binding'
    && context.sha256 && /^[0-9a-f]{64}$/.test(context.sha256)
    && result.policy?.context_accepted_by_caller === true
    && result.policy.context_sha256_selected_by_caller === context.sha256
    && result.first_height_assessment?.context_sha256 === context.sha256
    && context.snapshot && context.domain && context.domain.first_hemi_keystone === query.covering_checkpoint?.height
    && context.domain.last_hemi_block === context.snapshot.number && context.snapshot.number >= (publication?.keystone_number || 0)
    && context.domain.tip_hash === context.snapshot.hash && /^0x[0-9a-f]{64}$/.test(context.snapshot.hash)
    && context.historical_execution_certified === false && context.current_bitcoin_membership_verified === false);
  const qualified = Boolean(intervalExact && !conflict && assessment?.status === 'verified_conditional_first_height'
    && assessment.accepted_by_operator && assessment.policy_id === policy && (!snapshot || contextBound)
    && result?.status === 'verified_conditional_first_height'
    && result.first_height_assessment?.status === 'conditional'
    && result.first_height_assessment.bitcoin_height === proof?.bitcoin_height
    && result.first_height_assessment.policy_id === policy
    && result.policy?.id === policy && result.policy.accepted_by_caller && result.policy.accepted_from_packet === false
    && result.assurance?.first_height_under_accepted_assumptions === true && result.assurance.globally_first_proven === false);
  const validInterval = intervalExact && interval?.assurance.first_in_checked_interval === true;
  const another = Boolean(assessment?.publication && proof && !exact);
  const title = qualified ? snapshot ? 'First applicable publication under snapshot assumptions' : 'First applicable publication under supported rules'
    : another ? 'Assessment unavailable for this selection'
      : conflict ? 'First-publication assessment suspended'
        : assessment?.status === 'invalid' ? 'First-publication evidence invalid'
          : validInterval ? 'First in checked interval'
            : assessment?.status === 'not_available' || !assessment ? 'First-publication assessment not available'
              : 'First-publication assessment incomplete';
  const description = qualified ? snapshot ? 'Complete interval checks and a computational starting bound support this Bitcoin height within the recorded Hemi snapshot, conditional on operator-accepted source, historical-profile and security assumptions.' : 'This Bitcoin height is supported by complete interval checks and a computational starting bound, under the operator-accepted assumptions. The assessment describes the recorded chain view.'
    : another ? 'The retained assessment belongs to another publication. Selecting this record does not transfer its first-height conclusion.'
      : conflict ? 'Changed or conflicting source observations prevent using the first-publication assessment. Retained publication evidence remains inspectable.'
        : assessment?.status === 'invalid' ? 'The first-publication evidence contains an inconsistency. No conditional first-publication conclusion is accepted from it.'
          : validInterval ? 'The publication is first within the completely checked interval. A conditional first-publication conclusion is not established; review the missing requirements below.'
            : 'Ordinary publication evidence and search coverage remain separate. Missing first-publication assessment evidence does not mean no publication exists.';
  const download = exact && !conflict && assessment?.packet_available && assessment.download_url
    && /^\/api\/(?:receipts\/[a-z0-9-]+|v1\/blocks\/0x[0-9a-f]{64})\/first-publication\/[0-9a-f]{64}\/download$/.test(assessment.download_url)
    ? assessment.download_url : undefined;
  return { assessment, exact, qualified, validInterval, another, conflict, title, description, download,
    snapshot, context: exact && snapshot ? context : undefined, contextBound };
}
