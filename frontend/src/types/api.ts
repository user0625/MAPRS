export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'canceled' | 'interrupted'

export type OutputLanguage = 'zh' | 'en'
export type AnalysisDepth = 'quick' | 'standard' | 'deep'
export type TargetAudience = 'general' | 'researcher' | 'reviewer'
export type ReportTemplate = 'standard' | 'review' | 'reproducibility'

export interface ReportConfiguration {
  analysis_depth: AnalysisDepth
  target_audience: TargetAudience
  report_template: ReportTemplate
  custom_sections: string[]
}

export interface TaskCreateResponse {
  task_id: string
  status: TaskStatus
  message: string
  deduplicated?: boolean
}

export interface TaskStatusResponse {
  task_id: string
  status: TaskStatus
  message: string
  created_at: string
  updated_at: string
  completed_at: string | null
  paper_title: string | null
  paper_id: string | null
  report_path: string | null
  state_json_path: string | null
  error_message: string | null
  progress: number
  current_step: string | null
  attempt_count: number
  last_checkpoint_step: string | null
  last_event_id: number
  metadata: Record<string, unknown>
}

export interface TaskEvent {
  id: number
  type: string
  status: TaskStatus | null
  step: string | null
  message: string | null
  metadata: Record<string, unknown>
  created_at: string
}

export interface EvidenceItem {
  task_id: string; evidence_id: string; chunk_id: string | null
  page_start: number | null; page_end: number | null; section: string | null; text: string
}

export interface ReportClaim { text: string; evidence_ids: string[] }
export interface StructuredReportSection {
  title: string; content: string; order: number; evidence_ids: string[]; claims: ReportClaim[]
}
export interface StructuredReportResponse {
  task_id: string
  report: { title: string; paper_title: string | null; sections: StructuredReportSection[]; quality_summary: Record<string, unknown> | null }
  quality_summary: Record<string, unknown>
  evidence_index: Array<Omit<EvidenceItem, 'task_id' | 'text'>>
}

export interface TaskReportResponse {
  task_id: string
  status: 'completed'
  report_markdown: string
  report_path: string | null
}

export interface TaskListResponse {
  items: TaskStatusResponse[]
  total: number
  limit: number
  offset: number
}

export interface WorkflowStepSummary {
  step_name: string
  status: string
  timestamp: string | null
  message: string | null
  metadata: Record<string, unknown>
}

export interface TaskDetailResponse extends TaskStatusResponse {
  paper_authors: string[]
  report_markdown: string | null
  report_available: boolean
  state_available: boolean
  workflow_status: string | null
  workflow_created_at: string | null
  workflow_updated_at: string | null
  workflow_completed_at: string | null
  workflow_metadata: Record<string, unknown>
  step_history: WorkflowStepSummary[] | null
}

export type AskLanguage = 'auto' | 'zh' | 'en'
export interface Conversation { id:string; task_id:string; title:string; language:AskLanguage; created_at:string; updated_at:string }
export interface AskMessage { id:string; conversation_id:string; role:'user'|'assistant'; content:string; status:'completed'|'generating'|'failed'|'canceled'; language:AskLanguage; section:string|null; page_start:number|null; page_end:number|null; citation_ids:string[]; error:string|null; retry_of:string|null; created_at:string; updated_at:string }
export interface ConversationDetail extends Conversation { messages:AskMessage[]; total:number; limit:number; offset:number }
export interface AskAccepted { user_message_id:string|null; assistant_message_id:string; status:string }

export type DocumentSearchMode = 'auto' | 'bm25'
export type DocumentSearchModeUsed = 'hybrid' | 'bm25' | 'degraded_to_bm25'
export type DocumentSearchIndexSource = 'memory_hit' | 'disk_hit' | 'cold_build' | 'unavailable'
export interface DocumentSearchRequest {
  query: string
  mode: DocumentSearchMode
  section?: string | null
  page_start?: number | null
  page_end?: number | null
  top_k: number
}
export interface DocumentSearchContext {
  relation: 'before' | 'after'
  chunk_id: string
  text: string
  section: string | null
  page_start: number | null
  page_end: number | null
}
export interface DocumentSearchHit {
  rank: number
  chunk_id: string
  text: string
  section: string | null
  page_start: number | null
  page_end: number | null
  sources: Array<'bm25' | 'vector'>
  bm25_score: number | null
  vector_score: number | null
  hybrid_score: number | null
  context: DocumentSearchContext[]
}
export interface DocumentSearchResponse {
  task_id: string
  query: string
  mode_used: DocumentSearchModeUsed
  hits: DocumentSearchHit[]
  diagnostics: {
    actual_mode: DocumentSearchModeUsed
    candidate_count: number
    elapsed_ms: number
    index_source: DocumentSearchIndexSource
    fallback_reason: 'index_build_unavailable' | 'query_embedding_unavailable' | null
  }
}

