import { useState } from 'react';
import type { ReactNode } from 'react';
import { PublicationRoute } from './PublicationRoute';
import type { RouteRole } from './PublicationRoute';
import { RecordInspector } from './RecordInspector';
import type { Proof, Receipt } from './types';

export function RecordWorkspace({ receipt, proof, children }: { receipt: Receipt; proof?: Proof; children?: ReactNode }) {
  const defaultRole: RouteRole = proof ? 'publication' : 'requested';
  const [selectedRole, setSelectedRole] = useState<RouteRole>(defaultRole);
  const laterAvailable = Boolean(proof && receipt.query.covering_keystone !== undefined && proof.checkpoint.height > receipt.query.covering_keystone);
  const role = selectedRole === 'later' && !laterAvailable ? defaultRole : selectedRole;

  return <div className="record-workspace"><div className="record-workspace-grid">
    <div className="route-column"><PublicationRoute receipt={receipt} proof={proof} selectedRole={role} onSelect={setSelectedRole} /><a className="inspector-jump" href="#record-inspector">View selected record details <span aria-hidden="true">↓</span></a>{children}</div>
    <RecordInspector receipt={receipt} proof={proof} role={role} />
  </div></div>;
}
