import { useEffect, useRef, useState } from 'react';
import type { Job, ReceiptWrapper } from './types';
import { isRecorded } from './recordedEvidence';

export interface OperationCompletion {
  id: number;
  origin: 'requested' | 'resumed';
  operation: Job['operation'];
  result?: ReceiptWrapper;
  failed: boolean;
}
interface Intent {
  origin: Element | null;
  moved: boolean;
  stop: () => void;
}

// Completion events belong to UI actions, never to receipt freshness or state polling.
export function useOperationFeedback(completion?: OperationCompletion) {
  const pending = useRef<Intent | undefined>(undefined);
  const handled = useRef<number | undefined>(undefined);
  const [message, setMessage] = useState('');
  const [announce, setAnnounce] = useState(true);
  const [deferredTarget, setDeferredTarget] = useState<string>();

  function clear() {
    pending.current?.stop();
    pending.current = undefined;
    setMessage('');
    setAnnounce(true);
    setDeferredTarget(undefined);
  }
  function begin() {
    if (pending.current) return false;
    clear();
    const intent: Intent = { origin: document.activeElement, moved: false, stop: () => {} };
    const interact = (event: Event) => {
      if (event.type === 'wheel' || event.type === 'touchstart') { intent.moved = true; return; }
      if (event.target === document.body || event.target === document.documentElement) {
        if (event.type === 'keydown') intent.moved = true;
        return;
      }
      if (event.target instanceof Node && !intent.origin?.contains(event.target)) intent.moved = true;
    };
    const leave = () => { intent.moved = true; };
    const visibility = () => { if (document.hidden) leave(); };
    for (const kind of ['focusin', 'pointerdown', 'keydown', 'wheel', 'touchstart']) {
      document.addEventListener(kind, interact, { capture: true, passive: true });
    }
    window.addEventListener('blur', leave);
    document.addEventListener('visibilitychange', visibility);
    intent.stop = () => {
      for (const kind of ['focusin', 'pointerdown', 'keydown', 'wheel', 'touchstart']) document.removeEventListener(kind, interact, true);
      window.removeEventListener('blur', leave);
      document.removeEventListener('visibilitychange', visibility);
    };
    pending.current = intent;
    return true;
  }

  useEffect(() => {
    if (!completion || handled.current === completion.id) return;
    handled.current = completion.id;
    const intent = pending.current;
    intent?.stop();
    pending.current = undefined;
    const section = document.getElementById('result');
    const heading = section?.dataset.receiptId === completion.result?.id ? document.getElementById('result-heading') : null;
    const target = completion.failed ? document.getElementById('error-message') : heading;
    // Existing errors already use role=alert; do not echo their text in a second live region.
    if (!completion.failed && completion.result && heading?.textContent) {
      const action = completion.operation === 'refresh' ? 'source recheck' : completion.operation === 'continue' ? 'search segment' : 'search';
      const name = completion.origin === 'resumed' ? `Resumed ${action}` : action[0].toUpperCase() + action.slice(1);
      const incomplete = completion.result.receipt.status === 'incomplete' ? ' with an incomplete result' : '';
      const storedFound = isRecorded(completion.result.receipt) && heading.textContent.trim() === 'Publication found';
      setMessage(`${name} finished${incomplete}. ${heading.textContent.trim()}${storedFound ? ' in stored evidence' : ''}.${completion.operation === 'refresh' ? ' Original scan coverage is unchanged.' : isRecorded(completion.result.receipt) && !storedFound ? ' Current source views were not rechecked.' : ''}`);
    } else setMessage('');
    const active = document.activeElement;
    const waiting = active === document.body || active === document.documentElement || active === intent?.origin;
    if (target && completion.origin === 'requested' && intent && !intent.moved && waiting && document.hasFocus() && !document.hidden) {
      target.focus();
      const focused = document.activeElement === target;
      setAnnounce(!focused); // Heading focus is the completion channel in this case.
      setDeferredTarget(focused ? undefined : target.id);
    } else { setAnnounce(true); setDeferredTarget(target?.id); }
  }, [completion]);

  useEffect(() => () => { pending.current?.stop(); pending.current = undefined; }, []);

  function view() {
    const target = deferredTarget && document.getElementById(deferredTarget);
    if (target) { target.focus(); if (document.activeElement === target) setDeferredTarget(undefined); }
  }
  return { message, announce, deferredTarget, begin, clear, view };
}
