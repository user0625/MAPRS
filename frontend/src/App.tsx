import { useCallback, useEffect, useRef, useState } from 'react'
import { cancelTask, createAnalysisTask, getTaskReport, getTaskStatus } from './api/paperApi'
import { ReportViewer } from './components/ReportViewer'
import { TaskStatusCard } from './components/TaskStatusCard'
import { UploadPanel } from './components/UploadPanel'
import { TaskHistory } from './components/TaskHistory'
import type {
  OutputLanguage,
  TaskReportResponse,
  TaskStatusResponse,
  ReportConfiguration,
} from './types/api'
import './App.css'

const POLL_INTERVAL_MS = 3000

function toErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'An unexpected error occurred.'
}

function App() {
  const [activeTab, setActiveTab] = useState<'new' | 'history'>('new')
  const [historyRefresh, setHistoryRefresh] = useState(0)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [task, setTask] = useState<TaskStatusResponse | null>(null)
  const [report, setReport] = useState<TaskReportResponse | null>(null)
  const [requestError, setRequestError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [actionPending, setActionPending] = useState(false)
  const generationRef = useRef(0)

  const handleSubmit = useCallback(async (
    file: File,
    query: string,
    language: OutputLanguage,
    configuration: ReportConfiguration,
  ) => {
    generationRef.current += 1
    setSubmitting(true)
    setTaskId(null)
    setTask(null)
    setReport(null)
    setRequestError(null)

    try {
      const created = await createAnalysisTask(file, query, language, configuration)
      setTaskId(created.task_id)
      setHistoryRefresh(value => value + 1)
    } catch (error: unknown) {
      setRequestError(toErrorMessage(error))
      setSubmitting(false)
    }
  }, [])

  useEffect(() => {
    if (!taskId) return

    let cancelled = false
    let timeoutId: ReturnType<typeof setTimeout> | undefined
    const generation = generationRef.current

    const poll = async () => {
      try {
        const nextTask = await getTaskStatus(taskId)
        if (cancelled || generation !== generationRef.current) return

        setTask(nextTask)

        if (nextTask.status === 'completed') {
          const nextReport = await getTaskReport(taskId)
          if (cancelled || generation !== generationRef.current) return
          setReport(nextReport)
          setSubmitting(false)
          setHistoryRefresh(value => value + 1)
          return
        }

        if (nextTask.status === 'failed' || nextTask.status === 'canceled') {
          setSubmitting(false)
          return
        }

        timeoutId = setTimeout(poll, POLL_INTERVAL_MS)
      } catch (error: unknown) {
        if (cancelled || generation !== generationRef.current) return
        setRequestError(toErrorMessage(error))
        setSubmitting(false)
      }
    }

    void poll()

    return () => {
      cancelled = true
      if (timeoutId !== undefined) clearTimeout(timeoutId)
    }
  }, [taskId])

  const handleCancel = useCallback(async () => {
    if (!taskId) return
    setActionPending(true); setRequestError(null)
    try { const next = await cancelTask(taskId); setTask(next); setHistoryRefresh(value => value + 1) }
    catch (error) { setRequestError(toErrorMessage(error)) }
    finally { setActionPending(false) }
  }, [taskId])

  return (
    <div className="app-shell">
      <header className="app-header">
        <a className="brand" href="/" aria-label="Paper Reader home">
          <span className="brand-mark" aria-hidden="true">M</span>
          <span>
            <strong>Multi-Agent</strong>
            <small>Paper Reader</small>
          </span>
        </a>
        <div className="system-status">
          <span aria-hidden="true" />
          Research workspace
        </div>
      </header>

      <main>
        <section className="intro">
          <span className="intro-kicker">AI-assisted literature review</span>
          <h1>Read papers with a team of <em>specialized agents.</em></h1>
          <p>Upload a research paper and turn dense academic writing into an evidence-grounded, structured report.</p>
        </section>

        <nav className="workspace-tabs" aria-label="Workspace views">
          <button className={activeTab === 'new' ? 'active' : ''} onClick={() => setActiveTab('new')}>New Analysis</button>
          <button className={activeTab === 'history' ? 'active' : ''} onClick={() => setActiveTab('history')}>Task History</button>
        </nav>

        {activeTab === 'new' ? <div className="workspace-grid">
          <UploadPanel disabled={submitting} onSubmit={handleSubmit} />
          <div className="results-column">
            <TaskStatusCard task={task} taskId={taskId} onCancel={handleCancel} actionPending={actionPending} />
            {requestError && (
              <div className="request-error" role="alert">
                <strong>Unable to continue</strong>
                <span>{requestError}</span>
              </div>
            )}
            <ReportViewer report={report} loading={submitting} />
          </div>
        </div> : <TaskHistory refreshToken={historyRefresh} />}
      </main>

      <footer>
        <span>Multi-Agent Paper Reader System</span>
        <span>Built for focused research</span>
      </footer>
    </div>
  )
}

export default App
