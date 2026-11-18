export type DisclosureType = 'dividend' | 'financial_report' | 'material_event';

export interface TraceStep {
  step_index: number;
  step_type: 'tool_call' | 'tool_result' | 'sql_retry' | 'guardrail' | 'citation_gate';
  tool_name: string | null;
  input_summary: string;
  output_summary: string;
  is_error: boolean;
  duration_ms: number;
}

export interface ChartSpec {
  type: 'bar' | 'line';
  title: string;
  x_key: string;
  y_key: string;
}

export interface ResultArtifact {
  columns: string[];
  rows: Record<string, unknown>[];
  sql_query?: string | null;
  chart_spec?: ChartSpec | null;
}

export interface CitedDisclosure {
  disclosure_id: string;
  ticker: string;
  company_name: string;
  disclosure_type: DisclosureType;
  published_at: string;
  title: string;
  source_url: string;
  extraction_confidence: number;
  low_confidence_fields: string[];
}

export interface DisclosureSummary {
  disclosure_id: string;
  ticker: string;
  company_name: string;
  disclosure_type: DisclosureType;
  published_at: string;
  source_url: string;
  title: string;
  summary_tr: string;
  extraction_confidence: number;
  low_confidence_fields: string[];
  human_verified: boolean;
}

export interface DisclosureDetail extends DisclosureSummary {
  extracted_data: Record<string, unknown>;
  raw_text_excerpt: string;
}

export interface RemainingQuota {
  remaining_ip_minute: number;
  remaining_ip_day: number;
  remaining_global_day: number;
}

export interface HealthStatus {
  status: string;
  disclosures_count: number;
  low_confidence_count: number;
  pending_reviews_count: number;
  has_live_gemini: boolean;
  is_offline_fallback: boolean;
  provider_model: string;
  execution_mode: string;
  demo_questions?: string[];
  remaining_quota?: RemainingQuota;
}

export interface ChatDoneMeta {
  mode: 'live_llm' | 'offline_fallback' | 'guardrail_refusal' | 'cached_demo';
  provider_model: string;
  is_offline_fallback: boolean;
  quota_exhausted?: boolean;
  cached_hit?: boolean;
  remaining_quota?: RemainingQuota;
  sql_attempts: number;
  sql_retries: number;
  first_try_sql_success: boolean | null;
  refused_as_advice: boolean;
  no_evidence_found: boolean;
  total_duration_ms: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  trace?: TraceStep[];
  artifact?: ResultArtifact | null;
  citations?: CitedDisclosure[];
  meta?: ChatDoneMeta;
}

export interface ReviewFlagItem {
  flag_id: number;
  disclosure_id: string;
  ticker: string;
  disclosure_type: DisclosureType;
  title: string;
  source_url: string;
  field_name: string;
  current_value: string | null;
  suggested_value: string;
  reason_note: string | null;
  status: 'pending' | 'approved' | 'rejected';
  reviewer_note: string | null;
  created_at: string;
  reviewed_at: string | null;
}
