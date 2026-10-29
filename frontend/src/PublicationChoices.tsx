import { formatNumber, shortHash } from './format';
import type { RefObject } from 'react';
import type { Receipt } from './types';

export function PublicationChoices({ receipt, defaultIndex, displayedIndex, onSelect, summaryRef, defaultLabel = 'Default selection', selectionHelp }: { receipt: Receipt; defaultIndex?: number; displayedIndex?: number; onSelect: (index: number) => void; summaryRef?: RefObject<HTMLElement | null>; defaultLabel?: string; selectionHelp?: string }) {
  if (receipt.proofs.length < 2) return null;
  return <details id="other-proofs" className="other-proofs publication-choices">
    <summary id="other-proofs-summary" ref={summaryRef}>Matching publication evidence · {receipt.proofs.length} proof records</summary>
    {selectionHelp && <p className="field-help">{selectionHelp}</p>}
    <p className="section-help">Choose a retained proof record to update the route and inspector. Proof-record counts do not imply distinct transactions.</p>
    <ol id="proofs-body" className="publication-options" aria-label="Matching publication proof records">
      {receipt.proofs.map((proof, index) => <li key={index}>
        <button type="button" className={`publication-option${index === displayedIndex ? ' is-viewed' : ''}`} aria-label={`View proof record ${index + 1}`} aria-pressed={index === displayedIndex} onClick={() => onSelect(index)}>
          <span className="publication-option-top"><strong>Bitcoin block {formatNumber(proof.bitcoin_height)}</strong><span className="publication-option-tags">{index === defaultIndex && <span>{defaultLabel}</span>}{index === displayedIndex && <span>Viewing</span>}</span></span>
          <span className="publication-option-detail">Published keystone {formatNumber(proof.checkpoint.height)} · {proof.checkpoint.height === receipt.query.covering_keystone ? 'Covering keystone' : 'Later keystone support'}</span>
          <span className="publication-option-reference"><span>Proof record {index + 1}</span><span title={proof.txid}>Transaction {shortHash(proof.txid)}</span></span>
        </button>
      </li>)}
    </ol>
  </details>;
}
