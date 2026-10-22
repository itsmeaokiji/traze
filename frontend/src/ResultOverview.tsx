import type { Proof, ReceiptWrapper } from './types';
import { formatNumber, shortHash } from './format';
import { firstPublicationState } from './firstPublicationState';

export function ResultOverview({ wrapper, proof, defaultSelection }: { wrapper: ReceiptWrapper; proof?: Proof; defaultSelection: boolean }) {
  const receipt = wrapper.receipt;
  const hash = receipt.query.requested_block_hash || receipt.query.block_hash;
  const firstHeight = firstPublicationState(wrapper, proof);
  const earliestHeight = Math.min(...receipt.proofs.map(item => item.bitcoin_height));
  const earliestObserved = defaultSelection && !wrapper.selected_publication && proof?.bitcoin_height === earliestHeight;
  const assessment = firstHeight.qualified
    ? firstHeight.snapshot ? 'Conditional — recorded Hemi snapshot' : 'Conditional — supported rules'
    : firstHeight.conflict ? 'Suspended — source conflict'
      : firstHeight.assessment?.status === 'invalid' ? 'Stronger evidence invalid'
        : firstHeight.another ? 'Not available for this selection'
          : firstHeight.validInterval ? 'First in checked interval'
            : earliestObserved ? 'Earliest observed in this result' : 'Not established for this selection';
  return <div className="result-overview">
    <p id="result-route-summary" className="result-route-summary">
      <span>Hemi block <strong title={hash}>{receipt.query.block_number !== null ? formatNumber(receipt.query.block_number) : hash ? shortHash(hash) : 'not recorded'}</strong></span>
      {proof ? <><span aria-hidden="true" className="summary-arrow">→</span><span>Bitcoin block <strong>{formatNumber(proof.bitcoin_height)}</strong></span></> : <span className="muted">· No matching publication selected</span>}
    </p>
    {proof && <p id="result-assurance" className="result-assurance">First-height assessment: <a href="#first-publication">{assessment}</a></p>}
  </div>;
}
