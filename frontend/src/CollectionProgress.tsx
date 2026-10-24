import type { CollectionStatus } from './types';
import { formatDate, formatNumber } from './format';
import { collectionState } from './collectionState';

function elapsed(seconds: number) {
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function CollectionProgress({ value, showState = false }: { value: CollectionStatus; showState?: boolean }) {
  const progress = value.progress;
  if (!progress) return showState ? <p className="muted">{collectionState(value.state)}</p> : null;
  const tip = progress.observed_bitcoin_tip, job = progress.current_job;
  return <div className="collection-progress">
    {showState && <p className="collection-activity">{collectionState(value.state)}</p>}
    {progress.controlled_replay && <p className="collection-warning">Controlled replay observations. No live source collection.</p>}
    {progress.scope === 'ongoing_service' && <p>
      Checked service coverage: {progress.checked_through_height === null ? 'not yet established' : <>Bitcoin {formatNumber(progress.service_start_height)}–{formatNumber(progress.checked_through_height)}</>}.
      {progress.remaining_to_observed_tip !== null ? <> {formatNumber(progress.remaining_to_observed_tip)} heights remain from the service cursor to the last observed tip.</> : ' Gap to the observed tip is unavailable.'}
    </p>}
    {tip ? <p className="muted">Last observed Bitcoin tip: <strong>{formatNumber(tip.height)}</strong> · <time dateTime={tip.observed_at || undefined}>{formatDate(tip.observed_at || undefined)}</time>{tip.clock_valid && tip.age_seconds !== null ? ` · ${elapsed(tip.age_seconds)}` : ' · Observation time unavailable or inconsistent.'}</p>
      : <p className="muted">No usable Bitcoin tip observation is recorded for this collection.</p>}
    <p className="field-help">Recorded observations describe this collection's scope. They do not confirm current chain membership or complete history.</p>
    {job.range && <details className="limits-details collection-progress-details"><summary>Collection coverage details</summary>
      <p>Current job: Bitcoin {formatNumber(job.range[0])}–{formatNumber(job.range[1])}. {formatNumber(job.processed_bodies)} of {formatNumber(job.required_heights)} bodies processed; {formatNumber(job.checked_update_heights)} heights have completed their recorded checks.</p>
      <p>{formatNumber(job.pending_metadata)} publications need Hemi metadata · {formatNumber(job.pending_ancestry)} publication paths need ancestry work · {formatNumber(job.unsupported_publications)} unsupported payloads.</p>
      {job.unprocessed_body_heights.length > 0 && <p>Unprocessed body heights: {job.unprocessed_body_heights.slice(0, 12).map(formatNumber).join(', ')}{job.unprocessed_body_heights.length > 12 ? ` and ${formatNumber(job.unprocessed_body_heights.length - 12)} more` : ''}.</p>}
      <p className="field-help">Available keystone metadata and a path to your requested Hemi block are separate checks. These totals cover the current job.</p>
    </details>}
  </div>;
}
