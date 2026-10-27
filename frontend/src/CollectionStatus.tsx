import type { CollectionStatus as Collection } from './types';
import { formatNumber } from './format';
import { collectionState } from './collectionState';
import { CollectionProgress } from './CollectionProgress';

export function CollectionStatus({ value }: { value?: Collection | null }) {
  if (!value) return null;
  const uncertain = Boolean(value.unresolved_publications || value.pending_ancestry || value.source_conflicts);
  return <section className="collection-status" aria-labelledby="collection-heading">
    <div className="collection-heading"><h2 id="collection-heading">Indexed coverage</h2><span>{collectionState(value.state)}</span></div>
    <p className="muted">Collected data available to searches. Coverage for an individual result is shown below with its evidence.</p>
    {value.processed_blocks !== undefined && <p>{formatNumber(value.processed_blocks)} Bitcoin blocks processed · {formatNumber(value.supported_publications)} publications with supported payloads · {formatNumber(value.associated_publications)} keystone associations</p>}
    {uncertain && <p className="collection-warning">{formatNumber(value.unresolved_publications)} unresolved publications · {formatNumber(value.pending_ancestry)} pending ancestry checks · {formatNumber(value.source_conflicts)} source-view issues</p>}
    {value.state === 'source_conflict' && <p className="collection-warning">Conflicting source views require review. Retained evidence remains historical.</p>}
    {['blocked', 'incomplete', 'limited', 'storage_limited'].includes(value.state) && <p className="collection-warning">Collection is not advancing. Available evidence is limited to collected coverage.</p>}
    {value.state === 'unavailable' && <p className="collection-warning">An active collector could not be confirmed. Retained evidence remains available.</p>}
    {value.updated_at && <p className="muted">Last collection update: <time dateTime={value.updated_at}>{new Date(value.updated_at).toLocaleString('en-US')}</time>. This does not recheck sources for your result. Coverage remains scoped to the recorded observations.</p>}
    <CollectionProgress value={value} />
    <details className="limits-details collection-diagnostics"><summary>Collection diagnostics</summary>
      {value.range && <p className="muted">Collection job range: Bitcoin {formatNumber(value.range[0])}–{formatNumber(value.range[1])} · {formatNumber(value.processed_job_blocks)} of {formatNumber(value.requested_job_blocks)} blocks processed</p>}
      {value.next_height !== undefined && <p className="muted">Next collection height: Bitcoin {formatNumber(value.next_height)}. This cursor advances only through contiguous, checked updates.</p>}
      {value.issue && <p>{value.issue.message || 'Some collection work remains incomplete.'}</p>}
      {value.runner?.last_seen_at && <p className="muted">Last runner observation: <time dateTime={value.runner.last_seen_at}>{new Date(value.runner.last_seen_at).toLocaleString('en-US')}</time>. Runner activity does not confirm current chain views.</p>}
    </details>
  </section>;
}
