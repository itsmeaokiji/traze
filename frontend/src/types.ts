// The Python bridge owns verification. These types describe its additive v1 data;
// legacy checkpoint field names remain compatible with existing evidence.
export type ResultStatus = 'found' | 'not_found_within_coverage' | 'incomplete';
export interface Issue { code?: string; message?: string }
export interface Observation { height?: number; same?: boolean | null; error?: string }
export interface Identity {
  status: 'matches_current_rpc_view' | 'different_current_rpc_block' | 'unavailable' | 'not_rechecked';
  current_hash?: string | null;
  reason?: string;
}
export interface Keystone { height: number; keystone_id: string }
export interface Proof {
  checkpoint: Keystone;
  bitcoin_height: number;
  bitcoin_block_hash: string;
  txid: string;
  ancestry: unknown[];
  current_bitcoin_depth_in_provider_view?: number | null;
}
export interface Coverage {
  fully_scanned_intervals?: [number, number][];
  blocks?: { height: number; hash: string; transaction_count: number; hemi_candidates: number; cache_hit?: boolean }[];
  plan?: { mode: string; start: number; initial_end: number };
  gaps?: Issue[];
  unresolved_candidates?: Issue[];
  unsupported_payloads?: number;
  stop_reason?: string | null;
}
export interface Resources {
  response_bytes_this_run?: number;
  bitcoin_body_attempts?: number;
  cache_body_reads?: number;
  http_requests?: number;
  elapsed_seconds?: number;
  deadline_expired?: boolean;
  limits?: { response_bytes: number; bitcoin_body_attempts: number; requests: number; overall_seconds?: number };
}
export interface RecoveryEvent {
  kind: string;
  source?: string;
  reason?: string;
  message?: string;
  revalidated_observations?: number;
}
export interface Recovery {
  hemi_rpc_used?: string;
  timestamp_recovery_used?: boolean;
  hemi_events?: RecoveryEvent[];
  timestamp_events?: RecoveryEvent[];
}
export interface Receipt {
  schema: string;
  status: ResultStatus;
  mode: string;
  catalogue?: { historical_source_times?: string[]; number_resolved_from_retained_view?: boolean };
  completed_at?: string;
  query: {
    block_number: number | null;
    input_type?: 'number' | 'hash';
    requested_block_hash?: string;
    block_hash?: string;
    header?: Record<string, unknown>;
    covering_checkpoint?: Keystone;
    covering_keystone?: number;
  };
  proofs: Proof[];
  selection?: { current_proof_index?: number | null; best_observed_proof_index?: number | null };
  coverage?: Coverage;
  context?: {
    checked_at?: string;
    current_view_confirmed?: boolean;
    distinct_bitcoin_api_crosscheck?: boolean;
    requested_block_identity?: Identity;
    hemi_headers?: Observation[];
    bitcoin_blocks?: Observation[];
    bitcoin_secondary?: Observation[];
    error?: string;
  };
  source_recovery?: Recovery;
  sources?: { hemi_rpc?: string; hemi_rpc_used?: string; bitcoin_apis?: string[] };
  issues?: Issue[];
  resources?: Resources;
  termination?: { reason: string };
  validation?: { scope?: string; live_public_reads?: boolean };
}
export interface ReceiptWrapper {
  id: string;
  label: string;
  filename: string;
  receipt: Receipt;
  verification: { proofs_checked: number; current_chain_membership_checked: boolean };
}
export interface SavedResult {
  id: string;
  label: string;
  origin: string;
  block_number: number | null;
  requested_block_hash?: string;
  completed_at?: string;
  status: ResultStatus;
  mode?: string;
}
export interface Job {
  id: string;
  operation: 'lookup' | 'refresh';
  block_input?: number | string;
  block_number: number | null;
  started_at: string;
  state: 'running' | 'complete' | 'error';
  events: { at: string; message: string }[];
  receipt_id: string | null;
  error?: Issue | null;
}
export interface WorkspaceState {
  csrf_token: string;
  receipts: SavedResult[];
  active_job: Job | null;
  resources: { available: boolean; total_response_bytes?: number; session_limit_bytes: number; remaining_bytes?: number };
  limits: { response_mb_per_lookup: number; bitcoin_body_attempts: number; ancestry_links: number; session_response_mb: number };
}
