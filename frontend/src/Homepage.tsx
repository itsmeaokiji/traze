import { ExternalLink } from './ExternalLink';
import { RecordedExample } from './RecordedExample';

export function HomeHero() {
  return <div className="home-hero home-inner" id="home-introduction">
    <p className="home-eyebrow">Hemi–Bitcoin publication explorer</p>
    <h1>Trace <span className="home-hemi-word">Hemi</span> blocks to <span className="home-bitcoin-word">Bitcoin</span> publications.</h1>
    <p className="home-subtitle">Start with a Hemi block number or hash. Follow the keystone connection, inspect Bitcoin transactions, and review the supporting evidence.</p>
    <p className="home-network">Hemi Mainnet <span aria-hidden="true">·</span> Bitcoin Mainnet</p>
  </div>;
}

const capabilities = [
  ['Inspect the publication', 'Follow the keystone connection and inspect the Bitcoin transaction containing the publication.'],
  ['Explore indexed records', 'Browse collected publications and keystones in table or timeline views.'],
  ['Share and verify', 'Copy an evidence summary or download a JSON export for offline verification.'],
];
const evidenceScope = [
  ['Proof checks', 'Checks publication data, inclusion in a Bitcoin block, and links between the supplied Hemi blocks.'],
  ['Source observations', 'Shows which sources were checked, when they were observed, and any agreement or conflicts recorded at that time.'],
  ['Coverage', 'Shows the Bitcoin blocks examined, along with gaps and unresolved work, so you can understand the scope behind the result.'],
];
const steps = [
  ['Enter the block', 'Paste a Hemi block number or full block hash.'],
  ['Follow the connection', 'Trace the block through its supporting keystone to an applicable Bitcoin publication.'],
  ['Review and share', 'Inspect the proof checks, source observations, and coverage. Copy a summary or export the evidence for offline verification.'],
];
export function HomeSections({ exampleAvailable, hosted, indexedOnly, onGoToSearch }: {
  exampleAvailable: boolean; hosted: boolean; indexedOnly: boolean; onGoToSearch: () => void;
}) {
  const faqs = [
    ['What can I search for?', 'A Hemi Mainnet block number or a full 0x-prefixed block hash.'],
    ['Why might a search return no publication?', 'Coverage may be incomplete, sources may be unavailable, or the search may have reached its limits. A missing match does not prove that no applicable publication exists.'],
    ['Does every search contact live sources?', indexedOnly || hosted
      ? 'This installation searches collected records only. It does not start live discovery.'
      : 'Traze reuses compatible stored evidence first. When needed and permitted, it can search public sources within the installation’s limits.'],
    ['Does opening stored evidence refresh its sources?', 'No. Source observations retain their recorded times. Where available, use Recheck sources for a new check without expanding the original search coverage.'],
    ['Does a match establish the first publication?', 'Not by itself. “Earliest observed” refers to the evidence in that result. Stronger first-publication assessments are shown separately with their conditions.'],
    ['Do I need to download files to use Traze?', 'No. You can search and inspect results in the app. Evidence downloads are optional for sharing or offline verification.'],
  ];
  return <div className="home-sections home-inner">
    <section className="home-connection" aria-labelledby="home-connection-title">
      <div className="home-section-heading"><div>
        <p className="home-eyebrow">Recorded example</p>
        <h2 id="home-connection-title">Follow the connection.</h2>
        <p>Explore how a Hemi block connects through a supporting keystone to a Bitcoin publication.</p>
      </div>{exampleAvailable
        ? <a className="home-text-action" href="/?receipt=example-ordinary">Inspect this example <span aria-hidden="true">→</span></a>
        : <ExternalLink href="https://github.com/itsmeaokiji/traze/blob/main/demo_results/mainnet_5243117.json">View recorded example</ExternalLink>}
      </div>
      <RecordedExample />
    </section>
    <section className="home-capabilities" aria-labelledby="home-capabilities-title">
      <div className="home-section-heading"><h2 id="home-capabilities-title">Inspect the publication.<br />Understand the evidence.</h2>
        <a className="home-text-action" href="/browse">Browse indexed records <span aria-hidden="true">→</span></a></div>
      <div className="home-capability-list">{capabilities.map(([title, description], index) => <div className="home-capability" key={title}>
        <span className="home-item-number" aria-hidden="true">0{index + 1}</span><h3>{title}</h3><p>{description}</p>
      </div>)}</div>
    </section>
    <section className="home-evidence" aria-labelledby="home-evidence-title">
      <h2 id="home-evidence-title">Know what the evidence supports.</h2>
      <div className="home-evidence-list">{evidenceScope.map(([title, description]) => <div key={title}>
        <h3>{title}</h3><p>{description}</p>
      </div>)}</div>
    </section>
    <section className="home-workflow" aria-labelledby="home-workflow-title">
      <p className="home-eyebrow">How it works</p><h2 id="home-workflow-title">From block to evidence.</h2>
      <p>Investigating a Hemi block from your logs or another tool? Start with its number or hash.</p>
      <ol className="home-steps">{steps.map(([title, description], index) => <li key={title}>
        <span className="home-item-number" aria-hidden="true">0{index + 1}</span><h3>{title}</h3><p>{description}</p>
      </li>)}</ol>
    </section>
    <section className="home-developers" aria-labelledby="home-developers-title">
      <div><p className="home-eyebrow">For developers</p><h2 id="home-developers-title">Work with the evidence.</h2>
        <p>Access indexed records through Traze’s versioned read API, or independently check exported evidence with the offline verifier.</p>
        <div className="home-developer-links"><a className="secondary" href="/api-docs">Read API docs <span aria-hidden="true">→</span></a>
          <ExternalLink href="https://github.com/itsmeaokiji/traze">View on GitHub</ExternalLink></div>
      </div>
      <aside className="home-availability" aria-label="Current availability"><h3>Current availability</h3>
        <p>{hosted ? 'This installation serves indexed evidence. Search availability depends on collected coverage.' : 'Traze currently runs locally.'}
          {!hosted && (indexedOnly ? ' This installation reads indexed records. Public-source discovery is disabled.' : ' Public-source discovery depends on provider availability and configured search limits.')}</p>
        <ExternalLink href="https://github.com/itsmeaokiji/traze/blob/main/docs/VERIFICATION.md">Read the verification scope</ExternalLink>
      </aside>
    </section>
    <section className="home-faq" aria-labelledby="home-faq-title">
      <h2 id="home-faq-title">Before you search.</h2>
      <div className="home-faq-list">{faqs.map(([question, answer]) => <details className="home-faq-item" key={question}>
        <summary>{question}</summary><p>{answer}</p>
      </details>)}</div>
    </section>
    <section className="home-final-action" aria-labelledby="home-final-title">
      <div><h2 id="home-final-title">Start with a Hemi block.</h2>
        <p>Enter a block number or hash to inspect its publication evidence.</p></div>
      <button type="button" className="primary" onClick={onGoToSearch}>Go to search</button>
    </section>
  </div>;
}
