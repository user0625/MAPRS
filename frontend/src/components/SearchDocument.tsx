import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { listTasks, searchDocument } from '../api/paperApi'
import type {
  DocumentSearchHit,
  DocumentSearchMode,
  DocumentSearchResponse,
  TaskStatusResponse,
} from '../types/api'

export interface AskHandoff {
  taskId: string
  query: string
  nonce: number
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Document search failed.'
}

function sectionNames(task: TaskStatusResponse | undefined): string[] {
  const raw = task?.metadata.paper_sections
  if (!Array.isArray(raw)) return []
  return [...new Set(raw.map(item => {
    if (typeof item === 'string') return item
    return item && typeof item === 'object' && 'name' in item ? String(item.name) : ''
  }).filter(Boolean))]
}

function taskPageCount(task: TaskStatusResponse | undefined): number {
  const value = task?.metadata.num_pages
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : 0
}

function validatePageRange(startValue: string, endValue: string, totalPages: number): string {
  if (!startValue && !endValue) return ''
  if (!startValue || !endValue) return 'Enter both the first and last page, or clear both.'
  const start = Number(startValue), end = Number(endValue)
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < 1) return 'Pages must be positive whole numbers.'
  if (start > end) return 'The first page cannot be after the last page.'
  if (totalPages && end > totalPages) return `This paper has ${totalPages} pages.`
  return ''
}

function modeLabel(result: DocumentSearchResponse): string {
  if (result.mode_used === 'hybrid') return 'Hybrid'
  if (result.mode_used === 'degraded_to_bm25') return 'Degraded to BM25'
  return 'BM25'
}

const indexLabels = {
  memory_hit: 'Memory cache hit',
  disk_hit: 'Disk cache hit',
  cold_build: 'Cold index build',
  unavailable: 'Vector index unavailable',
} as const

function pages(hit: Pick<DocumentSearchHit, 'page_start' | 'page_end'>): string {
  if (!hit.page_start && !hit.page_end) return 'Pages unavailable'
  if (hit.page_start === hit.page_end || !hit.page_end) return `Page ${hit.page_start}`
  return `Pages ${hit.page_start}–${hit.page_end}`
}

