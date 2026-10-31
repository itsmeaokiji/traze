import { useEffect, useRef, useState } from 'react';
import { ChainMark } from './ChainMark';
import { formatNumber } from './format';
import { RecordInspector } from './RecordInspector';
import type { RouteRole } from './PublicationRoute';
import type { Proof, Receipt } from './types';

type Example = { receipt: Receipt; covering: Proof; later?: Proof };
let exampleRequest: Promise<Example> | undefined;

function loadExample(): Promise<Example> {
  return exampleRequest ??= import('../../demo_results/mainnet_5243117.json').then(({ default: data }) => {
    const receipt = data as unknown as Receipt;
    const covering = receipt.proofs.find(proof => proof.checkpoint.height === receipt.query.covering_keystone);
    if (receipt.query.block_number !== 5243117 || receipt.query.covering_keystone !== 5243125 || !covering || covering.bitcoin_height !== 965931) {
      throw new Error('The recorded example is unavailable.');
    }
    const later = receipt.proofs.filter(proof => proof.checkpoint.height > receipt.query.covering_keystone!)
      .sort((a, b) => a.checkpoint.height - b.checkpoint.height)[0];
    return { receipt, covering, later };
  }).catch(error => { exampleRequest = undefined; throw error; });
}

export function RecordedExample() {
  const [example, setExample] = useState<Example>();
  const [role, setRole] = useState<RouteRole>();
  const [later, setLater] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const mounted = useRef(true);
  const buttons = useRef<Partial<Record<RouteRole, HTMLButtonElement | null>>>({});
  const proof = example && (later ? example.later : example.covering);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (role && example) document.getElementById('record-inspector')?.focus();
  }, [role, example, later]);

  async function ensureExample() {
    if (example) return example;
    setLoading(true); setError(false);
    try {
      const next = await loadExample();
      if (!mounted.current) return;
      setExample(next);
      return next;
    } catch { if (mounted.current) setError(true); }
    finally { if (mounted.current) setLoading(false); }
  }
  function inspect(next: RouteRole) { setRole(next); void ensureExample(); }
  function close() { setRole(undefined); if (role) buttons.current[role]?.focus(); }
  async function changeSupport() {
    const next = await ensureExample();
    if (!next || !mounted.current || !next.later) return;
    setLater(!later); setRole(later ? 'covering' : 'later');
  }
  const records: { role: RouteRole; chain: 'hemi' | 'bitcoin'; label: string; value: number; inspect: string }[] = [
    { role: 'requested', chain: 'hemi', label: 'Hemi block', value: 5243117, inspect: 'Hemi block' },
    { role: 'covering', chain: 'hemi', label: 'Covering keystone', value: 5243125, inspect: 'Covering keystone' },
  ];
  if (later && proof) records.push({ role: 'later', chain: 'hemi', label: 'Later keystone', value: proof.checkpoint.height, inspect: 'Later keystone' });
  records.push({ role: 'publication', chain: 'bitcoin', label: 'Containing Bitcoin block', value: proof?.bitcoin_height ?? 965931, inspect: 'Bitcoin publication' });

  return <>
    <ol className={`home-route${later ? ' has-later-keystone' : ''}`} aria-label="Recorded Hemi to Bitcoin example">
      {records.map(record => <li key={record.role} className={`home-route-record home-chain-${record.chain}${role === record.role ? ' is-selected' : ''}`}>
        <div className="home-route-track"><button type="button" className="home-route-node"
          ref={button => { buttons.current[record.role] = button; }}
          aria-label={`Inspect recorded ${record.inspect}`} aria-pressed={role === record.role}
          aria-expanded={role === record.role} aria-controls="home-example-details" onClick={() => inspect(record.role)}>
          <ChainMark chain={record.chain} />
        </button></div>
        <span className="home-route-label">{record.label}</span><strong>{formatNumber(record.value)}</strong>
      </li>)}
    </ol>
    <p className="home-caption">Recorded publication evidence. Current source views have not been rechecked.</p>
    <div className="home-example-actions"><span>Select a node to inspect its recorded details.</span>
      {(!example || example.later) && <button type="button" className="text-button" disabled={loading} onClick={() => void changeSupport()}>
        {later ? 'Show covering-keystone publication' : 'Show later-keystone support'}
      </button>}
    </div>
    {error && <p role="alert" className="field-help">Recorded details could not be loaded. Try selecting a node again, or open the full example.</p>}
    {loading && <p role="status" className="field-help">Loading recorded example…</p>}
    <div id="home-example-details" className="home-example-details" hidden={!role}
      onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); close(); } }}>
      {role && <><div className="home-example-detail-actions"><button type="button" className="text-button" onClick={close}>Close details <span aria-hidden="true">×</span></button></div>
        {example && <RecordInspector receipt={example.receipt} proof={proof} role={role} historical />}
      </>}
    </div>
  </>;
}
