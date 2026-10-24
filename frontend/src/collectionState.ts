const states: Record<string, string> = {
  queued: 'Collection queued', running: 'Collection in progress', waiting: 'Waiting for Bitcoin blocks',
  complete: 'Collection completed', incomplete: 'Collection incomplete', limited: 'Collection paused at its limit',
  source_conflict: 'Source views changed', unavailable: 'Collection status unavailable',
  stopped: 'Collection stopped', stopping: 'Stopping collection', stop_requested: 'Collection stop requested',
  blocked: 'Collection needs operator review', storage_limited: 'Collection paused for storage review',
};

export const collectionState = (state: string) => states[state] || 'Recorded collection state';
