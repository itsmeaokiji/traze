import type { Issue } from './types';

export const blockInputHelp = 'Enter a valid Hemi block number or a full 0x-prefixed 64-character hexadecimal block hash.';
export const rangeInputHelp = 'Enter both Bitcoin block heights. The ending height must be greater than or equal to the starting height.';

export function issueMessage(issue: Issue, options: { partial?: boolean; sourceConfirmed?: boolean } = {}): string {
  const { partial = false, sourceConfirmed = false } = options;
  if (issue.code === 'budget_limit') {
    return (issue.limit === 'cumulative_response_bytes' || (!issue.limit && issue.message?.includes('session budget reached')))
      ? 'The local demo has reached its search allowance. Saved results remain available.'
      : partial ? 'Search limit reached. Results cover only the Bitcoin blocks shown.' : 'Search limit reached. The operation could not finish.';
  }
  if (issue.code === 'time_budget_exhausted') return `Search timed out.${partial ? ' Partial results are available.' : ''}`;
  const messages: Record<string, string> = {
    index_unavailable: 'The publication index is unavailable. Retry later; no source search was started.',
    no_processed_scope: 'No complete Bitcoin block scan is recorded in this indexed result. Publication proof checks are separate.',
    retained_view_conflict: 'Retained metadata has conflicting views. Use an exact recorded hash; current chain membership is not confirmed.',
    record_not_found: 'This record is not available in the selected index. This does not mean no publication exists.',
    invalid_filter: 'Check the height and evidence filters. Use supported whole-number heights and valid choices.',
    invalid_record: 'Use a complete publication record identifier.',
    invalid_cursor: 'This page cursor is invalid for the filters. Start browsing again.',
    read_busy: 'Indexed read capacity is busy. Retry shortly.',
    read_rate: 'Read request rate is busy. Retry shortly with backoff.',
    service_unavailable: 'The read service is unavailable. Retry later.',
    read_capacity: 'This indexed view exceeds the read budget. Narrow the request or retry later.',
    response_capacity: 'This indexed response exceeds the response limit. Narrow the request.',
    read_api_required: 'Reload Traze to use the current indexed search interface.',
    indexed_evidence_unavailable: 'No usable indexed evidence is available for this input. Collected coverage may be incomplete; this does not mean no publication exists.',
    source_operations_disabled: 'This website serves indexed evidence. Live discovery and source rechecks are unavailable here.',
    invalid_block: blockInputHelp,
    invalid_input: blockInputHelp,
    invalid_range: 'Enter an ascending Bitcoin range of at most 200 existing block heights.',
    requested_block_unavailable: 'The Hemi source could not return the requested block.',
    requested_block_not_current: 'At the last check, the Hemi RPC reported a different block hash at this height.',
    source_conflict: 'Sources returned conflicting data. Chain status could not be confirmed.',
    inconsistent_data: 'Source data could not be confirmed. Some checks remain incomplete.',
    wrong_network: 'The source did not return valid Hemi Mainnet data. Source checks remain incomplete.',
    source_unavailable: sourceConfirmed ? 'Some source requests could not be completed.' : 'Some sources were unavailable. Source checks remain incomplete.',
    network_recheck_unavailable: 'The Hemi source could not be checked again.',
    tip_recheck_unavailable: 'The Bitcoin chain tip could not be checked again.',
    invalid_response: 'A source returned data that could not be checked.',
    response_incomplete: 'Some source data could not be read completely.',
    response_too_large: 'Some source data could not be read within the search limits.',
    unsupported_payload: 'Some publication data could not be verified.',
    proof_retention_limit: 'Some additional publication candidates could not be retained. The evidence list may be incomplete.',
    invalid_proof: 'The saved evidence did not pass its proof checks.',
    invalid_receipt: 'This saved result could not be checked.',
    cache_corrupt: 'Saved Bitcoin data could not be checked.',
    busy: 'Another search is already running. Wait for it to finish.',
    session_locked: 'Another search may still be running. Saved results remain available.',
    session_changed: "The local demo's search allowance could not be verified. Saved results remain available.",
    invalid_prior_budget: "The local demo's search allowance could not be verified. Saved results remain available.",
    refresh_unavailable: 'This result needs a new search before its sources can be rechecked.',
    invalid_continuation: 'Use the continuation offered by this saved result.',
    continuation_changed: 'This saved result or its continuation changed. Reopen it before continuing; no acquisition was started.',
    continuation_unavailable: 'This search cannot be continued from the selected result. Reopen its latest segment or review the recovery message.',
    continuation_allowance: 'No usable discovery allowance is available. Saved results remain readable; operator review is needed before further acquisition.',
    unknown_receipt: 'This saved result could not be found. Choose another saved result.',
    saved_receipt_unavailable: 'This saved file is unavailable or unreadable. Choose another saved result; no source search was started.',
    retained_read_capacity: 'Saved evidence could not be fully checked within the local read limit. Open a saved result or ask the operator to review the retained data. No source search was started.',
    allowance_unavailable: 'The discovery allowance could not be verified. Saved results remain available; the operator should review accounting before further source requests.',
    restored_state_requires_review: 'Restored accounting needs operator reconciliation before source requests. Saved results remain available.',
    index_update_disabled: 'Local index updates are unavailable in this serving mode. Saved evidence remains readable.',
    invalid_index_update: 'This saved index update could not be validated. Open the original saved result.',
    receipt_changed: 'The saved evidence differs from its queued index update. The operator should review the original receipt.',
    invalid_history_cursor: 'This history page is no longer usable. Start history again to reload the list.',
    history_capacity: 'Saved history exceeds this local listing capacity. Existing result links still work; operator review is needed for a larger archive.',
    unknown_job: 'This operation could not be found. Reload the page to reconnect.',
    invalid_origin: 'Open Traze at its configured website address.',
    invalid_host: 'Open Traze at its configured website address.',
    local_connection_unavailable: 'Unable to contact Traze. Reload the page to reconnect.',
  };
  return messages[issue.code || ''] || 'Some checks could not be completed. Review the result and search coverage.';
}

