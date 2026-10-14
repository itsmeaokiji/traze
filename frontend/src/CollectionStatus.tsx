import type { CollectionStatus as Collection } from './types';
import { formatNumber } from './format';

const states: Record<string, string> = {
  queued: 'Collection queued', running: 'Collection in progress', waiting: 'Waiting for Bitcoin blocks',
  complete: 'Collection completed', incomplete: 'Collection incomplete', limited: 'Collection paused at its limit',
  source_conflict: 'Source views changed', unavailable: 'Collection status unavailable',
};

export function CollectionStatus({ value }: { value?: Collection | null }) {
  if (!value) return null;
  const uncertain = Boolean(value.unresolved_publications || value.pending_ancestry || value.source_conflicts);
  return <section className="collection-status" aria-labelledby="collection-heading">
    <div className="collection-heading"><h2 id="collection-heading">Indexed evidence</h2><span>{states[value.state] || 'Recorded collection state'}</span></div>
    {value.processed_blocks !== undefined && <p>{formatNumber(value.processed_blocks)} Bitcoin blocks processed · {formatNumber(value.supported_publications)} supported publications · {formatNumber(value.associated_publications)} keystone associations</p>}
    {value.range && <p className="muted">Collection range: Bitcoin {formatNumber(value.range[0])}–{formatNumber(value.range[1])} · {formatNumber(value.processed_job_blocks)} of {formatNumber(value.requested_job_blocks)} blocks processed</p>}
    {uncertain && <p className="collection-warning">{formatNumber(value.unresolved_publications)} unresolved publications · {formatNumber(value.pending_ancestry)} pending ancestry checks · {formatNumber(value.source_conflicts)} source-view issues</p>}
    {value.issue && <p className="collection-warning">{value.issue.message || 'Some collection work remains incomplete.'}</p>}
    {value.updated_at && <p className="muted">Last progress recorded: <time dateTime={value.updated_at}>{new Date(value.updated_at).toLocaleString('en-US')}</time>. Coverage and source times remain scoped to the recorded observations.</p>}
  </section>;
}
