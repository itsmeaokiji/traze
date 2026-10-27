import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from './api';
import { formatDate, formatNumber, shortHash } from './format';
import type { IndexPage, IndexSummary, KeystoneSummary, PublicationDetail, PublicationSummary } from './types';
import { CopyAction } from './CopyAction';
import { ExternalLink } from './ExternalLink';
import { bitcoinReference } from './evidenceLinks';
import { browseReturn, browseReturnLabel, withBrowseReturn } from './browseNavigation';
import { PublicationTimeline } from './PublicationTimeline';
import { CollectionProgress } from './CollectionProgress';
import { BrowseTable } from './BrowseTable';

const stateLabel = { associated: 'Hemi evidence available', pending: 'Hemi evidence unavailable', unsupported: 'Unsupported payload' };

function IndexCoverageDetails({ index }: { index: IndexSummary }) {
  return <><p className="field-help">{index.bitcoin_height_bounds ? `Indexed Bitcoin bounds: ${formatNumber(index.bitcoin_height_bounds[0])}–${formatNumber(index.bitcoin_height_bounds[1])}. Gaps may exist between these bounds. ` : ''}These counts describe collected data, not the checked coverage of an individual result. Current source views have not been rechecked for browsing. {index.counts.unassociated_publications > 0 ? `${formatNumber(index.counts.unassociated_publications)} publications have no usable Hemi evidence. ` : ''}{index.counts.failed_or_pending_bodies > 0 ? `${formatNumber(index.counts.failed_or_pending_bodies)} nominated bodies are incomplete. ` : ''}{index.counts.unsupported_publications > 0 ? `${formatNumber(index.counts.unsupported_publications)} unsupported payloads. ` : ''}</p>{Boolean(index.counts.proof_only_blocks) && <p className="field-help">Bitcoin blocks with publication-proof evidence but without complete block scans: {formatNumber(index.counts.proof_only_blocks)}. These records do not extend full-scan coverage.</p>}{index.last_import_stored_at && <p className="field-help">Index data last stored: {formatDate(index.last_import_stored_at)}. This is not a source-check time.</p>}</>;
}

