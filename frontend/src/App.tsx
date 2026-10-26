import { useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, parseQuery, QueryError } from './api';
import type { InputField } from './api';
import { Diagnostics, EvidenceView } from './EvidenceView';
import { hemiSource, receiptInput } from './format';
import { SavedResults } from './SavedResults';
import { useWorkspace } from './useWorkspace';
import ThemeToggle from './ThemeToggle';
import { OperationProgress } from './OperationProgress';
import { StatusIcon } from './StatusIcon';
import { ExternalLink } from './ExternalLink';
import { CollectionStatus } from './CollectionStatus';
import { ApiGuide, BrowseIndex, PublicationRecordView } from './ExplorerViews';
import { DiscoveryAllowance } from './SearchRecovery';
import { useOperationFeedback } from './useOperationFeedback';
import type { Job } from './types';

export default function App() {
  const workspace = useWorkspace();
  const feedback = useOperationFeedback(workspace.completion);
  const [rangeStart, setRangeStart] = useState('');
  const [rangeEnd, setRangeEnd] = useState('');
  const [invalidField, setInvalidField] = useState<InputField>();
  const blockRef = useRef<HTMLInputElement>(null);
  const startRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLInputElement>(null);
  const receipts = workspace.workspace?.receipts || [];
  const indexedOnly = workspace.workspace?.serving_mode === 'indexed-only';
  const hosted = workspace.workspace?.deployment === 'hosted-candidate';
  const serviceStatus = workspace.workspace?.service_status;
  const path = window.location.pathname;
  const searchPage = path === '/' || path.startsWith('/records/hemi/');
  const publicationRecord = /^\/records\/publications\/([0-9a-f]{64})$/.exec(path);

  async function runOperation(operation: Job['operation'], payload: object) {
    if (workspace.busy || !feedback.begin()) return;
    if (await workspace.startOperation(operation, payload) === false) feedback.clear();
  }
  function openSaved(id: string) {
    feedback.clear(); setInvalidField(undefined); void workspace.openSaved(id);
  }
  function inputError(error: unknown) {
    feedback.clear();
    if (error instanceof QueryError) {
      setInvalidField(error.field);
      const input = ({ 'block-input': blockRef, 'range-start': startRef, 'range-end': endRef }[error.field]).current;
      const details = input?.closest('details');
      if (details) details.open = true;
      input?.focus();
    }
    workspace.report(error);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (workspace.busy) return;
    try {
      const query = parseQuery(workspace.input, rangeStart, rangeEnd);
      setInvalidField(undefined);
      void runOperation('lookup', query);
    } catch (error) {
      inputError(error);
    }
  }
  const scoped = Boolean(rangeStart.trim() || rangeEnd.trim());
  function newLookup() {
    if (!workspace.result || workspace.busy) return;
    try {
      const query = parseQuery(receiptInput(workspace.result.receipt), rangeStart, rangeEnd);
      setInvalidField(undefined);
      void runOperation('lookup', { ...query, new_lookup: true });
    } catch (error) { inputError(error); }
  }
  function continueSearch() {
    const continuation = workspace.result?.continuation;
    if (!workspace.result || !continuation?.available || !continuation.bitcoin_range || workspace.busy) return;
    void runOperation('continue', { receipt_id: workspace.result.id,
      receipt_sha256: continuation.receipt_sha256, bitcoin_range: continuation.bitcoin_range });
  }
  const lookupLabel = workspace.busy && workspace.job ? workspace.job.operation === 'refresh' ? 'Recheck in progress' : 'Search in progress' : 'Find publication';
  const timeoutError = workspace.error instanceof ApiError && workspace.activity?.state === 'error' && workspace.activity.error?.code === 'time_budget_exhausted';
  const errorMessage = timeoutError && workspace.activity?.operation === 'refresh'
    ? 'Source recheck timed out. Open the saved result to inspect its retained evidence and original search coverage.' : workspace.error?.message;
  return <>
    <header className="topbar"><a href="/" className="network-label brand-link" aria-label="Traze home">Traze</a><nav className="explorer-nav" aria-label="Explorer"><a href="/" aria-current={path === '/' || path.startsWith('/records/hemi/') ? 'page' : undefined}>Search</a><a href="/browse" aria-current={path === '/browse' || publicationRecord ? 'page' : undefined}>Browse</a><a href="/api-docs" aria-current={path === '/api-docs' ? 'page' : undefined}>Read API</a></nav><div className="header-actions"><span className="local-label"><span className="local-dot" aria-hidden="true" />{hosted ? 'Indexed explorer' : 'Local demo'}</span><ThemeToggle /></div></header>
    <main>
      {searchPage && <><div className="workspace-heading"><div><h1>Publication evidence</h1><p className="intro">Find Bitcoin publication evidence supporting a Hemi block.</p></div><span className="network-note">Hemi Mainnet · Bitcoin Mainnet</span></div>
      <section className="lookup-workspace" aria-labelledby="lookup-heading">
        <h2 id="lookup-heading" className="sr-only">Find publication evidence for a Hemi block</h2>
        <form id="lookup-form" noValidate onSubmit={submit} aria-busy={workspace.busy}>
          <label htmlFor="block-input">Hemi block number or hash</label>
          <div className="input-row"><input id="block-input" ref={blockRef} name="block" type="text" autoComplete="off" spellCheck={false} value={workspace.input} disabled={workspace.busy} aria-invalid={invalidField === 'block-input'} aria-describedby="input-help" required onChange={event => { workspace.setInput(event.target.value); setInvalidField(undefined); }} /><button className="primary" id="lookup-button" type="submit" disabled={workspace.busy || !workspace.workspace} aria-label={lookupLabel} title={lookupLabel}><svg className={`lookup-icon${workspace.busy && workspace.job ? ' is-pending' : ''}`} width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{workspace.busy && workspace.job ? <path d="M20 12a8 8 0 1 1-8-8" /> : <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" /></>}</svg><span className="lookup-label">{lookupLabel}</span></button></div>
          <p id="input-help" className="field-help">{indexedOnly ? 'Enter a block number or a full 0x-prefixed block hash. Search uses indexed evidence; availability depends on collected coverage.' : 'Enter a block number or a full 0x-prefixed block hash. Recorded evidence is used when available; new source searches may take several minutes.'}</p>
          <DiscoveryAllowance workspace={workspace.workspace} />
          <div className="lookup-options">{!indexedOnly && <details className="scope-options"><summary><span id="scope-label">{scoped ? `Bitcoin range: ${rangeStart.trim() || '…'}–${rangeEnd.trim() || '…'}` : 'Bitcoin search range'}</span> <span id="scope-note" className="optional">{scoped ? 'specified range' : 'optional'}</span></summary>
            <div className="range-fields"><div><label htmlFor="range-start">From Bitcoin block</label><input id="range-start" ref={startRef} inputMode="numeric" autoComplete="off" placeholder="Start height" value={rangeStart} disabled={workspace.busy} aria-invalid={invalidField === 'range-start'} onChange={event => { setRangeStart(event.target.value); setInvalidField(undefined); }} /></div><div><label htmlFor="range-end">Through Bitcoin block</label><input id="range-end" ref={endRef} inputMode="numeric" autoComplete="off" placeholder="End height" value={rangeEnd} disabled={workspace.busy} aria-invalid={invalidField === 'range-end'} onChange={event => { setRangeEnd(event.target.value); setInvalidField(undefined); }} /></div></div>
            <p className="field-help">Search only within these Bitcoin block heights. Leave both fields blank to estimate a range from the Hemi block timestamp.</p>
          </details>}
          {!hosted && <SavedResults receipts={receipts} ready={Boolean(workspace.workspace)} busy={workspace.busy} historyAvailable={workspace.workspace?.saved_history_available} onSelect={openSaved} />}</div>
        </form>
      </section>
      <CollectionStatus value={workspace.workspace?.collection} /></>}
      {searchPage && hosted && serviceStatus && <section className="collection-status" aria-label="Recorded source observations"><p>{serviceStatus.monitoring === 'available' ? <>Recorded observation recency: Bitcoin {serviceStatus.observations.bitcoin?.recency || 'unavailable'} · Hemi {serviceStatus.observations.hemi?.recency || 'unavailable'}.</> : 'The monitoring snapshot is unavailable or outdated. Retained evidence may still be readable.'}</p><p className="muted">Observation recency does not establish chain synchronization or current canonical membership.{serviceStatus.generated_at && <> Status recorded <time dateTime={serviceStatus.generated_at}>{new Date(serviceStatus.generated_at).toLocaleString('en-US')}</time>.</>}</p></section>}
      {workspace.error && <div id="error-message" className="error-message" role="alert" tabIndex={-1}><StatusIcon tone="error" /><p>{errorMessage}</p></div>}
      {searchPage && <div id="operation-feedback" className="operation-feedback"><p id="completion-status" role="status" aria-live={feedback.announce ? 'polite' : 'off'} aria-atomic="true" className={feedback.message ? 'field-help' : 'sr-only'}>{feedback.message}</p>{feedback.deferredTarget && <button type="button" className="text-button" onClick={feedback.view}>{feedback.deferredTarget === 'error-message' ? 'View error' : 'View result'}</button>}</div>}
      {path === '/browse' ? <BrowseIndex /> : path === '/api-docs' ? <ApiGuide /> : publicationRecord ? <PublicationRecordView id={publicationRecord[1]} /> : workspace.job ? <OperationProgress job={workspace.job} /> : workspace.result ? <EvidenceView key={workspace.result.id} wrapper={workspace.result} fresh={workspace.fresh} busy={workspace.busy} onError={workspace.report} workspace={workspace.workspace} onContinue={continueSearch} onOpenSaved={openSaved} sourceOperationsEnabled={!indexedOnly && !workspace.result.record_url} onNewLookup={newLookup} onRecheck={() => { if (workspace.result) void runOperation('refresh', { receipt_id: workspace.result.id }); }} /> : <section id="empty-state" className="empty-state"><div className="empty-path" aria-hidden="true"><span>Hemi block</span><span>→</span><span>Keystone</span><span>→</span><span>Bitcoin publication</span></div>{workspace.busy ? <p role="status">{workspace.workspace ? 'Loading evidence…' : 'Loading local workspace…'}</p> : <p>Search for a Hemi block or <a href="/browse">browse indexed publications</a> to inspect their evidence.</p>}</section>}
      {searchPage && !hosted && <Diagnostics workspace={workspace.workspace} result={workspace.result} activity={workspace.activity} error={workspace.error} running={Boolean(workspace.job)} />}
      <footer className="workspace-footer"><p>Public data sources: <ExternalLink id="hemi-source-link" href={hemiSource(workspace.result?.receipt)}>Hemi RPC</ExternalLink> · <ExternalLink href="https://blockstream.info">Blockstream</ExternalLink> · <ExternalLink href="https://mempool.space">mempool.space</ExternalLink></p></footer>
    </main>
  </>;
}
