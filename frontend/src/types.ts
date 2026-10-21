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
  publication_id?: string;
  checkpoint: Keystone;
  bitcoin_height: number;
  bitcoin_block_hash: string;
  txid: string;
  tx_index?: number;
  vout_index?: number;
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
  record_url?: string;
  selected_publication?: string | null;
  selected_publication_available?: boolean;
  receipt: Receipt;
  verification: { proofs_checked: number; current_chain_membership_checked: boolean };
  first_publication?: FirstPublicationAssessment;
}

export interface FirstPublicationAssessment {
  status: string;
  query: { number: number | null; hash?: string };
  publication?: { bitcoin_height: number; bitcoin_hash: string; keystone_number: number; txid: string; tx_index: number; vout: number } | null;
  packet_available: boolean;
  packet_digest?: string;
  download_url?: string;
  globally_first_proven: false;
  policy_id: string;
  accepted_by_operator: boolean;
  assumptions: { id: string; description: string }[];
  issues: Issue[];
  source_context?: { packet_prepared_at?: string; recorded_at?: string; source_checked_at?: string };
  result?: {
    schema?: string;
    status: string;
    first_height_assessment?: { status: string; bitcoin_height: number | null; policy_id?: string; context_sha256?: string };
    assurance?: { first_height_under_accepted_assumptions?: boolean; globally_first_proven?: boolean };
    policy?: { id: string; accepted_by_caller: boolean; accepted_from_packet: boolean;
      context_accepted_by_caller?: boolean; context_sha256_selected_by_caller?: string | null };
    context_evidence?: {
      status: string; sha256?: string;
      snapshot?: { number: number; hash: string; timestamp: number };
      domain?: { first_hemi_keystone: number; last_hemi_block: number; tip_hash: string };
      source?: string; observed_from?: string; observed_through?: string;
      historical_execution_certified?: boolean; current_bitcoin_membership_verified?: boolean;
    };
    interval_evidence?: {
      status: string;
      query: { number: number; hash: string };
      publication: { bitcoin_height: number; bitcoin_hash: string; keystone_number: number; txid: string; tx_index: number; vout: number };
      verified_interval: { first_height: number; last_height: number };
      assurance: { first_in_checked_interval: boolean };
    };
  };
}

export interface IndexSummary {
  index_kind: 'maintained' | 'catalogue';
  counts: { processed_bodies: number; supported_publications: number; associated_publications: number; unassociated_publications: number; unsupported_publications: number; failed_or_pending_bodies: number };
  bitcoin_height_bounds: [number, number] | null;
  source_observations_current: false;
  history_complete: false;
  last_import_stored_at: string | null;
}
export interface PublicationSummary {
  id: string; record_url: string; bitcoin_block_hash: string; bitcoin_height: number | null;
  height_conflict: boolean; txid: string; vout_index: number;
  keystone_height: number | null; keystone_id: string | null;
  state: 'associated' | 'pending' | 'unsupported'; association_available: boolean;
}
export interface KeystoneSummary {
  height: number; keystone_id: string; publication_count: number; associated_count: number; publications_url: string;
}
export interface IndexPage<T> {
  items: T[]; page: { limit: number; next_cursor: string | null }; retrieved_at: string;
}
export interface PublicationDetail {
  publication: PublicationSummary; retrieved_at: string;
  reported_bitcoin_heights: number[]; reported_heights_truncated: boolean;
  hemi_records: { height: number; hash: string; record_url: string }[]; hemi_records_truncated: boolean;
  data: { raw_transaction_hex: string; bitcoin_header_hex: string; tx_index: number; vout_index: number; merkle_siblings_display_hex: string[] };
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
  phase?: 'recorded_lookup' | 'public_discovery' | 'source_refresh' | 'first_publication_check';
  block_input?: number | string;
  block_number: number | null;
  started_at: string;
  state: 'running' | 'complete' | 'error';
  events: { at: string; message: string }[];
  receipt_id: string | null;
  error?: Issue | null;
}
export interface WorkspaceState {
  deployment?: 'hosted-candidate';
  service_status?: { monitoring: 'available' | 'unavailable'; generated_at?: string | null;
    observations: Partial<Record<'bitcoin' | 'hemi', { observed_at?: string | null; recency: 'recent' | 'stale' | 'unavailable'; age_seconds?: number | null }>> };
  serving_mode?: 'indexed-only' | 'discovery';
  collection?: CollectionStatus | null;
  csrf_token: string;
  receipts: SavedResult[];
  active_job: Job | null;
  resources: { available: boolean; total_response_bytes?: number; session_limit_bytes: number; remaining_bytes?: number };
  limits: { response_mb_per_lookup: number; bitcoin_body_attempts: number; ancestry_links: number; session_response_mb: number };
}
export interface CollectionStatus {
  state: string;
  kind?: 'job' | 'service';
  next_height?: number;
  recorded_state?: string;
  runner?: { state: 'active' | 'inactive' | 'unavailable'; last_seen_at?: string | null };
  stop?: { reason: string; requested_at: string; acknowledged_at?: string } | null;
  range?: [number, number] | null;
  processed_blocks?: number;
  processed_job_blocks?: number;
  requested_job_blocks?: number;
  supported_publications?: number;
  associated_publications?: number;
  unresolved_publications?: number;
  pending_ancestry?: number;
  source_conflicts?: number;
  updated_at?: string;
  issue?: Issue | null;
}
