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
