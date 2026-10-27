import { useState } from 'react';
import { ChainMark } from './ChainMark';
import { ExternalLink } from './ExternalLink';
import { bitcoinReference } from './evidenceLinks';
import { formatNumber, shortHash } from './format';
import { withBrowseReturn } from './browseNavigation';
import { timelineGroups, timelineKeystoneKey } from './timelineRecords';
import type { PublicationSummary } from './types';

export function PublicationTimeline({ records, returnTo }: { records: PublicationSummary[]; returnTo: string }) {
  const [selected, setSelected] = useState<string>();
  const groups = timelineGroups(records);
  const matching = selected ? records.filter(record => timelineKeystoneKey(record) === selected) : [];
  const selectedRecord = matching[0];
  const counts = new Map<string, number>();
  for (const record of records) {
    const key = timelineKeystoneKey(record);
    if (key) counts.set(key, (counts.get(key) || 0) + 1);
  }
  return <section className="publication-timeline" aria-labelledby="timeline-heading">
    <div className="timeline-heading">
      <div><h2 id="timeline-heading">Publication timeline</h2><p className="field-help">Recorded Bitcoin heights, newest first. Connections describe retained records.</p></div>
      <p>{formatNumber(records.length)} publication{records.length === 1 ? '' : 's'} <span>·</span> {formatNumber(groups.length)} Bitcoin block{groups.length === 1 ? '' : 's'} <small>on this page</small></p>
    </div>
    <p className="field-help timeline-scope">Only this page is grouped. Other pages may contain more publications for the same keystone or Bitcoin block.</p>
    <div className="timeline-selection" aria-live="polite">
      {selectedRecord ? <><div><strong>Keystone {formatNumber(selectedRecord.keystone_height)}</strong><span className="hash-value">{selectedRecord.keystone_id}</span><span>{matching.length} publication{matching.length === 1 ? '' : 's'} highlighted on this page</span></div><button type="button" className="text-button" onClick={() => setSelected(undefined)}>Clear highlight</button></> : <p>Select a keystone to highlight its publications on this page.</p>}
    </div>
    {records.length === 0 ? <p className="browse-empty">No indexed records match these filters. This is not a claim that no publication exists.</p> : <>
      <div className="timeline-lanes" aria-hidden="true"><span>Encoded Hemi keystone</span><span>Publication transaction</span><span>Containing Bitcoin block</span></div>
      <ol className={`timeline-groups${selected ? ' has-selection' : ''}`} aria-label="Indexed publication timeline">
        {groups.map((group, groupIndex) => <li className={`timeline-group${group.height === null ? ' is-unpositioned' : ''}`} key={group.hash} data-bitcoin-hash={group.hash}>
          {group.height !== null && groupIndex > 0 && groups[groupIndex - 1].height !== null && groups[groupIndex - 1].height! - group.height > 1 && <p className="timeline-interval">Intervening Bitcoin heights are not shown</p>}
          <h3 className="sr-only">{group.heightConflict ? 'Bitcoin block with conflicting heights' : group.height === null ? 'Bitcoin block without a recorded height' : `Bitcoin block ${formatNumber(group.height)}`}, hash {group.hash}</h3>
          <div className="timeline-group-body">
            <ul className="timeline-records">
              {group.records.map(record => {
                const key = timelineKeystoneKey(record);
                const associated = Boolean(key) && record.state === 'associated' && record.association_available;
                const active = key !== undefined && selected === key;
                return <li key={record.id} className={`timeline-row${associated ? ' is-associated' : ' is-pending'}${active ? ' is-selected' : ''}`} data-publication-id={record.id} data-keystone-id={record.keystone_id || undefined}>
                  {key ? <button type="button" className="timeline-node timeline-keystone" aria-pressed={active} aria-label={`Highlight keystone ${formatNumber(record.keystone_height)}, ID ${record.keystone_id}`} onClick={() => setSelected(active ? undefined : key)}>
                    <span className="timeline-mark" aria-hidden="true"><ChainMark chain="hemi" /></span>
                    <span className="timeline-node-copy"><span className="timeline-node-label">Encoded keystone</span><strong>{formatNumber(record.keystone_height)}</strong><span className="hash-value" title={record.keystone_id!}>{shortHash(record.keystone_id!)}</span>{counts.get(key)! > 1 && <small>{counts.get(key)} publications on this page</small>}</span>
                  </button> : <div className="timeline-node timeline-unknown"><span className="timeline-mark" aria-hidden="true">?</span><span className="timeline-node-copy"><span className="timeline-node-label">Keystone unavailable</span><strong>{record.state === 'unsupported' ? 'Unsupported payload' : 'Not recorded'}</strong><small>No Hemi association asserted</small></span></div>}
                  <span className="timeline-edge" aria-hidden="true"><svg viewBox="0 0 48 16"><path d="M0 8H48" /><circle cx="24" cy="8" r="3" /></svg></span>
                  <a className="timeline-node timeline-transaction" href={withBrowseReturn(record.record_url, returnTo)} aria-label={`Inspect publication ${record.txid}, output ${record.vout_index}`}>
                    <span className="timeline-publication-dot" aria-hidden="true" />
                    <span className="timeline-node-copy"><span className="timeline-node-label">Transaction · output {record.vout_index}</span><strong className="hash-value" title={record.txid}>{shortHash(record.txid)}</strong><span className={`record-state state-${associated ? 'associated' : record.state === 'unsupported' ? 'unsupported' : 'pending'}`}>{associated ? 'Hemi evidence available' : record.state === 'unsupported' ? 'Unsupported payload' : 'Hemi evidence unavailable'}</span>{record.evidence_scope === 'publication_proof' && <small>Publication proof only</small>}<span className="timeline-inspect">Inspect <span aria-hidden="true">↗</span></span></span>
                  </a>
                </li>;
              })}
            </ul>
            <div className="timeline-bitcoin">
              <div className={`timeline-block-node${group.heightConflict ? ' has-conflict' : ''}`}>
                <span className="timeline-mark" aria-hidden="true"><ChainMark chain="bitcoin" /></span>
                <div className="timeline-node-copy"><span className="timeline-node-label">Bitcoin block</span><strong>{group.heightConflict ? 'Conflicting heights' : group.height === null ? 'Height not recorded' : formatNumber(group.height)}</strong><ExternalLink href={bitcoinReference('block', group.hash)!} title={`Open Bitcoin block ${group.hash} in a new tab`}><span className="hash-value">{shortHash(group.hash)}</span></ExternalLink><small>{group.records.length} publication{group.records.length === 1 ? '' : 's'} on this page</small>{group.height === null && <small className="timeline-height-warning">Not placed at a known height</small>}</div>
              </div>
            </div>
          </div>
        </li>)}
      </ol>
    </>}
    <details className="limits-details timeline-qualification"><summary>How to read these connections</summary><p>Keystone identifiers come from supported publication payloads. A solid Hemi connection means usable indexed Hemi evidence is linked; a dashed connection means Hemi evidence is unavailable or the payload is unsupported. Open the publication, then its Hemi evidence, to recompute proof checks.</p><p>Different hashes or keystone IDs remain separate even at the same height. Within each block, records sort by encoded keystone height and identity, not transaction chronology. Spacing is not elapsed time. Unshown heights may be outside the page, filtered out or absent from the index; this view does not decide which. Conflicting or unknown heights are listed after known heights without an assigned position.</p><p>These are indexed summaries, not fresh source checks, current chain confirmation, finality or a first-publication guarantee. Index storage time is not a source observation time.</p></details>
  </section>;
}