export class ApiError extends Error {
  readonly diagnostic: string;
  constructor(issue: Issue = {}) {
    const known = issue.code && issue.code !== 'local_error' && issue.code !== 'invalid_data';
    super(known ? issueMessage(issue) : 'The operation could not finish. Saved results remain available.');
    this.diagnostic = `${issue.code || 'local_error'}: ${issue.message || 'No diagnostic message recorded'}`;
  }
}

export async function api<T>(path: string, signal: AbortSignal, payload?: object, token = ''): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, payload === undefined ? { cache: 'no-store', signal } : {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Local-Token': token },
      body: JSON.stringify(payload), cache: 'no-store', signal,
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new ApiError({ code: 'local_connection_unavailable', message: 'Local server connection failed' });
  }
  let body: T & { error?: Issue };
  try { body = await response.json() as T & { error?: Issue }; }
  catch (error) {
    if (signal.aborted) throw error;
    throw new ApiError({ code: 'local_error', message: `Unreadable local response (${response.status})` });
  }
  if (!response.ok) throw new ApiError(body.error || { code: 'local_error', message: `Local request failed (${response.status})` });
  return body;
}

export type InputField = 'block-input' | 'range-start' | 'range-end';
export class QueryError extends Error {
  constructor(message: string, readonly field: InputField) { super(message); }
}
function integer(raw: string, allowZero: boolean, message: string, field: InputField): number {
  const text = raw.trim();
  const valid = /^\d+$/.test(text) || /^\d{1,3}(,\d{3})+$/.test(text);
  const value = valid ? Number(text.replaceAll(',', '')) : NaN;
  if (!Number.isSafeInteger(value) || value < (allowZero ? 0 : 1)) throw new QueryError(message, field);
  return value;
}

export function parseQuery(input: string, start: string, end: string): { block: number | string; bitcoin_range: [number, number] | null } {
  const text = input.trim();
  let block: number | string;
  if (/^0x[0-9a-f]{64}$/i.test(text)) block = text.toLowerCase();
  else {
    block = integer(text, false, blockInputHelp, 'block-input');
    if (Math.ceil(block / 25) * 25 > 0xffffffff) throw new QueryError('This Hemi block is outside the supported keystone format.', 'block-input');
  }
  let range: [number, number] | null = null;
  if (start.trim() || end.trim()) {
    range = [integer(start, true, rangeInputHelp, 'range-start'), integer(end, true, rangeInputHelp, 'range-end')];
    if (range[1] < range[0]) throw new QueryError(rangeInputHelp, 'range-end');
    if (range[1] - range[0] + 1 > 200) throw new QueryError('Use a Bitcoin range of at most 200 block heights. Search limits still apply.', 'range-end');
  }
  return { block, bitcoin_range: range };
}

export function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException('Page connection closed', 'AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, milliseconds);
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, { once: true });
  });
}