export function BrowseIndex() {
  const initial = new URLSearchParams(window.location.search);
  const [view, setView] = useState(initial.get('view') === 'timeline' ? 'timeline' : initial.get('view') === 'keystones' ? 'keystones' : 'publications');
  const [keystone, setKeystone] = useState(initial.get('keystone_height') || '');
  const [bitcoin, setBitcoin] = useState(initial.get('bitcoin_height') || '');
  const [state, setState] = useState(initial.get('state') || 'all');
  const [filters, setFilters] = useState({ keystone, bitcoin, state });
  const [cursors, setCursors] = useState<(string | undefined)[]>([initial.get('cursor') || undefined]);
  const [index, setIndex] = useState<IndexSummary>();
  const [page, setPage] = useState<IndexPage<PublicationSummary | KeystoneSummary>>();
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(true);
  const [revision, setRevision] = useState(0);
  const cursor = cursors[cursors.length - 1];
  useEffect(() => {
    const connection = new AbortController();
    const params = new URLSearchParams({ limit: '20' });
    if (filters.keystone) params.set('keystone_height', filters.keystone);
    if (filters.bitcoin) params.set('bitcoin_height', filters.bitcoin);
    if (filters.state !== 'all') params.set('state', filters.state);
    if (cursor) params.set('cursor', cursor);
    const location = new URLSearchParams(params);
    location.delete('limit');
    if (view !== 'publications') location.set('view', view);
    window.history.replaceState(null, '', '/browse' + (location.size ? '?' + location : ''));
    setBusy(true); setError(undefined); setPage(undefined);
    void Promise.all([api<IndexSummary>('/api/v1/index', connection.signal), api<IndexPage<PublicationSummary | KeystoneSummary>>(`/api/v1/${view === 'keystones' ? 'keystones' : 'publications'}?${params}`, connection.signal)])
      .then(([summary, records]) => { if (!connection.signal.aborted) { setIndex(summary); setPage(records); } })
      .catch(cause => { if (!connection.signal.aborted) setError(cause instanceof Error ? cause : new Error('Indexed records are unavailable.')); })
      .finally(() => { if (!connection.signal.aborted) setBusy(false); });
    return () => connection.abort();
  }, [view, cursor, filters, revision]);
  function apply(event: FormEvent) { event.preventDefault(); setFilters({ keystone: keystone.trim(), bitcoin: bitcoin.trim(), state }); setCursors([undefined]); }
  function chooseView(next: string) { if (next === view) return; setPage(undefined); setBusy(true); setView(next); if (next === 'keystones' || view === 'keystones') setCursors([undefined]); if (next === 'keystones' && filters.state === 'unsupported') { setState('all'); setFilters(values => ({ ...values, state: 'all' })); } }
  return <section className={`index-browser${view === 'timeline' ? ' has-timeline' : ''}`} aria-labelledby="browse-heading" aria-busy={busy}>
    <div className="section-heading"><div><p className="eyebrow">INDEXED RECORDS</p><h1 id="browse-heading">Browse publications</h1></div><div className="index-actions"><button type="button" className="secondary" disabled={busy} onClick={() => { setCursors([undefined]); setRevision(value => value + 1); }}>Refresh index</button><a href="/api-docs" className="text-link">Use the read API <span aria-hidden="true">↗</span></a></div></div>
    <p className="section-help">Explore collected Bitcoin publications and the keystones encoded in them. Coverage is limited to the indexed data.</p>
    {index && <div className="index-coverage"><h2>Indexed coverage</h2><p><strong>{formatNumber(index.counts.processed_bodies)}</strong> fully scanned Bitcoin blocks · <strong>{formatNumber(index.counts.supported_publications)}</strong> publications with supported payloads · <strong>{formatNumber(index.counts.associated_publications)}</strong> with Hemi evidence</p>{view === 'timeline' ? <details className="timeline-index-details"><summary>Coverage and source details</summary><IndexCoverageDetails index={index} /></details> : <IndexCoverageDetails index={index} />}</div>}
    {index?.collection && <CollectionProgress value={index.collection} showState />}
    <div className="browse-tabs" role="group" aria-label="Record view"><button type="button" className={view === 'publications' ? 'is-active' : ''} aria-pressed={view === 'publications'} onClick={() => chooseView('publications')}>Publications</button><button type="button" className={view === 'timeline' ? 'is-active' : ''} aria-pressed={view === 'timeline'} onClick={() => chooseView('timeline')}>Timeline</button><button type="button" className={view === 'keystones' ? 'is-active' : ''} aria-pressed={view === 'keystones'} onClick={() => chooseView('keystones')}>Published keystones</button></div>
    <form className="browse-filters" onSubmit={apply}><div><label htmlFor="keystone-filter">Keystone height</label><input id="keystone-filter" inputMode="numeric" value={keystone} onChange={event => setKeystone(event.target.value)} placeholder="Any indexed height" /></div><div><label htmlFor="bitcoin-filter">Bitcoin height</label><input id="bitcoin-filter" inputMode="numeric" value={bitcoin} onChange={event => setBitcoin(event.target.value)} placeholder="Any indexed height" /></div><div><label htmlFor="association-filter">Evidence availability</label><select id="association-filter" value={state} onChange={event => setState(event.target.value)}><option value="all">All records</option><option value="associated">Hemi evidence available</option><option value="pending">Hemi evidence unavailable</option>{view === 'publications' && <option value="unsupported">Unsupported payload</option>}</select></div><button type="submit" className="secondary" disabled={busy}>Apply filters</button></form>
    {(view === 'keystones' || filters.keystone) && <p className="field-help">{view === 'keystones' && 'Only supported publication payloads are grouped here. Open a height to browse its publications; '}Height filters can include different keystone IDs at the same height. Inspect an exact record to check its identity.</p>}
    {error && <p className="error-message" role="alert">{error.message}</p>}
    {busy && <p role="status" className="browse-loading">Loading indexed records…</p>}
    {page && <>{view === 'timeline' ? <PublicationTimeline key={window.location.search} records={page.items as PublicationSummary[]} returnTo={window.location.pathname + window.location.search} /> : <BrowseTable label={view === 'publications' ? 'Indexed Bitcoin publications' : 'Published Hemi keystones'}><table><caption className="sr-only">{view === 'publications' ? 'Indexed Bitcoin publications' : 'Published Hemi keystones'}</caption><thead>{view === 'publications' ? <tr><th>Bitcoin block</th><th>Publication transaction</th><th>Keystone</th><th>Evidence</th><th><span className="sr-only">Record</span></th></tr> : <tr><th>Keystone height</th><th>Keystone ID</th><th>Publications</th><th>With Hemi metadata</th></tr>}</thead><tbody>{page.items.map(item => 'txid' in item ? <tr key={item.id}><td>{item.bitcoin_height === null ? item.height_conflict ? 'Conflicting heights' : 'Not recorded' : formatNumber(item.bitcoin_height)}</td><td><a className="hash-link" href={withBrowseReturn(item.record_url, window.location.pathname + window.location.search)} title={item.txid}>{shortHash(item.txid)}</a><small>Output {item.vout_index}</small></td><td>{item.keystone_height === null ? 'Unsupported' : <a href={`/browse?keystone_height=${item.keystone_height}`}>{formatNumber(item.keystone_height)}</a>}</td><td><span className={`record-state state-${item.state}`}>{stateLabel[item.state]}</span>{item.evidence_scope === 'publication_proof' && <small>Publication proof only</small>}</td><td><a href={withBrowseReturn(item.record_url, window.location.pathname + window.location.search)} className="text-link" aria-label={`Inspect publication ${item.txid}`}>Inspect <span aria-hidden="true">→</span></a></td></tr> : <tr key={item.keystone_id}><td><a href={item.publications_url}>{formatNumber(item.height)}</a></td><td><span className="hash-value" title={item.keystone_id}>{shortHash(item.keystone_id)}</span></td><td>{formatNumber(item.publication_count)}</td><td>{formatNumber(item.associated_count)}</td></tr>)}</tbody></table></BrowseTable>}{view !== 'timeline' && page.items.length === 0 && <p className="browse-empty">No indexed records match these filters. This is not a claim that no publication exists.</p>}<div className="browse-pagination"><button className="secondary" disabled={busy || cursors.length < 2} onClick={() => setCursors(values => values.slice(0, -1))}>Previous page</button><span className="field-help">{page.items.length} records shown</span><button className="secondary" disabled={busy || !page.page.next_cursor} onClick={() => setCursors(values => [...values, page.page.next_cursor!])}>Next page</button><button className="text-button" onClick={() => setCursors([undefined])} disabled={busy}>Start over</button></div><p className="field-help">Summaries describe retained data. Open Hemi evidence to recompute proof checks. Use Refresh index to include newly stored records and return to the first page.</p></>}
  </section>;
}

