import { useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, parseQuery, QueryError } from './api';
import type { InputField } from './api';
import { Diagnostics, EvidenceView } from './EvidenceView';
import { formatNumber, hemiSource, receiptInput, shortHash } from './format';
import type { SavedResult } from './types';
import { useWorkspace } from './useWorkspace';
import ThemeToggle from './ThemeToggle';
import { OperationProgress } from './OperationProgress';
import { StatusIcon } from './StatusIcon';
import { ExternalLink } from './ExternalLink';
import { CollectionStatus } from './CollectionStatus';

function savedLabel(receipt: SavedResult): string {
  const examples: Record<string, string> = { 'example-ordinary': 'Ordinary block', 'example-checkpoint': 'Keystone block', 'example-no-match': 'No match in range' };
  const status = receipt.status === 'found' ? 'found' : receipt.status === 'not_found_within_coverage' ? 'no match' : 'incomplete';
  return receipt.origin === 'saved_example' ? examples[receipt.id] || receipt.label : `${receipt.mode === 'offline_catalogue' ? 'Recorded evidence' : receipt.mode === 'live_metadata_refresh' ? 'Source recheck' : 'Search'} · ${status}`;
}
function SavedResults({ receipts, ready, busy, onSelect }: { receipts: SavedResult[]; ready: boolean; busy: boolean; onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const summary = useRef<HTMLElement>(null);
  function choose(id: string) {
    setOpen(false);
    summary.current?.focus();
    onSelect(id);
  }
  return <details id="saved-results" className="saved-evidence" open={open}>
    <summary ref={summary} aria-expanded={open} onClick={event => { event.preventDefault(); setOpen(value => !value); }}><span>Saved results</span>{ready && <span className="saved-count">{receipts.length}</span>}</summary>
    {open && <div className="saved-content"><p>Opening a saved result does not contact public sources.</p><div id="saved-list" className="saved-list">
      {!ready ? <span className="muted">Loading saved results…</span> : receipts.length ? receipts.map(receipt => {
        const inputLabel = receipt.block_number !== null ? formatNumber(receipt.block_number) : receipt.requested_block_hash ? shortHash(receipt.requested_block_hash) : 'unresolved input';
        return <button type="button" key={receipt.id} data-receipt-id={receipt.id} className="saved-item" disabled={busy} aria-label={`Open saved ${savedLabel(receipt)} for Hemi ${inputLabel}`} onClick={() => choose(receipt.id)}><strong>{receipt.block_number !== null ? formatNumber(receipt.block_number) : receipt.requested_block_hash ? shortHash(receipt.requested_block_hash) : 'Unresolved block'}</strong><span>{savedLabel(receipt)}</span></button>;
      }) : <span className="muted">No saved results are available. Search for a block to create one.</span>}
    </div></div>}
  </details>;
}
export default function App() {
  const workspace = useWorkspace();
  const [rangeStart, setRangeStart] = useState('');
  const [rangeEnd, setRangeEnd] = useState('');
  const [invalidField, setInvalidField] = useState<InputField>();
  const blockRef = useRef<HTMLInputElement>(null);
  const startRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLInputElement>(null);
  const receipts = workspace.workspace?.receipts || [];
  const saved = [...receipts.filter(r => r.origin === 'saved_example'), ...receipts.filter(r => r.origin !== 'saved_example').sort((a, b) => (b.completed_at || '').localeCompare(a.completed_at || '')).slice(0, 2)];

  function submit(event: FormEvent) {
    event.preventDefault();
    if (workspace.busy) return;
    try {
      const query = parseQuery(workspace.input, rangeStart, rangeEnd);
      setInvalidField(undefined);
      void workspace.startOperation('lookup', query);
    } catch (error) {
      if (error instanceof QueryError) {
        setInvalidField(error.field);
        ({ 'block-input': blockRef, 'range-start': startRef, 'range-end': endRef }[error.field]).current?.focus();
      }
      workspace.report(error);
    }
  }
  const scoped = Boolean(rangeStart.trim() || rangeEnd.trim());
  function newLookup() {
    if (!workspace.result || workspace.busy) return;
    try {
      const query = parseQuery(receiptInput(workspace.result.receipt), rangeStart, rangeEnd);
      setInvalidField(undefined);
      void workspace.startOperation('lookup', { ...query, new_lookup: true });
    } catch (error) { workspace.report(error); }
  }
  const lookupLabel = workspace.busy && workspace.job ? workspace.job.operation === 'refresh' ? 'Recheck in progress' : 'Search in progress' : 'Find publication';
  const timeoutError = workspace.error instanceof ApiError && workspace.activity?.state === 'error' && workspace.activity.error?.code === 'time_budget_exhausted';
  const errorMessage = timeoutError && workspace.activity?.operation === 'refresh'
    ? 'Source recheck timed out. Open the saved result to inspect its retained evidence and original search coverage.' : workspace.error?.message;
  return <>
    <header className="topbar"><div className="network-label">Traze</div><div className="header-actions"><span className="local-label"><span className="local-dot" aria-hidden="true" />Local demo</span><ThemeToggle /></div></header>
    <main>
      <div className="workspace-heading"><div><h1>Publication evidence</h1><p className="intro">Find Bitcoin publication evidence supporting a Hemi block.</p></div><span className="network-note">Hemi Mainnet · Bitcoin Mainnet</span></div>
      <section className="lookup-workspace" aria-labelledby="lookup-heading">
        <h2 id="lookup-heading" className="sr-only">Find publication evidence for a Hemi block</h2>
        <form id="lookup-form" noValidate onSubmit={submit} aria-busy={workspace.busy}>
          <label htmlFor="block-input">Hemi block number or hash</label>
          <div className="input-row"><input id="block-input" ref={blockRef} name="block" type="text" autoComplete="off" spellCheck={false} value={workspace.input} disabled={workspace.busy} aria-invalid={invalidField === 'block-input'} aria-describedby="input-help" required onChange={event => { workspace.setInput(event.target.value); setInvalidField(undefined); }} /><button className="primary" id="lookup-button" type="submit" disabled={workspace.busy || !workspace.workspace} aria-label={lookupLabel} title={lookupLabel}><svg className={`lookup-icon${workspace.busy && workspace.job ? ' is-pending' : ''}`} width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{workspace.busy && workspace.job ? <path d="M20 12a8 8 0 1 1-8-8" /> : <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" /></>}</svg><span className="lookup-label">{lookupLabel}</span></button></div>
          <p id="input-help" className="field-help">Enter a block number or a full 0x-prefixed block hash. Recorded evidence is used when available; new source searches may take several minutes.</p>
          <div className="lookup-options"><details className="scope-options"><summary><span id="scope-label">{scoped ? `Bitcoin range: ${rangeStart.trim() || '…'}–${rangeEnd.trim() || '…'}` : 'Bitcoin search range'}</span> <span id="scope-note" className="optional">{scoped ? 'specified range' : 'optional'}</span></summary>
            <div className="range-fields"><div><label htmlFor="range-start">From Bitcoin block</label><input id="range-start" ref={startRef} inputMode="numeric" autoComplete="off" placeholder="965925" value={rangeStart} disabled={workspace.busy} aria-invalid={invalidField === 'range-start'} onChange={event => { setRangeStart(event.target.value); setInvalidField(undefined); }} /></div><div><label htmlFor="range-end">Through Bitcoin block</label><input id="range-end" ref={endRef} inputMode="numeric" autoComplete="off" placeholder="965930" value={rangeEnd} disabled={workspace.busy} aria-invalid={invalidField === 'range-end'} onChange={event => { setRangeEnd(event.target.value); setInvalidField(undefined); }} /></div></div>
            <p className="field-help">Search only within these Bitcoin block heights. Leave blank to start with a range estimated from the Hemi block timestamp.</p>
          </details>
          <SavedResults receipts={saved} ready={Boolean(workspace.workspace)} busy={workspace.busy} onSelect={id => { setInvalidField(undefined); void workspace.openSaved(id); }} /></div>
        </form>
      </section>
      <CollectionStatus value={workspace.workspace?.collection} />
      {workspace.error && <div id="error-message" className="error-message" role="alert"><StatusIcon tone="error" /><p>{errorMessage}</p></div>}
      {workspace.job ? <OperationProgress job={workspace.job} /> : workspace.result ? <EvidenceView key={workspace.result.id} wrapper={workspace.result} fresh={workspace.fresh} busy={workspace.busy} onError={workspace.report} onNewLookup={newLookup} onRecheck={() => { if (workspace.result) void workspace.startOperation('refresh', { receipt_id: workspace.result.id }); }} /> : <section id="empty-state" className="empty-state"><div className="empty-path" aria-hidden="true"><span>Hemi block</span><span>→</span><span>Keystone</span><span>→</span><span>Bitcoin publication</span></div>{workspace.busy ? <p role="status">{workspace.workspace ? 'Loading evidence…' : 'Loading local workspace…'}</p> : <p>Search for a Hemi block or open a saved result to inspect its publication evidence.</p>}</section>}
      <Diagnostics workspace={workspace.workspace} result={workspace.result} activity={workspace.activity} error={workspace.error} running={Boolean(workspace.job)} />
      <footer className="workspace-footer"><p>Public data sources: <ExternalLink id="hemi-source-link" href={hemiSource(workspace.result?.receipt)}>Hemi RPC</ExternalLink> · <ExternalLink href="https://blockstream.info">Blockstream</ExternalLink> · <ExternalLink href="https://mempool.space">mempool.space</ExternalLink></p></footer>
    </main>
  </>;
}
