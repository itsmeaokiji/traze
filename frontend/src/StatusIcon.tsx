export type StatusTone = 'pass' | 'neutral' | 'warning' | 'error';

export function StatusIcon({ tone }: { tone: StatusTone }) {
  return <svg className={`status-icon status-${tone}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <circle cx="12" cy="12" r="9" />
    {tone === 'pass' ? <path d="m7.5 12 3 3 6-6" />
      : tone === 'warning' ? <><path d="M12 7.5v5" /><circle cx="12" cy="16" r=".8" fill="currentColor" stroke="none" /></>
        : tone === 'error' ? <path d="m9 9 6 6m0-6-6 6" /> : <path d="M8 12h8" />}
  </svg>;
}