export function SearchDocument({
  initialTaskId,
  onContinueAsk,
}: {
  initialTaskId?: string | null
  onContinueAsk: (handoff: AskHandoff) => void
}) {
  const [tasks, setTasks] = useState<TaskStatusResponse[]>([])
  const [taskId, setTaskId] = useState(initialTaskId || '')
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<DocumentSearchMode>('auto')
  const [section, setSection] = useState('')
  const [pageStart, setPageStart] = useState('')
  const [pageEnd, setPageEnd] = useState('')
  const [topK, setTopK] = useState(6)
  const [result, setResult] = useState<DocumentSearchResponse | null>(null)
  const [drawer, setDrawer] = useState<DocumentSearchHit | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let active = true
    listTasks(100, 0, '', 'completed').then(response => {
      if (!active) return
      setTasks(response.items)
      setTaskId(current => {
        const preferred = initialTaskId && response.items.some(item => item.task_id === initialTaskId) ? initialTaskId : ''
        return preferred || current || response.items[0]?.task_id || ''
      })
    }).catch(cause => { if (active) setError(errorMessage(cause)) })
    return () => { active = false }
  }, [initialTaskId])

  useEffect(() => {
    if (initialTaskId && tasks.some(item => item.task_id === initialTaskId)) setTaskId(initialTaskId)
  }, [initialTaskId, tasks])

  const task = tasks.find(item => item.task_id === taskId)
  const totalPages = taskPageCount(task)
  const availableSections = useMemo(() => sectionNames(task), [task])
  const pageError = validatePageRange(pageStart, pageEnd, totalPages)

  function changeTask(nextTaskId: string) {
    setTaskId(nextTaskId)
    setSection(''); setPageStart(''); setPageEnd('')
    setResult(null); setDrawer(null); setError('')
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    const trimmed = query.trim()
    if (!taskId || !trimmed || pageError) return
    setLoading(true); setError(''); setDrawer(null); setCopied(false)
    try {
      setResult(await searchDocument(taskId, {
        query: trimmed,
        mode,
        section: section || null,
        page_start: pageStart ? Number(pageStart) : null,
        page_end: pageEnd ? Number(pageEnd) : null,
        top_k: topK,
      }))
    } catch (cause) {
      setResult(null)
      setError(errorMessage(cause))
    } finally {
      setLoading(false)
    }
  }

  async function copyPassage() {
    if (!drawer) return
    const context = drawer.context.map(item => `${item.relation === 'before' ? 'Before' : 'After'}: ${item.text}`).join('\n\n')
    await navigator.clipboard.writeText([drawer.text, context].filter(Boolean).join('\n\n'))
    setCopied(true)
  }

  return <section className="search-workspace">
    <form className="panel search-form" onSubmit={submit}>
      <header><span className="eyebrow">Instant retrieval</span><h2>Search document</h2><p>Find passages directly with lexical and semantic retrieval. No answer is generated.</p></header>
      <label>Completed paper<select aria-label="Search paper" value={taskId} onChange={event => changeTask(event.target.value)}><option value="">Select a paper</option>{tasks.map(item => <option key={item.task_id} value={item.task_id}>{item.paper_title || item.task_id}</option>)}</select></label>
      <label>Search query<textarea aria-label="Search query" maxLength={8000} value={query} onChange={event => setQuery(event.target.value)} placeholder="Search methods, findings, limitations…" /></label>
      <fieldset className="search-mode"><legend>Retrieval mode</legend><label><input type="radio" name="search-mode" checked={mode === 'auto'} onChange={() => setMode('auto')} /><span><strong>Auto</strong><small>BM25 + vector when available</small></span></label><label><input type="radio" name="search-mode" checked={mode === 'bm25'} onChange={() => setMode('bm25')} /><span><strong>BM25</strong><small>Offline lexical baseline</small></span></label></fieldset>
      <div className="search-filter-grid">
        <label>Section<select aria-label="Search section" value={section} onChange={event => setSection(event.target.value)}><option value="">Whole paper</option>{availableSections.map(name => <option key={name}>{name}</option>)}</select></label>
        <label>Top K<select aria-label="Search Top K" value={topK} onChange={event => setTopK(Number(event.target.value))}>{[3, 6, 10, 20].map(value => <option key={value}>{value}</option>)}</select></label>
      </div>
      <label>Page range<div className="page-range"><input aria-label="Search from page" type="number" min="1" max={totalPages || undefined} value={pageStart} onChange={event => setPageStart(event.target.value)} placeholder="From page" /><span>to</span><input aria-label="Search to page" type="number" min="1" max={totalPages || undefined} value={pageEnd} onChange={event => setPageEnd(event.target.value)} placeholder={totalPages ? `To ${totalPages}` : 'To page'} /></div></label>
      {pageError && <small className="scope-error">{pageError}</small>}
      <button className="primary-button" disabled={loading || !taskId || !query.trim() || Boolean(pageError)}>{loading ? <><span className="spinner" />Searching…</> : 'Search paper'}</button>
    </form>

    <div className="search-main">
      {!result && !loading && !error && <div className="panel search-empty"><span aria-hidden="true">⌕</span><h2>Locate the exact passage</h2><p>Choose a completed paper and run a query to inspect ranked source text and its immediate context.</p></div>}
      {error && <div className="request-error" role="alert"><strong>Unable to search</strong><span>{error}</span></div>}
      {result && <>
        <header className="panel search-summary"><div><span className={`search-mode-badge mode-${result.mode_used}`}>{modeLabel(result)}</span><h2>{result.hits.length ? `${result.hits.length} ranked passages` : 'No matching passages'}</h2><p>{result.hits.length ? `Search completed in ${result.diagnostics.elapsed_ms.toFixed(1)} ms.` : 'Try a broader query or remove section and page filters.'}</p></div><div className="search-summary-actions"><span>{indexLabels[result.diagnostics.index_source]}</span><button onClick={() => onContinueAsk({ taskId: result.task_id, query: result.query, nonce: Date.now() })}>Continue in Ask Paper</button></div></header>
        {result.mode_used === 'degraded_to_bm25' && <div className="search-degraded" role="status">Vector retrieval was unavailable, so this search safely used BM25.</div>}
        <div className="search-results">{result.hits.map(hit => <article className="panel search-hit" key={hit.chunk_id}>
          <header><span className="search-rank">#{hit.rank}</span><div className="source-tags">{hit.sources.map(source => <span key={source} className={`source-${source}`}>{source === 'bm25' ? 'BM25' : 'Vector'}</span>)}</div></header>
          <div className="search-hit-meta"><span>{pages(hit)}</span><span>{hit.section || 'Unsectioned'}</span></div>
          <p>{hit.text}</p>
          <footer><details><summary>Score details</summary><dl><dt>BM25</dt><dd>{hit.bm25_score?.toFixed(5) ?? '—'}</dd><dt>Vector</dt><dd>{hit.vector_score?.toFixed(5) ?? '—'}</dd><dt>Hybrid</dt><dd>{hit.hybrid_score?.toFixed(5) ?? '—'}</dd></dl></details><button onClick={() => { setDrawer(hit); setCopied(false) }}>Open passage</button></footer>
        </article>)}</div>
      </>}
      {drawer && <aside role="dialog" aria-label={`Passage ${drawer.chunk_id}`} className="search-drawer"><header><div><small>Rank #{drawer.rank}</small><strong>{pages(drawer)} · {drawer.section || 'Unsectioned'}</strong></div><button aria-label="Close passage" onClick={() => setDrawer(null)}>×</button></header><section><span>Matched passage</span><blockquote>{drawer.text}</blockquote></section>{drawer.context.map(item => <section key={`${item.relation}-${item.chunk_id}`}><span>{item.relation === 'before' ? 'Previous chunk' : 'Next chunk'} · {pages(item)}</span><blockquote>{item.text}</blockquote></section>)}<button onClick={() => void copyPassage()}>{copied ? 'Copied' : 'Copy passage and context'}</button></aside>}
    </div>
  </section>
}
