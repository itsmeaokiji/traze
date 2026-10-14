import { useEffect, useId, useRef, useState } from 'react';

type Props = {
  value: string; label: string; scope?: string; id?: string; compact?: boolean;
  disabled?: boolean; className?: string; onManualCopy?: () => void; onError?: (error: Error) => void;
};

export function CopyAction(props: Props) {
  // New evidence/view values get a new control; pending old writes cannot label them copied.
  return <CopyControl key={`${props.scope || ''}:${props.value}`} {...props} />;
}

function CopyControl({ value, label, id, compact = false, disabled = false, className, onManualCopy, onError }: Props) {
  const [phase, setPhase] = useState<'idle' | 'pending' | 'copied' | 'failed'>('idle');
  const alive = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const writing = useRef(false);
  const manualId = useId();
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; clearTimeout(timer.current); };
  }, []);

  async function copy() {
    if (writing.current || disabled) return;
    writing.current = true;
    clearTimeout(timer.current);
    setPhase('pending');
    try {
      await navigator.clipboard.writeText(value);
      if (!alive.current) return;
      setPhase('copied');
      timer.current = setTimeout(() => { if (alive.current) setPhase('idle'); }, 3000);
    } catch {
      if (!alive.current) return;
      setPhase('failed');
      onError?.(new Error('Could not copy automatically. Use the manual copy option beside the action.'));
    } finally { writing.current = false; }
  }
  const name = phase === 'copied' ? 'Copied' : phase === 'pending' ? 'Copying…' : compact ? 'Copy' : label;
  return <div className={`copy-action${compact ? ' copy-action-compact' : ''}${phase === 'failed' ? ' has-fallback' : ''}`}>
    <button type="button" id={id} className={className || (compact ? 'identifier-copy' : 'copy-button')} disabled={disabled || phase === 'pending'} onClick={() => void copy()}
      aria-label={compact ? phase === 'copied' ? `${label.replace(/^Copy /, '')} copied` : label : undefined} title={phase === 'copied' ? 'Copied to clipboard' : `${label} · Copies the full value`}>
      <svg className="action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
        {phase === 'copied' ? <path d="m5 12 4 4 10-10" /> : <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M15 8V4H4v11h4" /></>}
      </svg><span>{name}</span>
    </button>
    <span role="status" aria-live="polite" className="sr-only">{phase === 'copied' ? `${label.replace(/^Copy /, '')} copied to clipboard.` : ''}</span>
    {phase === 'failed' && <div className="copy-fallback">
      <p role="alert">Automatic copy is unavailable. Copy the text manually.</p>
      {onManualCopy ? <button type="button" className="manual-copy-button" onClick={onManualCopy}>Open summary for manual copy</button>
        : <><label htmlFor={manualId} className="sr-only">Manual copy: {label.replace(/^Copy /, '')}</label><textarea id={manualId} readOnly value={value} rows={3} spellCheck={false} onFocus={event => event.currentTarget.select()} /></>}
    </div>}
  </div>;
}
