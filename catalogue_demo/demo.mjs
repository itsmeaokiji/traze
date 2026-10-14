export function routeRecords(packet, index = 0) {
  const proof = packet.proofs[index];
  if (!proof) return [];
  const records = [
    {label: 'Requested Hemi block', value: packet.query.block_number, chain: 'hemi'},
    {label: 'Covering keystone', value: packet.query.covering_keystone, chain: 'hemi'},
  ];
  if (proof.checkpoint.height !== packet.query.covering_keystone) {
    records.push({label: 'Published later keystone', value: proof.checkpoint.height, chain: 'hemi'});
  }
  records.push({label: 'Bitcoin publication', value: proof.bitcoin_height ?? 'Height unresolved', chain: 'bitcoin'});
  return records;
}

export function resultCopy(packet) {
  if (packet.status === 'found') return 'Recorded publication found';
  if (packet.status === 'no_match') return 'No match in processed scope';
  return 'Evidence incomplete';
}

if (typeof document !== 'undefined') {
  const get = id => document.getElementById(id);
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  };
  let packet = null, generation = 0, abort = null;

  function reset() {
    generation += 1;
    abort?.abort();
    packet = null;
    get('evidence').hidden = true;
    get('download').hidden = true;
    get('operation').textContent = '';
    get('view').disabled = false;
  }

  function fact(label, value, link) {
    const row = element('div');
    row.append(element('dt', label));
    const dd = element('dd');
    if (link) {
      const anchor = element('a', value);
      anchor.href = link;
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
      dd.append(anchor);
    } else dd.textContent = String(value);
    row.append(dd);
    get('facts').append(row);
  }

  function renderSelection(index) {
    get('route').hidden = !packet.proofs.length;
    get('route').replaceChildren();
    get('facts').replaceChildren();
    for (const record of routeRecords(packet, index)) {
      const node = element('div', undefined, `route-node ${record.chain}`);
      const circle = element('div', undefined, 'circle');
      const mark = element('img');
      mark.src = `/assets/${record.chain}.svg`;
      mark.alt = '';
      circle.append(mark);
      node.append(circle, element('p', record.label, 'node-label'), element('p', typeof record.value === 'number' ? record.value.toLocaleString('en-US') : record.value, 'node-value'));
      get('route').append(node);
    }
    fact('Requested Hemi hash', packet.query.requested_block_hash);
    fact('Current source confirmation', 'Not rechecked');
    const proof = packet.proofs[index];
    if (proof) {
      fact('Bitcoin transaction ID', proof.txid, `https://blockstream.info/tx/${proof.txid}`);
      fact('Containing Bitcoin block hash', proof.bitcoin_block_hash, `https://blockstream.info/block/${proof.bitcoin_block_hash}`);
      fact('Supplied Hemi parent links checked', proof.ancestry.length - 1);
      fact('Proof checks', 'Passed against supplied headers');
    }
    const times = packet.coverage.historical_observations.map(source => source.observation.context?.checked_at ?? 'Observation time unavailable');
    fact('Historical source observation time', [...new Set(times)].join('; '));
  }

  function render() {
    get('evidence').hidden = false;
    get('evidence-title').textContent = resultCopy(packet);
    get('qualification').textContent = packet.proofs.length
      ? 'Proof checks passed against supplied data. Historical source observations retain their original scope; the requested target has not been freshly rechecked.'
      : packet.status === 'no_match'
        ? 'No applicable supported publication was found in the fully processed scope. This is a scan result, not a cryptographic proof of global absence.'
        : packet.issues[0]?.message ?? 'Required metadata or complete processing is unavailable. This result does not establish publication absence.';
    if (packet.proofs.length && !packet.coverage.target_association_complete) {
      get('qualification').textContent += ' Processing or target association is incomplete; see coverage details.';
    }
    if (packet.issues.some(issue => issue.code === 'historical_height_conflict')) {
      get('qualification').textContent += ' Historical sources report conflicting Bitcoin heights.';
    }
    const choice = get('publication');
    choice.replaceChildren(...packet.proofs.map((proof, index) => {
      const option = element('option', `Keystone ${proof.checkpoint.height.toLocaleString('en-US')} · Bitcoin ${proof.bitcoin_height ?? 'height unresolved'} · ${proof.txid.slice(0, 12)}…`);
      option.value = String(index);
      return option;
    }));
    get('publication-choice').hidden = packet.proofs.length < 2;
    get('download').hidden = false;
    const details = get('coverage-details');
    details.replaceChildren();
    const coverage = packet.coverage;
    details.append(element('p', `${coverage.blocks.length} retained Bitcoin blocks processed · ${(coverage.unresolved_candidates ?? []).length} unresolved target associations · ${coverage.unsupported_payloads ?? 0} unsupported-format records.`));
    for (const scope of coverage.scopes) {
      details.append(element('p', `Processed Bitcoin scope: ${scope.fully_scanned_intervals.map(([start, end]) => `${start.toLocaleString('en-US')}–${end.toLocaleString('en-US')}`).join(', ') || 'No complete bodies'}. Heights are source-reported.`));
    }
    if (coverage.gaps.length) details.append(element('p', `${coverage.gaps.length} nominated bodies are incomplete or unavailable.`));
    const limits = element('ul');
    for (const limit of packet.offline_recomputation.limits) limits.append(element('li', limit));
    details.append(limits);
    renderSelection(0);
  }

  get('target').addEventListener('input', () => { reset(); get('block-title').textContent = 'Exact Hemi hash request'; });
  get('example').addEventListener('change', () => {
    reset();
    get('target').value = get('example').value;
    get('block-title').textContent = get('example').selectedOptions[0].textContent;
  });
  get('publication').addEventListener('change', () => renderSelection(Number(get('publication').value)));
  get('view').addEventListener('click', async () => {
    reset();
    const current = generation;
    const hash = get('target').value.trim();
    if (!/^0x[0-9a-f]{64}$/i.test(hash)) {
      get('operation').textContent = 'Enter a full 0x-prefixed Hemi block hash.';
      get('target').focus();
      return;
    }
    abort = new AbortController();
    get('view').disabled = true;
    get('operation').textContent = 'Reading compiled evidence…';
    try {
      const response = await fetch(`/api/evidence?hash=${encodeURIComponent(hash)}`, {signal: abort.signal});
      const result = await response.json();
      if (current !== generation) return;
      if (!response.ok) throw new Error(result.message ?? 'Compiled evidence is unavailable.');
      packet = result;
      get('operation').textContent = 'Compiled evidence loaded. No public-chain request was made.';
      render();
    } catch (error) {
      if (current === generation && error.name !== 'AbortError') get('operation').textContent = error.message;
    } finally {
      if (current === generation) get('view').disabled = false;
    }
  });
  get('download').addEventListener('click', () => {
    if (!packet) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(packet, null, 2) + '\n'], {type: 'application/json'}));
    const link = element('a');
    link.href = url;
    link.download = `traze-retained-${packet.query.block_number ?? 'unresolved'}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  fetch('/api/catalogue').then(async response => {
    if (!response.ok) throw new Error('Retained catalogue is unavailable.');
    const result = await response.json();
    const options = result.examples.map(example => {
      const option = element('option', `Hemi block ${example.number.toLocaleString('en-US')}`);
      option.value = example.hash;
      return option;
    });
    get('example').replaceChildren(...options);
    get('example').disabled = false;
    get('target').disabled = false;
    get('target').value = result.examples[0].hash;
    get('block-title').textContent = options[0].textContent;
    get('view').disabled = false;
    get('scope-note').textContent = `${result.summary.processed_bodies} retained Bitcoin blocks · ${result.summary.supported_publications} supported publication records. Coverage is limited to the saved periods.`;
  }).catch(error => { get('scope-note').textContent = error.message; });
}
