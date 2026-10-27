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
  const range = /^(?:Search Bitcoin|Scanning Bitcoin blocks) (\d+)-(\d+);/.exec(latest);
  const estimate = [...job.events].reverse().map(event => /^Estimated Bitcoin search window: (\d+)-(\d+); bounded first segment: (\d+)-(\d+)$/.exec(event.message)).find(Boolean);
  const gap = /^Gap at Bitcoin (\d+):/.exec(latest);
  const extension = /^No match in previous window; extending through Bitcoin (\d+)/.exec(latest);
  const refresh = job.operation === 'refresh';
  const recorded = job.phase === 'recorded_lookup';
  const stronger = job.phase === 'first_publication_check';
  const continuing = job.operation === 'continue';
  const stages: Record<string, string> = {
    'Reading Hemi block and keystone data': 'Reading the Hemi block and its covering keystone…',
    'Estimating the Bitcoin search range': 'Estimating the Bitcoin search range…',
    'Preparing the requested Bitcoin search range': 'Preparing the requested Bitcoin search range…',
    'Using the fallback timestamp source': 'Using the fallback timestamp source…',
    'Estimating the Bitcoin search range using fallback metadata': 'Estimating the Bitcoin search range using fallback metadata…',
    'Checking source views': 'Checking source views…',
    'Estimated window processed; further search requires an explicit continuation': 'The estimated window was processed. Preparing the result…',
    'Scan segment processed; estimated-window heights remain': 'The planned segment was processed. Preparing the result with remaining search coverage…',
  };
  const message = stronger ? latest || 'Preparing and checking first-publication evidence…' : refresh ? 'Checking recorded Hemi and Bitcoin blocks against the source views…'
    : scanned ? `Bitcoin block ${formatNumber(Number(scanned[1]))} scanned · ${formatNumber(Number(scanned[2]))} matching proof record${scanned[2] === '1' ? '' : 's'} retained.`
      : gap ? `Bitcoin block ${formatNumber(Number(gap[1]))} could not be checked. Search coverage will record this gap.`
        : range ? `Scanning Bitcoin blocks ${formatNumber(Number(range[1]))}–${formatNumber(Number(range[2]))}…`
          : extension ? `Extending the search through Bitcoin block ${formatNumber(Number(extension[1]))}…`
            : stages[latest] ? stages[latest]
              : estimate && latest.startsWith('Estimated Bitcoin search window:') ? 'Preparing the bounded scan segment…'
            : recorded ? 'Checking recorded publication evidence…'
              : continuing ? 'Checking the same Hemi block hash, then scanning the offered next Bitcoin segment…'
                : job.phase === 'public_discovery' ? 'Reading public sources…' : 'Preparing publication search…';
  const started = Date.parse(job.started_at);
  const elapsed = Number.isFinite(started) ? `${Math.max(0, Math.round((now - started) / 1000))}s elapsed` : 'Elapsed time unavailable';
  return <section id="job-progress" className="job-progress" aria-labelledby="progress-heading" aria-busy="true">
    <p className="eyebrow">{stronger ? 'FIRST-PUBLICATION CHECK IN PROGRESS' : refresh ? 'SOURCE RECHECK IN PROGRESS' : recorded ? 'RECORDED EVIDENCE SEARCH' : 'PUBLICATION SEARCH IN PROGRESS'}</p>
    <div className="progress-header"><span className="spinner" aria-hidden="true" /><h2 id="progress-heading">{refresh ? 'Rechecking sources…' : `Searching Hemi ${typeof job.block_input === 'string' ? shortHash(job.block_input) : formatNumber(job.block_number)}…`}</h2><span id="progress-time">{elapsed}</span></div>
    <p id="progress-latest" role="status" aria-live="polite">{message}</p>
    {estimate && <p id="progress-search-plan" className="field-help">Estimated Bitcoin window: {formatNumber(Number(estimate[1]))}–{formatNumber(Number(estimate[2]))}. Planned first segment: {formatNumber(Number(estimate[3]))}–{formatNumber(Number(estimate[4]))}. Time, response-data and download limits can stop this segment earlier.</p>}
    <p className="field-help">{stronger ? 'Preparing the additional evidence within this search\u2019s existing limits. A publication result remains useful if the first-publication check cannot finish.' : refresh ? 'Retained proof data is preserved. This operation does not scan additional Bitcoin blocks or expand the original search coverage.' : recorded ? 'Searching server-side records. Historical source observations and collected coverage remain unchanged.' : 'Only fully scanned Bitcoin blocks count toward search coverage. Proof and source checks are reported separately in the result.'}</p>
  </section>;
}
