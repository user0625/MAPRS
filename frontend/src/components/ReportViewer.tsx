import Markdown from 'react-markdown'
import type { TaskReportResponse } from '../types/api'

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
        <span className="report-ready">Markdown</span>
      </header>
      <article className="markdown-body">
        <Markdown>{report.report_markdown}</Markdown>
      </article>
    </section>
  )
}
