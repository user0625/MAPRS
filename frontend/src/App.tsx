import { useCallback, useEffect, useRef, useState } from 'react'
import { cancelTask, createAnalysisTask, getTaskReport } from './api/paperApi'
import { useTaskEvents } from './hooks/useTaskEvents'
import { ReportViewer } from './components/ReportViewer'
import { TaskStatusCard } from './components/TaskStatusCard'
import { UploadPanel } from './components/UploadPanel'
import { TaskHistory } from './components/TaskHistory'
import type {
  OutputLanguage,
  TaskReportResponse,
  ReportConfiguration,
} from './types/api'
import './App.css'

function toErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'An unexpected error occurred.'
}

function App() {
  const [activeTab, setActiveTab] = useState<'new' | 'history'>('new')
  const [historyRefresh, setHistoryRefresh] = useState(0)
  const [taskId, setTaskId] = useState<string | null>(null)
  const live = useTaskEvents(taskId)
  const task = live.task
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
    if (!taskId || !task) return
    if (task.status === 'completed' && !report) void getTaskReport(taskId).then(setReport).catch(error => setRequestError(toErrorMessage(error)))
    if (['completed','failed','canceled'].includes(task.status)) { setSubmitting(false); setHistoryRefresh(value => value + 1) }
  }, [taskId, task, report])

  const handleCancel = useCallback(async () => {
    if (!taskId) return
    setActionPending(true); setRequestError(null)
    try { await cancelTask(taskId); setHistoryRefresh(value => value + 1) }
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
        <ThemeSwitch />
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
            <TaskStatusCard task={task} taskId={taskId} connection={live.connection} onCancel={handleCancel} actionPending={actionPending} />
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

function ThemeSwitch() {
  const [theme, setTheme] = useState(() => localStorage.getItem('paper-reader-theme') || 'system')
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('paper-reader-theme', theme) }, [theme])
  return <label className="theme-switch">Theme<select aria-label="Theme" value={theme} onChange={e => setTheme(e.target.value)}>
    <option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option>
  </select></label>
}

export default App
