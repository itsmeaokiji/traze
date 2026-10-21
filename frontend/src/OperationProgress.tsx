import { useEffect, useState } from 'react';
import { formatNumber, shortHash } from './format';
import type { Job } from './types';

export function OperationProgress({ job }: { job: Job }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const latest = job.events.at(-1)?.message || '';
  const scanned = /^Bitcoin (\d+): \d+ txs, \d+ HEMI candidates, (\d+) matches/.exec(latest);
  const range = /^Search Bitcoin (\d+)-(\d+);/.exec(latest);
  const gap = /^Gap at Bitcoin (\d+):/.exec(latest);
  const extension = /^No match in previous window; extending through Bitcoin (\d+)/.exec(latest);
  const refresh = job.operation === 'refresh';
  const recorded = job.phase === 'recorded_lookup';
  const stronger = job.phase === 'first_publication_check';
  const message = stronger ? latest || 'Preparing and checking first-publication evidence…' : refresh ? 'Checking recorded Hemi and Bitcoin blocks against the source views…'
    : scanned ? `Bitcoin block ${formatNumber(Number(scanned[1]))} scanned · ${formatNumber(Number(scanned[2]))} matching proof record${scanned[2] === '1' ? '' : 's'} retained.`
      : gap ? `Bitcoin block ${formatNumber(Number(gap[1]))} could not be checked. Search coverage will record this gap.`
        : range ? `Scanning Bitcoin blocks ${formatNumber(Number(range[1]))}–${formatNumber(Number(range[2]))}…`
          : extension ? `Extending the search through Bitcoin block ${formatNumber(Number(extension[1]))}…`
            : recorded ? 'Checking recorded publication evidence…'
              : job.phase === 'public_discovery' ? 'Reading public sources…' : 'Preparing publication search…';
  const started = Date.parse(job.started_at);
  const elapsed = Number.isFinite(started) ? `${Math.max(0, Math.round((now - started) / 1000))}s elapsed` : 'Elapsed time unavailable';
  return <section id="job-progress" className="job-progress" aria-labelledby="progress-heading" aria-busy="true">
    <p className="eyebrow">{stronger ? 'FIRST-PUBLICATION CHECK IN PROGRESS' : refresh ? 'SOURCE RECHECK IN PROGRESS' : recorded ? 'RECORDED EVIDENCE SEARCH' : 'PUBLICATION SEARCH IN PROGRESS'}</p>
    <div className="progress-header"><span className="spinner" aria-hidden="true" /><h2 id="progress-heading">{refresh ? 'Rechecking sources…' : `Searching Hemi ${typeof job.block_input === 'string' ? shortHash(job.block_input) : formatNumber(job.block_number)}…`}</h2><span id="progress-time">{elapsed}</span></div>
    <p id="progress-latest" role="status" aria-live="polite">{message}</p>
    <p className="field-help">{stronger ? 'Preparing the additional evidence within this search\u2019s existing limits. A publication result remains useful if the stronger check cannot finish.' : refresh ? 'Retained proof data is preserved. This operation does not scan additional Bitcoin blocks or expand the original search coverage.' : recorded ? 'Searching server-side records. Historical source observations and collected coverage remain unchanged.' : 'Only fully scanned Bitcoin blocks count toward search coverage. Proof and source checks are reported separately in the result.'}</p>
  </section>;
}
