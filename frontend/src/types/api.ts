export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed'

export type OutputLanguage = 'zh' | 'en'

export interface TaskCreateResponse {
  task_id: string
  status: TaskStatus
  message: string
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
  metadata: Record<string, unknown>
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