export function PublicationRecordView({ id }: { id: string }) {
  const [record, setRecord] = useState<PublicationDetail>();
  const [error, setError] = useState<Error>();
  useEffect(() => {
    const connection = new AbortController();
    setRecord(undefined); setError(undefined);
    void api<PublicationDetail>(`/api/v1/publications/${encodeURIComponent(id)}`, connection.signal)
      .then(value => { if (!connection.signal.aborted) setRecord(value); })
      .catch(cause => { if (!connection.signal.aborted) setError(cause instanceof Error ? cause : new Error('Publication record unavailable.')); });
    return () => connection.abort();
  }, [id]);
  if (error) return <p className="error-message" role="alert">{error.message}</p>;
  if (!record) return <p role="status">Loading publication record…</p>;
  const p = record.publication;
  const returnTo = browseReturn();
  return <section className="publication-record" aria-labelledby="publication-heading"><a className="text-link" href={returnTo || "/browse"}>{returnTo ? browseReturnLabel(returnTo) : "← Browse indexed records"}</a><div className="publication-record-heading"><div><p className="eyebrow">RETAINED PUBLICATION</p><h1 id="publication-heading">Bitcoin publication</h1><p className={`record-state state-${p.state}`}>{stateLabel[p.state]}</p></div><CopyAction label="Copy record link" value={new URL(p.record_url, window.location.origin).href} className="secondary" /></div>
    <p className="section-help">This record identifies one transaction output in a supplied Bitcoin block. Open its Hemi evidence to recompute payload, inclusion and ancestry checks.</p>
    <dl className="publication-fields"><div><dt>Transaction ID</dt><dd><ExternalLink href={bitcoinReference('tx', p.txid)!}>{p.txid}</ExternalLink><CopyAction compact label="Copy transaction ID" value={p.txid} /></dd></div><div><dt>Bitcoin block</dt><dd><ExternalLink href={bitcoinReference('block', p.bitcoin_block_hash)!}>{p.bitcoin_block_hash}</ExternalLink></dd></div><div><dt>Source-reported height</dt><dd>{record.reported_bitcoin_heights.length ? record.reported_bitcoin_heights.map(formatNumber).join(', ') : 'Not recorded'}{p.height_conflict ? ' · conflicting observations' : ''}{record.reported_heights_truncated ? ' · additional observations omitted' : ''}</dd></div><div><dt>Transaction output</dt><dd>{p.vout_index}</dd></div><div><dt>Published keystone</dt><dd>{p.keystone_height === null ? 'Unsupported payload' : formatNumber(p.keystone_height)}{p.keystone_id && <span className="hash-value">{p.keystone_id}</span>}</dd></div><div><dt>Record ID</dt><dd className="hash-value">{p.id}</dd></div></dl>
    {p.evidence_scope === 'publication_proof' && <p className="field-help">Publication proof only. Complete scanning of the containing Bitcoin block is not established by this record.</p>}
    <div className="publication-evidence-links"><h2>Hemi evidence</h2>{record.hemi_records.length ? <ul>{record.hemi_records.map(item => <li key={item.hash}><a href={withBrowseReturn(item.record_url, returnTo)}>Inspect Hemi keystone {formatNumber(item.height)} <span aria-hidden="true">→</span></a><span className="hash-value">{shortHash(item.hash)}</span></li>)}</ul> : <p>{p.state === 'unsupported' ? 'This payload is outside the supported format. No Hemi association is asserted.' : p.association_available && p.bitcoin_height === null ? 'Hemi metadata is retained, but a usable Hemi view cannot be linked until the reported Bitcoin height is unambiguous.' : 'Hemi ancestry and keystone metadata have not been associated with this publication yet.'}</p>}{record.hemi_records_truncated && <p className="field-help">Additional metadata associations exist beyond this record list.</p>}</div>
    <p className="field-help">Index view retrieved: {formatDate(record.retrieved_at)}. Current source views have not been rechecked. This URL identifies the same transaction output as the index grows; metadata availability may change.</p>
    <details className="limits-details"><summary>Supplied transaction and inclusion data</summary><p className="section-help">Retained bytes and Merkle siblings. This display alone does not establish current chain membership or a Hemi finality value.</p><pre className="record-json">{JSON.stringify(record.data, null, 2)}</pre></details>
  </section>;
}

