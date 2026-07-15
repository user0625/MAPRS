import { useCallback, useEffect, useState } from 'react'
import { cancelTask, deleteTask, getTaskDetail, listTasks, rerunTask, resumeTask, retryTask } from '../api/paperApi'
import type { TaskDetailResponse, TaskStatus, TaskStatusResponse } from '../types/api'
import { InteractiveReport } from './InteractiveReport'

const PAGE_SIZE = 3
const POLL_INTERVAL_MS = 3000

function formatDate(value: string | null): string {
  return value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
}

function taskName(task: TaskStatusResponse): string {
  return task.paper_title || String(task.metadata.original_filename || task.task_id)
}

function StatusBadge({ status }: { status: TaskStatus }) {
  return <span className={`status-badge status-${status}`}><span className="status-dot" />{status}</span>
}

export function TaskHistory({ refreshToken, onSearchDocument }: { refreshToken: number; onSearchDocument?: (taskId: string) => void }) {
  const [tasks, setTasks] = useState<TaskStatusResponse[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<TaskDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actionPending, setActionPending] = useState(false)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const loadList = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await listTasks(PAGE_SIZE, offset, search, statusFilter)
      setTasks(response.items)
      setTotal(response.total)
      setSelectedId(current => current && response.items.some(item => item.task_id === current)
        ? current : response.items[0]?.task_id || null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load task history.')
    } finally {
      setLoading(false)
    }
  }, [offset, search, statusFilter])

  useEffect(() => { void loadList() }, [loadList, refreshToken])

  useEffect(() => {
    if (!selectedId) { setDetail(null); return }
    let cancelled = false
    let timeout: ReturnType<typeof setTimeout> | undefined
    const load = async () => {
      setDetailLoading(true)
      try {
        const response = await getTaskDetail(selectedId)
        if (cancelled) return
        setDetail(response)
        setError(null)
        if (response.status === 'pending' || response.status === 'running') timeout = setTimeout(load, POLL_INTERVAL_MS)
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : 'Could not load task details.')
      } finally {
        if (!cancelled) setDetailLoading(false)
      }
    }
    void load()
    return () => { cancelled = true; if (timeout) clearTimeout(timeout) }
  }, [selectedId])

  const authors = detail?.paper_authors?.length ? detail.paper_authors :
    (Array.isArray(detail?.metadata.paper_authors) ? detail.metadata.paper_authors.filter((author): author is string => typeof author === 'string') : [])
  const quality = (detail?.metadata.quality_evaluation || detail?.workflow_metadata.quality_evaluation || {}) as Record<string, unknown>
  const documentParsing = (detail?.metadata.document_parsing || detail?.workflow_metadata.document_parsing || {}) as Record<string, unknown>
  const metadataQuality = (detail?.metadata.metadata_quality || detail?.workflow_metadata.metadata_quality || {}) as Record<string, { source?: string, confidence?: number }>
  const paperSections = (detail?.metadata.paper_sections || detail?.workflow_metadata.paper_sections || []) as Array<{ name?: string, page_start?: number, page_end?: number }>

  const runAction = async (action: 'cancel' | 'retry' | 'resume' | 'rerun' | 'delete') => {
    if (!detail) return
    setActionPending(true); setError(null)
    try {
      if (action === 'delete') {
        if (!window.confirm('Delete this task and all files permanently?')) return
        await deleteTask(detail.task_id); setSelectedId(null); setDetail(null)
      } else {
        const response = action === 'cancel' ? await cancelTask(detail.task_id) : action === 'retry' ? await retryTask(detail.task_id) : action === 'resume' ? await resumeTask(detail.task_id) : await rerunTask(detail.task_id)
        if ('task_id' in response && action !== 'cancel' && action !== 'resume') setSelectedId(response.task_id)
        else setDetail(await getTaskDetail(response.task_id))
      }
      await loadList()
    } catch (cause) { setError(cause instanceof Error ? cause.message : `Could not ${action} task.`) }
    finally { setActionPending(false) }
  }

  return <div className="history-layout">
    <div className="history-top">
    <aside className="panel history-list">
      <div className="history-heading"><div><span className="eyebrow">Archive</span><h2>Task history</h2></div><button onClick={() => void loadList()} disabled={loading}>Refresh</button></div>
      <div className="history-filters"><input aria-label="Search tasks" type="search" placeholder="Search title or task ID" value={search} onChange={e => { setOffset(0); setSearch(e.target.value) }} /><select aria-label="Filter by status" value={statusFilter} onChange={e => { setOffset(0); setStatusFilter(e.target.value) }}><option value="">All statuses</option>{['pending','running','completed','failed','canceled'].map(value => <option key={value}>{value}</option>)}</select></div>
      {error && <div className="request-error" role="alert">{error}</div>}
      {loading ? <p className="history-state">Loading tasks…</p> : tasks.length === 0 ? <p className="history-state">No analysis tasks yet.</p> :
        <div className="task-list">{tasks.map(task => <button className={selectedId === task.task_id ? 'selected' : ''} key={task.task_id} onClick={() => setSelectedId(task.task_id)}>
          <strong>{taskName(task)}</strong><div><StatusBadge status={task.status} /><time>{formatDate(task.created_at)}</time></div><small>{String(task.metadata.language || '—').toUpperCase()}</small>
        </button>)}</div>}
      <div className="pagination"><button disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button><span>{total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total}` : '0 tasks'}</span><button disabled={offset + PAGE_SIZE >= total || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button></div>
    </aside>
    <section className="history-detail">
      {!selectedId ? <div className="panel detail-empty">Select a task to see its complete details.</div> : detailLoading && !detail ? <div className="panel detail-empty">Loading details…</div> : !detail ? <div className="panel detail-empty">{error || 'Task details are unavailable.'}</div> : <>
        <div className="panel detail-card">
          <div className="detail-title"><div><span className="eyebrow">Analysis detail</span><h2>{taskName(detail)}</h2></div><div className="detail-actions"><StatusBadge status={detail.status} />{detail.status === 'completed' && onSearchDocument && <button onClick={() => onSearchDocument(detail.task_id)}>Search document</button>}{(detail.status === 'pending' || detail.status === 'running') && <button disabled={actionPending} onClick={() => void runAction('cancel')}>Cancel</button>}{detail.status === 'failed' && detail.last_checkpoint_step && <button disabled={actionPending} onClick={() => void runAction('resume')}>Resume</button>}{(detail.status === 'failed' || detail.status === 'canceled') && <button disabled={actionPending} onClick={() => void runAction('retry')}>Retry</button>}{['completed','failed','canceled'].includes(detail.status) && <><button disabled={actionPending} onClick={() => void runAction('rerun')}>Rerun</button><button disabled={actionPending} onClick={() => void runAction('delete')}>Delete</button></>}</div></div>
          {detail.error_message && <div className="error-message"><strong>Task failed</strong>{detail.error_message}</div>}
          <dl className="detail-fields">
            <div className="wide"><dt>Paper title</dt><dd>{detail.paper_title || '—'}</dd></div>
            <div className="wide"><dt>Paper authors</dt><dd>{authors.length ? authors.join(', ') : '—'}</dd></div>
            <div><dt>Task ID</dt><dd>{detail.task_id}</dd></div><div><dt>Paper ID</dt><dd>{detail.paper_id || '—'}</dd></div>
            <div><dt>Original file</dt><dd>{String(detail.metadata.original_filename || '—')}</dd></div><div><dt>Language</dt><dd>{String(detail.metadata.language || '—')}</dd></div>
            <div className="wide"><dt>Query</dt><dd>{String(detail.metadata.query || '—')}</dd></div>
            <div><dt>Created</dt><dd>{formatDate(detail.created_at)}</dd></div><div><dt>Updated</dt><dd>{formatDate(detail.updated_at)}</dd></div><div><dt>Completed</dt><dd>{formatDate(detail.completed_at)}</dd></div>
            <div><dt>Prompt set</dt><dd>{String(detail.workflow_metadata.prompt_set_version || detail.metadata.prompt_set_version || '—')}</dd></div><div><dt>Structured calls</dt><dd>{String((detail.workflow_metadata.structured_output_stats as Record<string, unknown> | undefined)?.total_calls || '—')}</dd></div>
          </dl>
          {Object.keys(documentParsing).length > 0 && <div className="document-parsing"><div><h3>Document parsing</h3><small>{String(documentParsing.mode || 'auto')} · {String(documentParsing.layout_version || 'unknown')}</small></div><dl>
            <div><dt>Layout pages</dt><dd>{String(documentParsing.layout_pages ?? 0)}</dd></div>
            <div><dt>Fallback pages</dt><dd>{String(documentParsing.fallback_pages ?? 0)}</dd></div>
            <div><dt>Columns</dt><dd>{String(documentParsing.single_column_pages ?? 0)} single · {String(documentParsing.double_column_pages ?? 0)} double</dd></div>
            <div><dt>Blocks kept</dt><dd>{String(documentParsing.blocks_retained ?? 0)}</dd></div>
            <div><dt>Margins removed</dt><dd>{String(documentParsing.header_footer_blocks_removed ?? 0)}</dd></div>
            <div><dt>Words rejoined</dt><dd>{String(documentParsing.dehyphenations ?? 0)}</dd></div>
          </dl></div>}
          {Object.keys(metadataQuality).length > 0 && <div className="phase-d-details"><h3>Metadata provenance</h3><ul>{Object.entries(metadataQuality).map(([name, field]) => <li key={name}><strong>{name}</strong>: {field.source || 'unidentified'} · {typeof field.confidence === 'number' ? `${Math.round(field.confidence * 100)}%` : '—'}</li>)}</ul></div>}
          {paperSections.length > 0 && <div className="phase-d-details"><h3>Detected sections</h3><ol>{paperSections.map((section, index) => <li key={`${section.name}-${index}`}>{section.name || 'Unidentified'} <small>pp. {section.page_start}–{section.page_end}</small></li>)}</ol></div>}
          {Object.keys(quality).length > 0 && <div className={`quality-card ${quality.passed ? 'passed' : 'warning'}`}><h3>Report quality · {String(quality.overall || 0)}/100</h3><div>{['accuracy','completeness','faithfulness','citation_validity','critical_depth'].map(key => <span key={key}>{key.replace('_', ' ')} <strong>{String(quality[key] ?? '—')}</strong></span>)}</div><p>Citation coverage: {typeof quality.citation_coverage === 'number' ? `${Math.round(quality.citation_coverage * 100)}%` : '—'} · Revisions: {String(quality.revision_count ?? 0)}</p></div>}
        </div>
      </>}
    </section>
    </div>
    {detail && <section className="history-lower">
        <details className="panel collapsible-card timeline-card" open><summary><h3>Workflow timeline</h3><span aria-hidden="true" /></summary><div className="collapsible-content">
          {!detail.state_available ? <p className="missing-note">Workflow state file is unavailable. Task metadata is still preserved.</p> : !detail.step_history?.length ? <p className="missing-note">No workflow steps were recorded.</p> :
            <ol className="timeline">{detail.step_history.map((step, index) => <li key={`${step.step_name}-${index}`}><span className={`step-dot step-${step.status}`} /><div><div><strong>{step.step_name}</strong><time>{formatDate(step.timestamp)}</time></div><small>{step.status}</small>{step.message && <p>{step.message}</p>}{Object.keys(step.metadata).length > 0 && <pre>{JSON.stringify(step.metadata, null, 2)}</pre>}</div></li>)}</ol>}
        </div></details>
        <details className="panel collapsible-card history-report" open><summary><h3>Report</h3><span aria-hidden="true" /></summary><div className="collapsible-content">{detail.report_available && detail.report_markdown ? <InteractiveReport taskId={detail.task_id} markdown={detail.report_markdown} compact /> : <p className="missing-note">{detail.status === 'completed' ? 'The report file is unavailable.' : 'The report will appear after this task completes.'}</p>}</div></details>
    </section>}
  </div>
}
