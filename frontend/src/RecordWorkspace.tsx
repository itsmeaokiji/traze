import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { PublicationRoute } from './PublicationRoute';
import type { RouteRole } from './PublicationRoute';
import { RecordInspector } from './RecordInspector';
import type { Proof, Receipt } from './types';

export function RecordWorkspace({ receipt, proof, children }: { receipt: Receipt; proof?: Proof; children?: ReactNode }) {
  const defaultRole: RouteRole = proof ? 'publication' : 'requested';
  const [selectedRole, setSelectedRole] = useState<RouteRole>(defaultRole);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const laterAvailable = Boolean(proof && receipt.query.covering_keystone !== undefined && proof.checkpoint.height > receipt.query.covering_keystone);
  const role = selectedRole === 'later' && !laterAvailable ? defaultRole : selectedRole;
  function inspect(next: RouteRole) { setSelectedRole(next); setInspectorOpen(true); }
  useEffect(() => {
    if (inspectorOpen) document.getElementById('record-inspector')?.focus({ preventScroll: true });
  }, [inspectorOpen, role]);

  return <div className="record-workspace"><div className={`record-workspace-grid${inspectorOpen ? ' has-inspector' : ''}`}>
    <div className="route-column"><PublicationRoute receipt={receipt} proof={proof} selectedRole={inspectorOpen ? role : undefined} onSelect={inspect} />{children}</div>
    <details className="record-inspection limits-details" open={inspectorOpen} onToggle={event => setInspectorOpen(event.currentTarget.open)}>
      <summary>Inspect record details</summary>
      <div hidden={!inspectorOpen}><RecordInspector receipt={receipt} proof={proof} role={role} /></div>
    </details>
  </div></div>;
}
