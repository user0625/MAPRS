import type {
  OutputLanguage,
  TaskCreateResponse,
  TaskReportResponse,
  TaskStatusResponse,
  TaskDetailResponse,
  TaskListResponse,
} from '../types/api'

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000'
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
): Promise<TaskCreateResponse> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('query', query)
  formData.append('language', language)

  return requestJson<TaskCreateResponse>(`${API_BASE_URL}/api/tasks/analyze`, {
    method: 'POST',
    body: formData,
  })
}

export function getTaskStatus(taskId: string): Promise<TaskStatusResponse> {
  return requestJson<TaskStatusResponse>(`${API_BASE_URL}/api/tasks/${taskId}`)
}

export function getTaskReport(taskId: string): Promise<TaskReportResponse> {
  return requestJson<TaskReportResponse>(`${API_BASE_URL}/api/tasks/${taskId}/report`)
}

export function listTasks(limit = 20, offset = 0): Promise<TaskListResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return requestJson<TaskListResponse>(`${API_BASE_URL}/api/tasks?${params}`)
}

export function getTaskDetail(taskId: string): Promise<TaskDetailResponse> {
  return requestJson<TaskDetailResponse>(`${API_BASE_URL}/api/tasks/${taskId}/detail`)
}
