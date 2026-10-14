import type { ReactNode } from 'react';

export function ExternalLink({ href, children, id, title = 'Open source reference in a new tab' }: { href: string; children: ReactNode; id?: string; title?: string }) {
  return <a id={id} href={href} target="_blank" rel="noopener noreferrer" className="external-link" title={title}>
    {children}<svg className="action-icon external-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M14 4h6v6m0-6L10 14" /><path d="M10 4H4v16h16v-6" /></svg>
  </a>;
}
