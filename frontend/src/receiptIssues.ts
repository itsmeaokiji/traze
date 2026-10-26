import { issueMessage } from './api';
import { operationTimedOut } from './evidenceState';
import { coverageIssuesText } from './coveragePresentation';
import { sourceState } from './evidenceState';
import type { Issue, Receipt } from './types';

export function receiptIssues(r: Receipt, sourceOperationsEnabled = true, options: { primaryStopShown?: boolean; compact?: boolean } = {}): { warnings: string[]; raw: Issue[]; details: string[] } {
  const coverage = r.coverage || {};
  const blocks = coverage.blocks || [];
  const gaps = coverage.gaps || [];
  const unresolved = coverage.unresolved_candidates || [];
  const context = r.context || {};
  const observations = [...(context.hemi_headers || []), ...(context.bitcoin_blocks || []), ...(context.bitcoin_secondary || [])];
  const raw = [...(r.issues || []), ...gaps, ...unresolved];
  const refresh = r.mode === 'live_metadata_refresh';
  // Only suppress a duplicate of the same search stop already displayed above.
  // Refresh failures, conflicts and all raw issues retain their own meaning.
  const repeatedStop = (issue: Issue) => options.primaryStopShown && !refresh && (
    issue.code === 'budget_limit' && ['budget_limit', 'body_attempt_budget'].includes(coverage.stop_reason || '')
    || issue.code === 'time_budget_exhausted' && coverage.stop_reason === 'time_budget_exhausted');
  const messageFor = (issue: Issue) => refresh && issue.code === 'time_budget_exhausted'
    ? 'Source recheck timed out. Retained evidence and original search coverage are preserved.'
    : refresh && issue.code === 'budget_limit' ? 'Source recheck limit reached. Retained evidence and original search coverage are preserved.'
      : issue.code === 'catalogue_recheck_unavailable' ? `Recorded keystone metadata is not sufficient for a source recheck.${sourceOperationsEnabled ? ' Use Run new lookup for a new search.' : ''}`
      : issueMessage(issue, { partial: Boolean(r.proofs.length || blocks.length), sourceConfirmed: context.current_view_confirmed === true });
  const warnings = new Set((r.issues || []).filter(issue => !repeatedStop(issue)).map(messageFor));
  const coverageIssues = coverageIssuesText(r);
  if (coverageIssues) warnings.add(`Coverage issues: ${coverageIssues}.`);
  if (observations.some(item => item.same === false)) warnings.add('Recorded block data did not match the source view at the last check. Chain status could not be confirmed.');
  else if (r.mode !== 'offline_catalogue' && context.current_view_confirmed !== true) warnings.add('Source checks remain incomplete.');
  if (operationTimedOut(r) && !(r.issues || []).some(issue => issue.code === 'time_budget_exhausted')
      && !repeatedStop({ code: 'time_budget_exhausted' })) {
    warnings.add(refresh ? 'Source recheck timed out. Retained evidence and original search coverage are preserved.'
      : issueMessage({ code: 'time_budget_exhausted' }, { partial: Boolean(r.proofs.length || blocks.length) }));
  }
  if (context.error) raw.push({ code: 'source_check', message: context.error });
  if (context.requested_block_identity?.reason) raw.push({ code: 'block_identity', message: context.requested_block_identity.reason });
  for (const observation of observations) if (observation.error) raw.push({ code: 'source_check', message: `Block ${observation.height}: ${observation.error}` });
  const original = [...warnings];
  if (!options.compact) return { warnings: original, raw, details: [] };
  // The always-visible coverage summary owns these exact qualifications. Other
  // issues, especially invalid data and source conflicts, stay above the route.
  if (coverageIssues) warnings.delete(`Coverage issues: ${coverageIssues}.`);
  warnings.delete(issueMessage({ code: 'no_processed_scope' }));
  const conflict = sourceState(r) === 'conflict';
  if (conflict) {
    for (const issue of r.issues || []) if (issue.code === 'source_conflict') warnings.delete(messageFor(issue));
    warnings.delete('Recorded block data did not match the source view at the last check. Chain status could not be confirmed.');
    warnings.add(issueMessage({ code: 'source_conflict' }));
  }
  const unavailable = (r.issues || []).filter(issue => ['source_unavailable', 'network_recheck_unavailable', 'tip_recheck_unavailable'].includes(issue.code || ''));
  if (unavailable.length) {
    for (const issue of unavailable) warnings.delete(messageFor(issue));
    warnings.delete('Source checks remain incomplete.');
    warnings.add(refresh ? 'Some source rechecks could not finish. Retained evidence and original search coverage are preserved.'
      : 'Some source checks could not finish. Current chain membership is not confirmed.');
  }
  const compact = [...warnings];
  return { warnings: compact, raw, details: original.filter(message => !warnings.has(message)) };
}
