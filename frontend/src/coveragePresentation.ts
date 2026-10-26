import { formatNumber } from './format';
import type { Receipt } from './types';

export function coverageIssuesText(receipt: Receipt): string {
  const coverage = receipt.coverage || {};
  const parts: string[] = [];
  if (coverage.gaps?.length) parts.push(`${formatNumber(coverage.gaps.length)} scan gap${coverage.gaps.length === 1 ? '' : 's'}`);
  if (coverage.unresolved_candidates?.length) parts.push(`${formatNumber(coverage.unresolved_candidates.length)} unresolved publication association${coverage.unresolved_candidates.length === 1 ? '' : 's'}`);
  if (coverage.unsupported_payloads) parts.push(`${formatNumber(coverage.unsupported_payloads)} unsupported payload${coverage.unsupported_payloads === 1 ? '' : 's'}`);
  return parts.join(' · ');
}

export function searchCoverageText(receipt: Receipt): string {
  const coverage = receipt.coverage || {};
  const checked = [...new Set((coverage.fully_scanned_intervals || []).map(([a, b]) => a === b ? formatNumber(a) : `${formatNumber(a)}–${formatNumber(b)}`))].join(', ');
  const label = receipt.mode === 'offline_catalogue' ? 'Retained Bitcoin coverage' : receipt.mode === 'live_metadata_refresh' ? 'Original search coverage' : 'Bitcoin blocks scanned';
  let text = checked ? `${label}: ${checked}.` : 'No complete Bitcoin block scan was recorded.';
  if (receipt.mode === 'offline_catalogue') text += ' Compiled retained-data coverage; no new Bitcoin blocks were scanned for this request.';
  if (coverage.plan) text += ` ${coverage.plan.mode === 'explicit_range' ? 'Requested range' : 'Initial estimated range'}: ${formatNumber(coverage.plan.start)}–${formatNumber(coverage.plan.initial_end)}.`;
  if (coverage.plan?.execution_segment) text += ` Planned scan segment: ${coverage.plan.execution_segment.map(formatNumber).join('–')}.`;
  if (receipt.mode === 'live_metadata_refresh') text += ' Coverage from the original search. This operation rechecked source views without scanning additional Bitcoin blocks.';
  return text;
}

export function searchStopText(receipt: Receipt): string {
  const stop = receipt.coverage?.stop_reason;
  const detail = receipt.coverage?.stop_detail || [...(receipt.issues || [])].reverse().find(issue => issue.code === 'budget_limit');
  const message = detail?.message || '';
  const limit = detail?.limit || (message.includes('body-attempt') ? 'bitcoin_body_attempts'
    : message.includes('Per-lookup response') ? 'response_bytes'
      : message.includes('session budget') ? 'cumulative_response_bytes' : message.includes('Request limit') ? 'requests' : undefined);
  if (stop === 'budget_limit' || stop === 'body_attempt_budget') {
    const limits = receipt.resources?.limits;
    const text = limit === 'bitcoin_body_attempts' || stop === 'body_attempt_budget'
      ? `Reached this operation’s Bitcoin block download limit${limits?.bitcoin_body_attempts ? ` (${formatNumber(limits.bitcoin_body_attempts)} attempts)` : ''}.`
      : limit === 'response_bytes' ? 'Reached this operation’s response-data limit.'
        : limit === 'cumulative_response_bytes' ? 'The installation has insufficient cumulative response-data allowance for the next request.'
          : limit === 'requests' ? 'Reached this operation’s request limit.' : 'Reached a search limit.';
    return `${receipt.mode === 'live_metadata_refresh' ? 'Original search: ' : ''}${text}${detail?.bitcoin_height !== undefined ? ` Stopped before completing Bitcoin ${formatNumber(detail.bitcoin_height)}.` : ''} Results cover only the Bitcoin blocks shown.`;
  }
  const labels: Record<string, string> = {
    first_matching_height_fully_processed: 'Stopped after processing the Bitcoin block containing matching evidence.',
    explicit_range_end: 'Reached the end of the requested range.',
    search_segment_end: 'Reached the end of the planned scan segment. The estimated window has unfinished heights.',
    estimated_window_end: 'Reached the end of the initial estimated window. A publication may exist outside the checked blocks.',
    observed_tip_reached: 'Reached the Bitcoin chain tip reported by the source.',
    budget_limit: 'Search limit reached. Results cover only the Bitcoin blocks shown.',
    body_attempt_budget: 'Search limit reached. Results cover only the Bitcoin blocks shown.',
    time_budget_exhausted: 'Search timed out.', lookup_incomplete: 'The search could not finish.',
    retained_corpus_processed: 'Recorded evidence retrieved from the processed retained corpus.',
  };
  return `${receipt.mode === 'live_metadata_refresh' ? 'Original search: ' : ''}${labels[receipt.coverage?.stop_reason || ''] || 'The search stopping reason was not recorded.'}`;
}
