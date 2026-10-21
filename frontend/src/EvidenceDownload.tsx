import { useEffect, useState } from 'react';
import type { ReceiptWrapper } from './types';

export function EvidenceDownload({ wrapper }: { wrapper: ReceiptWrapper }) {
  const [url, setUrl] = useState<string>();
  useEffect(() => {
    if (!wrapper.record_url || typeof URL.createObjectURL !== 'function') return;
    // Export exactly the displayed snapshot, not a later regenerated API view.
    const next = URL.createObjectURL(new Blob([JSON.stringify(wrapper.receipt, null, 2)], { type: 'application/json' }));
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [wrapper]);
  const href = wrapper.record_url ? url : `/api/receipts/${encodeURIComponent(wrapper.id)}/download`;
  return <a id="download-link" className="download-link" href={href} aria-disabled={!href || undefined} download={wrapper.filename}>Download JSON <span aria-hidden="true">↓</span></a>;
}
