import { useEffect, useRef, useState } from 'react';
import { api, ApiError, delay } from './api';
import { receiptInput } from './format';
import { browseReturn, withBrowseReturn } from './browseNavigation';
import type { Job, ReceiptWrapper, WorkspaceState } from './types';

export function useWorkspace() {
  const [workspace, setWorkspace] = useState<WorkspaceState>();
  const [result, setResult] = useState<ReceiptWrapper>();
  const [fresh, setFresh] = useState(false);
  const [job, setJob] = useState<Job>();
  const [activity, setActivity] = useState<Job>();
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<Error>();
  const [input, setInput] = useState('5243117');
  const controller = useRef<AbortController | null>(null);
  const occupied = useRef(true);
  const token = useRef('');
  const connected = useRef(false);

  function report(error: unknown) {
    setError(error instanceof Error ? error : new Error('The operation could not finish.'));
  }
  async function loadState(signal: AbortSignal) {
    const next = await api<WorkspaceState>('/api/state', signal);
    if (signal.aborted) return;
    token.current = next.csrf_token;
    connected.current = true;
    setWorkspace(next);
    return next;
  }
  async function loadReceipt(id: string, signal: AbortSignal, isFresh = false) {
    const next = await api<ReceiptWrapper>(`/api/receipts/${encodeURIComponent(id)}`, signal);
    if (signal.aborted) return;
    setResult(next);
    setFresh(isFresh);
    setInput(receiptInput(next.receipt));
    window.history.replaceState(null, '', '/');
  }
  async function loadIndexed(target: string | number, signal: AbortSignal, publication?: string | null) {
    const returnTo = browseReturn();
    setInput(String(target));
    const suffix = publication ? `?publication=${encodeURIComponent(publication)}` : '';
    const next = await api<ReceiptWrapper>(`/api/v1/blocks/${encodeURIComponent(target)}${suffix}`, signal);
    if (signal.aborted) return;
    if (next.record_url) window.history.replaceState(null, '', withBrowseReturn(next.record_url, returnTo));
    setResult(next); setFresh(true); setInput(receiptInput(next.receipt));
  }
  async function watchJob(initial: Job, signal: AbortSignal) {
    let current = initial;
    if (!signal.aborted && initial.block_input !== undefined) setInput(String(initial.block_input));
    while (current.state === 'running') {
      if (signal.aborted) return;
      setJob(current);
      setActivity(current);
      await delay(1200, signal);
      current = await api<Job>(`/api/jobs/${encodeURIComponent(current.id)}`, signal);
    }
    if (signal.aborted) return;
    setActivity(current);
    if (current.state === 'error') throw new ApiError(current.error || undefined);
    if (!current.receipt_id) throw new ApiError({ code: 'local_error', message: 'Completed operation has no receipt ID' });
    await loadReceipt(current.receipt_id, signal, true);
  }

  useEffect(() => {
    const connection = new AbortController();
    controller.current = connection;
    occupied.current = true;
    const signal = connection.signal;
    void (async () => {
      try {
        const initial = await loadState(signal);
        const exactRecord = /^\/records\/hemi\/(0x[0-9a-f]{64})$/.exec(window.location.pathname);
        if (exactRecord) {
          await loadIndexed(exactRecord[1], signal, new URLSearchParams(window.location.search).get('publication'));
        } else if (initial?.serving_mode !== 'indexed-only' && initial?.active_job?.state === 'running') {
          await watchJob(initial.active_job, signal);
          await loadState(signal);
        }
      } catch (error) { if (!signal.aborted) report(error); }
      finally {
        if (!signal.aborted) { occupied.current = false; setBusy(false); setJob(undefined); }
      }
    })();
    // Disconnect only this page's requests/polling. The server job is unchanged.
    return () => connection.abort();
  }, []);

  useEffect(() => {
    const poll = new AbortController();
    let stopped = false;
    void (async () => {
      while (!stopped) {
        try {
          await delay(4000, poll.signal);
          if (connected.current) await loadState(poll.signal);
        } catch { if (poll.signal.aborted) break; }
      }
    })();
    return () => { stopped = true; poll.abort(); };
  }, []);

  async function openSaved(id: string) {
    const signal = controller.current?.signal;
    if (!signal || signal.aborted || occupied.current || !workspace) return;
    occupied.current = true;
    setBusy(true);
    setError(undefined);
    setActivity(undefined);
    try { await loadReceipt(id, signal); }
    catch (error) { if (!signal.aborted) report(error); }
    finally { if (!signal.aborted) { occupied.current = false; setBusy(false); } }
  }
  async function startOperation(operation: 'lookup' | 'refresh', payload: object) {
    const signal = controller.current?.signal;
    if (!signal || signal.aborted || occupied.current || !workspace) return;
    // A ref gates rapid submissions before React has committed disabled controls.
    occupied.current = true;
    setBusy(true);
    setError(undefined);
    setActivity(undefined);
    // A new query must not keep a previous record's shareable URL on failure.
    if (operation === 'lookup') window.history.replaceState(null, '', '/');
    try {
      if (workspace.serving_mode === 'indexed-only' && operation === 'lookup') {
        const query = payload as { block: string | number };
        setResult(undefined);
        await loadIndexed(query.block, signal);
        return;
      }
      const started = await api<Job>(`/api/${operation}`, signal, payload, token.current);
      if (!signal.aborted) { setResult(undefined); window.history.replaceState(null, '', '/'); }
      await watchJob(started, signal);
    } catch (error) { if (!signal.aborted) report(error); }
    finally {
      if (!signal.aborted) {
        setJob(undefined);
        try { await loadState(signal); } catch (error) { if (!signal.aborted) report(error); }
        if (!signal.aborted) { occupied.current = false; setBusy(false); }
      }
    }
  }
  return { workspace, result, fresh, job, activity, busy, error, input, setInput, report, openSaved, startOperation };
}
