import type { TaskReportResponse } from '../types/api'
import { InteractiveReport } from './InteractiveReport'

interface ReportViewerProps {
  report: TaskReportResponse | null
  loading: boolean
}

export function ReportViewer({ report, loading }: ReportViewerProps) {
  if (!report) {
    return (
      <section className="panel report-panel report-empty">
        <div className="report-placeholder" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <h2>{loading ? 'Preparing your report…' : 'Your report will appear here'}</h2>
        <p>The final Markdown report is available when all agents finish their work.</p>
      </section>
    )
  }

  return (
    <section className="panel report-panel">
      <header className="report-header">
        <div>
          <span className="eyebrow">Final report</span>
          <h2>Paper analysis</h2>
        </div>
      </header>
      <InteractiveReport taskId={report.task_id} markdown={report.report_markdown} />
    </section>
  )
}
