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
  Conversation, ConversationDetail, AskAccepted, AskLanguage,
} from '../types/api'

const DEFAULT_API_BASE_URL = ''
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL).replace(/\/$/, '')

interface ApiErrorBody {
  detail?: string
}

async function responseError(response: Response, fallback: string): Promise<Error> {
  let message = fallback
  try {
    const body = (await response.json()) as ApiErrorBody
    if (typeof body.detail === 'string') message = body.detail
  } catch {
    // Keep the HTTP status fallback when the response is not JSON.
  }
  return new Error(message)
}

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init)

  if (!response.ok) {
    throw await responseError(response, `Request failed (${response.status})`)
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

export function listConversations(taskId:string, search=''):Promise<{items:Conversation[]}> {
  const params = new URLSearchParams()
  if (search.trim()) params.set('search', search)
  const query = params.size ? `?${params}` : ''
  return requestJson(`${API_BASE_URL}/api/tasks/${taskId}/conversations${query}`)
}
export function createConversation(taskId:string, language:AskLanguage='auto'):Promise<Conversation> { return requestJson(`${API_BASE_URL}/api/tasks/${taskId}/conversations`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({language})}) }
export function getConversation(id:string):Promise<ConversationDetail> { return requestJson(`${API_BASE_URL}/api/conversations/${id}`) }
export function updateConversationTitle(id:string,title:string):Promise<Conversation> { return requestJson(`${API_BASE_URL}/api/conversations/${id}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title})}) }
export async function deleteConversation(id:string):Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/conversations/${id}`, {method:'DELETE'})
  if (!response.ok) throw await responseError(response, `Delete failed (${response.status})`)
}
export async function downloadConversationArtifact(id:string, format:'markdown'|'json'):Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/conversations/${id}/artifacts/${format}`)
  if (!response.ok) throw await responseError(response, `Download failed (${response.status})`)
  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition') || ''
  const matched = /filename="?([^";]+)"?/i.exec(disposition)
  const filename = matched?.[1] || `ask-paper-${id}.${format === 'markdown' ? 'md' : 'json'}`
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
export function askQuestion(id:string, content:string, section:string|null, language:AskLanguage, pageStart:number|null=null, pageEnd:number|null=null):Promise<AskAccepted> { return requestJson(`${API_BASE_URL}/api/conversations/${id}/messages`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content,section,language,page_start:pageStart,page_end:pageEnd})}) }
export function cancelAnswer(conversationId:string,messageId:string) { return requestJson(`${API_BASE_URL}/api/conversations/${conversationId}/messages/${messageId}/cancel`,{method:'POST'}) }
export function retryAnswer(conversationId:string,messageId:string):Promise<AskAccepted> { return requestJson(`${API_BASE_URL}/api/conversations/${conversationId}/messages/${messageId}/retry`,{method:'POST'}) }
export function messageEventsUrl(conversationId:string,messageId:string,after=0) { return `${API_BASE_URL}/api/conversations/${conversationId}/messages/${messageId}/events?after=${after}` }