export function ApiGuide() {
  const base = window.location.origin;
  return <section className="api-guide" aria-labelledby="api-heading">
    <p className="eyebrow">DEVELOPER ACCESS</p><h1 id="api-heading">Read API · v1</h1>
    <p className="section-help">Retrieve the same indexed records used by this explorer. Requests do not start collection or refresh source observations. The index response also reports recorded collection progress when available.</p>
    <div className="table-scroll"><table><thead><tr><th>GET endpoint</th><th>Returns</th></tr></thead><tbody>
      <tr><td><a href="/api/v1/index">/api/v1/index</a></td><td>Indexed counts, height bounds and qualifications</td></tr>
      <tr><td><a href="/api/v1/publications">/api/v1/publications</a></td><td>Paginated publication summaries</td></tr>
      <tr><td><a href="/api/v1/keystones">/api/v1/keystones</a></td><td>Paginated published-keystone summaries</td></tr>
      <tr><td><code>/api/v1/blocks/&#123;number-or-hash&#125;</code></td><td>Exact record link, JSON evidence export and recomputed checks</td></tr>
      <tr><td><code>/api/v1/publications/&#123;id&#125;</code></td><td>Retained transaction data and Hemi record links</td></tr>
    </tbody></table></div>
    <h2>Example: read indexed publications</h2><pre className="record-json">{`curl '${base}/api/v1/publications?limit=5'`}</pre>
    <p>List filters: <code>keystone_height</code>, <code>bitcoin_height</code>, and <code>state</code> (<code>all</code>, <code>associated</code>, <code>pending</code>, <code>unsupported</code>). Follow <code>page.next_cursor</code> with the same filters. Maximum page size: 50.</p>
    <h2>Evidence and source observations</h2>
    <p>Block responses include the JSON evidence export in <code>receipt</code>, its <code>verification</code> and an additive <code>finality</code> assessment. Supported named reference calculations use recorded Bitcoin tips; missing or conflicting inputs remain unavailable. A conditional Hemi-block model needs separately accepted exact contexts. These are recorded models, not deployed Hemi finality or current-chain certification. Record pages may gain evidence as the index changes.</p>
    <h2>Limits and errors</h2>
    <p>Responses are limited to 8 MB. A busy service returns HTTP 429; unavailable or oversized indexed work returns HTTP 503. Both include a retry hint. Invalid filters return HTTP 400; conflicting retained views return HTTP 409. Missing indexed evidence does not prove publication absence.</p>
    <details className="limits-details"><summary>Advanced service limits</summary><p>Reads share five processing slots. Decoded indexed input is capped at 32 MB and accounted SQL work at two million units. A five-second work budget is checked during SQL and proof processing. These are service safeguards, not a public performance guarantee.</p></details>
  </section>;
}
