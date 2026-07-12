import type {
  OutputLanguage,
  TaskCreateResponse,
  TaskReportResponse,
  TaskStatusResponse,
  TaskDetailResponse,
  TaskListResponse,
  ReportConfiguration,
  EvidenceItem,
  StructuredReportResponse,
} from '../types/api'

const DEFAULT_API_BASE_URL = ''
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL).replace(/\/$/, '')

interface ApiErrorBody {
  detail?: string
}

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init)

  if (!response.ok) {
    let message = `Request failed (${response.status})`

    try {
      const body = (await response.json()) as ApiErrorBody
      if (typeof body.detail === 'string') {
        message = body.detail
      }
    } catch {
      // Keep the HTTP status fallback when the response is not JSON.
    }

    throw new Error(message)
  }

  return (await response.json()) as T
}

export function createAnalysisTask(
  file: File,
  query: string,
  language: OutputLanguage,
  configuration: ReportConfiguration,
): Promise<TaskCreateResponse> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('query', query)
  formData.append('language', language)
  formData.append('analysis_depth', configuration.analysis_depth)
  formData.append('target_audience', configuration.target_audience)
  formData.append('report_template', configuration.report_template)
  formData.append('custom_sections', JSON.stringify(configuration.custom_sections))

  return requestJson<TaskCreateResponse>(`${API_BASE_URL}/api/tasks/analyze`, {
    method: 'POST',
    body: formData,
  })
}

export function artifactUrl(taskId: string, format: 'markdown' | 'json' | 'html' | 'pdf' | 'docx'): string {
  return `${API_BASE_URL}/api/tasks/${taskId}/artifacts/${format}`
}

export function getTaskStatus(taskId: string): Promise<TaskStatusResponse> {
  return requestJson<TaskStatusResponse>(`${API_BASE_URL}/api/tasks/${taskId}`)
}

export function getTaskReport(taskId: string): Promise<TaskReportResponse> {
  return requestJson<TaskReportResponse>(`${API_BASE_URL}/api/tasks/${taskId}/report`)
}
export function getStructuredReport(taskId: string): Promise<StructuredReportResponse> {
  return requestJson<StructuredReportResponse>(`${API_BASE_URL}/api/tasks/${taskId}/report/structured`)
}

export function listTasks(limit = 20, offset = 0, search = '', status = ''): Promise<TaskListResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (search) params.set('search', search)
  if (status) params.set('status', status)
  return requestJson<TaskListResponse>(`${API_BASE_URL}/api/tasks?${params}`)
}

export function getTaskDetail(taskId: string): Promise<TaskDetailResponse> {
  return requestJson<TaskDetailResponse>(`${API_BASE_URL}/api/tasks/${taskId}/detail`)
}

export function cancelTask(taskId: string): Promise<TaskStatusResponse> {
  return requestJson<TaskStatusResponse>(`${API_BASE_URL}/api/tasks/${taskId}/cancel`, { method: 'POST' })
}

export function retryTask(taskId: string): Promise<TaskCreateResponse> {
  return requestJson<TaskCreateResponse>(`${API_BASE_URL}/api/tasks/${taskId}/retry`, { method: 'POST' })
}

export function resumeTask(taskId: string): Promise<TaskStatusResponse> {
  return requestJson(`${API_BASE_URL}/api/tasks/${taskId}/resume`, { method: 'POST' })
}
export function rerunTask(taskId: string): Promise<TaskCreateResponse> {
  return requestJson(`${API_BASE_URL}/api/tasks/${taskId}/rerun`, { method: 'POST' })
}
export async function deleteTask(taskId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/tasks/${taskId}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(`Delete failed (${response.status})`)
}
export function evidence(taskId: string, evidenceId: string): Promise<EvidenceItem> {
  return requestJson(`${API_BASE_URL}/api/tasks/${taskId}/evidence/${encodeURIComponent(evidenceId)}`)
}
export function taskEventsUrl(taskId: string, after = 0): string {
  return `${API_BASE_URL}/api/tasks/${taskId}/events?after=${after}`
}
