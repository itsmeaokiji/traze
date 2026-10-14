import { issueMessage } from './api';
import { operationTimedOut } from './evidenceState';
import type { Issue, Receipt } from './types';

export function receiptIssues(r: Receipt): { warnings: string[]; raw: Issue[] } {
  const coverage = r.coverage || {};
  const blocks = coverage.blocks || [];
  const gaps = coverage.gaps || [];
  const unresolved = coverage.unresolved_candidates || [];
  const context = r.context || {};
  const observations = [...(context.hemi_headers || []), ...(context.bitcoin_blocks || []), ...(context.bitcoin_secondary || [])];
  const raw = [...(r.issues || []), ...gaps, ...unresolved];
  const refresh = r.mode === 'live_metadata_refresh';
  const warnings = new Set((r.issues || []).map(issue => refresh && issue.code === 'time_budget_exhausted'
    ? 'Source recheck timed out. Retained evidence and original search coverage are preserved.'
    : refresh && issue.code === 'budget_limit' ? 'Source recheck limit reached. Retained evidence and original search coverage are preserved.'
      : issue.code === 'catalogue_recheck_unavailable' ? 'Recorded keystone metadata is not sufficient for a source recheck. Use Run new lookup for a new search.'
      : issueMessage(issue, { partial: Boolean(r.proofs.length || blocks.length), sourceConfirmed: context.current_view_confirmed === true })));
  if (gaps.length || unresolved.length) warnings.add('Some blocks or publication candidates could not be checked.');
  if (coverage.unsupported_payloads) warnings.add('Some publication data could not be verified.');
  if (observations.some(item => item.same === false)) warnings.add('Recorded block data did not match the source view at the last check. Chain status could not be confirmed.');
  else if (r.mode !== 'offline_catalogue' && context.current_view_confirmed !== true) warnings.add('Source checks remain incomplete.');
  if (operationTimedOut(r) && !(r.issues || []).some(issue => issue.code === 'time_budget_exhausted')) {
    warnings.add(refresh ? 'Source recheck timed out. Retained evidence and original search coverage are preserved.'
      : issueMessage({ code: 'time_budget_exhausted' }, { partial: Boolean(r.proofs.length || blocks.length) }));
  }
  if (context.error) raw.push({ code: 'source_check', message: context.error });
  if (context.requested_block_identity?.reason) raw.push({ code: 'block_identity', message: context.requested_block_identity.reason });
  for (const observation of observations) if (observation.error) raw.push({ code: 'source_check', message: `Block ${observation.height}: ${observation.error}` });
  return { warnings: [...warnings], raw };
}