export type ComparisonStatus = 'pending' | 'running' | 'completed' | 'failed' | 'canceled'
export interface ComparisonPaper { source_task_id:string; paper_id:string|null; title:string; authors:string[]; year:number|null; position:number }
export interface ComparisonResponse {
  id:string; title:string; focus:string; language:OutputLanguage; status:ComparisonStatus
  progress:number; current_step:string|null; message:string; error_message:string|null
  retry_of:string|null; report_available:boolean; structured_available:boolean
  artifact_formats:string[]; last_event_id:number; created_at:string; updated_at:string
  completed_at:string|null; papers:ComparisonPaper[]
}
export interface ComparisonListResponse { items:ComparisonResponse[]; total:number; limit:number; offset:number }
export interface ComparisonCell { source_task_id:string; summary:string; evidence_ids:string[] }
export interface ComparisonMatrixRow { dimension:string; cells:ComparisonCell[] }
export interface ComparisonStructuredReport {
  schema_version:'paper-comparison-v1'; comparison_id:string; title:string; focus:string; language:OutputLanguage
  source_papers:Array<Omit<ComparisonPaper,'position'>>
  profiles:Array<Record<string,unknown>>; matrix:ComparisonMatrixRow[]
  synthesis:Record<string,{content:string;evidence_ids:string[]}>
  claims:Array<{text:string;evidence_ids:string[]}>; evidence_ids:string[]; quality_warnings:string[]
}
export interface ComparisonEvidence {
  comparison_id:string; evidence_id:string; source_task_id:string; paper_id:string|null; paper_title:string
  chunk_id:string; page_start:number|null; page_end:number|null; section:string|null; text:string; score:number|null
}

export interface EvaluationMetrics {
  candidate_recall_at_20:number; recall_at_6:number; precision_at_6:number; mrr:number
  evidence_coverage:number; evidence_f1:number; unanswerable_refusal_rate:number
  answerable_false_refusal_rate:number; answer_token_f1:number; citation_validity_rate:number
  evidence_support_rate:number; latency_p50_ms:number; latency_p95_ms:number; estimated_cost_usd:number
  degradation_rate?:number
  answerability_threshold_refusal_rate?:number; evidence_filter_empty_rate?:number
  unanswerable_answerability_refusal_rate?:number; unanswerable_evidence_empty_refusal_rate?:number
  answerable_answerability_false_refusal_rate?:number; answerable_evidence_empty_false_refusal_rate?:number
}
export interface EvaluationScenario {
  scenario:string; effective_modes:string[]; case_count:number; metrics:EvaluationMetrics
  answer_quality_by_type:Record<string,{count:number;token_f1:number}>
  degraded_reasons:string[]; failure_case_ids:string[]
}
export interface EvaluationReport {
  schema_version:'public-paper-benchmark-v1'|'public-paper-benchmark-v2'; benchmark:string; result_status:string
  dataset_adapter_version:string; dataset_version?:string; source_sha256?:string; adapted_sha256?:string
  split:'train'|'validation'|'test'; generated_at:string
  scope:{evaluates:string[];does_not_evaluate:string[];answer_baseline:string}
  paper_count:number; dataset_paper_count?:number; evaluated_paper_count?:number
  case_count:number; exclusions:Record<string,number>
  configuration:Record<string,string|number|null>; scenarios:EvaluationScenario[]
  run_level?:'pilot'|'validation'; run_version?:string
  quality_gate?:{passed:boolean;failures:string[]}
  quality_gates?:Record<'retrieval'|'reranker'|'refusal',{name:string;passed:boolean;failures:string[]}>
  validation_authorized?:boolean; validation_scope?:'retrieval_only'|null
  production_recommendation?:{
    embedding:'candidate_default'|'candidate_for_validation'|'keep_current';reranker:'shadow'|'disabled'
    shadow_exit?:{minimum_requests:number;minimum_days:number}
  }
  request_counts?:{embedding_batches:number;rerank_requests:number}
}
