import { useEffect, useMemo, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { artifactUrl, evidence, getStructuredReport } from '../api/paperApi'
import type { EvidenceItem, StructuredReportResponse } from '../types/api'
import { ReportActions } from './ReportActions'

interface Props { taskId: string; markdown: string; compact?: boolean }

function anchors(sections: StructuredReportResponse['report']['sections']) {
  const used = new Map<string, number>()
  return sections.map(section => {
    const base = section.title.toLowerCase().normalize('NFKD').replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '') || 'section'
    const count = used.get(base) || 0; used.set(base, count + 1)
    return { section, id: `report-${base}${count ? `-${count + 1}` : ''}` }
  })
}

export function InteractiveReport({ taskId, markdown, compact = false }: Props) {
  const [structured, setStructured] = useState<StructuredReportResponse | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)
  const [query, setQuery] = useState('')
  const [match, setMatch] = useState(-1)
  const [active, setActive] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [cache, setCache] = useState<Record<string, EvidenceItem>>({})
  const [evidenceError, setEvidenceError] = useState<string | null>(null)
  const [evidenceLoading, setEvidenceLoading] = useState(false)
  const returnFocus = useRef<HTMLElement | null>(null)
  const closeButton = useRef<HTMLButtonElement | null>(null)
  const sectionsContainer = useRef<HTMLElement | null>(null)
  const scrollFrame = useRef<number | null>(null)

  useEffect(() => {
    let cancelled = false
    setStructured(null); setLoadError(null); setQuery(''); setSelected(null); setCache({}); setMatch(-1)
    getStructuredReport(taskId).then(value => { if (!cancelled) setStructured(value) }).catch(error => {
      if (!cancelled && !String(error).includes('unavailable')) setLoadError(error instanceof Error ? error.message : 'Structured report could not be loaded.')
    })
    return () => { cancelled = true }
  }, [taskId, retry])

  const items = useMemo(() => anchors(structured?.report.sections || []), [structured])
  const matches = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return needle ? items.filter(({ section }) => [section.title, section.content, ...section.claims.map(claim => claim.text)].join('\n').toLocaleLowerCase().includes(needle)) : items
  }, [items, query])

  useEffect(() => { setMatch(-1) }, [query, taskId])
  useEffect(() => {
    if (!selected) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setSelected(null); returnFocus.current?.focus() }
      if (event.key === 'Tab') {
        const drawer = closeButton.current?.closest('[role="dialog"]')
        const focusable = drawer?.querySelectorAll<HTMLElement>('button, a[href], [tabindex]:not([tabindex="-1"])')
        if (!focusable?.length) return
        const first = focusable[0]; const last = focusable[focusable.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    window.addEventListener('keydown', onKey); closeButton.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [selected])

  useEffect(() => () => { if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current) }, [])

  const go = (id: string) => {
    const container = sectionsContainer.current
    const target = document.getElementById(id)
    if (!container || !target) return
    setActive(id)
    container.scrollTo({
      top: Math.max(0, container.scrollTop + target.getBoundingClientRect().top - container.getBoundingClientRect().top),
      behavior: 'smooth',
    })
  }
  const resetNavigation = () => {
    setQuery('')
    setMatch(-1)
    setActive('')
    if (sectionsContainer.current) sectionsContainer.current.scrollTop = 0
  }
  const move = (delta: number) => { if (!matches.length) return; const next = (match + delta + matches.length) % matches.length; setMatch(next); go(matches[next].id) }
  const trackCurrentSection = () => {
    if (scrollFrame.current !== null) return
    scrollFrame.current = requestAnimationFrame(() => {
      scrollFrame.current = null
      const container = sectionsContainer.current
      if (!container) return
      const threshold = container.getBoundingClientRect().top + 32
      const current = [...container.querySelectorAll<HTMLElement>(':scope > section')]
        .filter(section => section.getBoundingClientRect().top <= threshold).at(-1)
      setActive(current?.id || '')
    })
  }
  const openEvidence = async (id: string, button: HTMLButtonElement) => {
    returnFocus.current = button; setSelected(id); setEvidenceError(null)
    if (cache[id]) return
    setEvidenceLoading(true)
    try { const item = await evidence(taskId, id); setCache(current => ({ ...current, [id]: item })) }
    catch (error) { setEvidenceError(error instanceof Error ? error.message : 'Evidence is unavailable.') }
    finally { setEvidenceLoading(false) }
  }
  const selectedItem = selected ? cache[selected] : undefined

  return <div className={`interactive-report ${compact ? 'compact' : ''}`}>
    <div className="interactive-report-actions"><ReportActions markdown={markdown} filename={`${taskId}-report.md`} />
      {(['markdown','json','html','pdf','docx'] as const).map(format => <a className="artifact-link" key={format} href={artifactUrl(taskId, format)}>{format.toUpperCase()}</a>)}
    </div>
    {loadError && <div className="structured-warning" role="status">Interactive navigation unavailable: {loadError} <button onClick={() => setRetry(value => value + 1)}>Retry</button></div>}
    {!structured ? <article className="markdown-body"><Markdown>{markdown}</Markdown></article> : <>
      <div className="report-search"><label>Search report<input type="search" value={query} onChange={event => event.target.value ? setQuery(event.target.value) : resetNavigation()} /></label><span aria-live="polite">{query ? `${matches.length} matching sections` : `${items.length} sections`}</span><button type="button" disabled={!query || !matches.length} onClick={() => move(-1)}>Previous</button><button type="button" disabled={!query || !matches.length} onClick={() => move(1)}>Next</button>{(query || active) && <button type="button" onClick={resetNavigation}>Reset</button>}</div>
      {query && !matches.length && <p className="report-no-results" role="status">No matching sections.</p>}
      <details className="report-mobile-toc"><summary>Contents</summary><nav><button type="button" onClick={resetNavigation}>Overview</button>{items.map(item => <button type="button" className={query && matches.some(matchItem => matchItem.id === item.id) ? 'search-result' : ''} key={item.id} onClick={() => go(item.id)}>{item.section.title}</button>)}</nav></details>
      <div className="report-reader"><nav className="report-toc" aria-label="Report contents"><button type="button" className={!active ? 'active' : ''} aria-current={!active ? 'location' : undefined} onClick={resetNavigation}>Overview</button>{items.map(item => <button type="button" className={`${active === item.id ? 'active' : ''} ${query && matches.some(matchItem => matchItem.id === item.id) ? 'search-result' : ''}`} aria-current={active === item.id ? 'location' : undefined} key={item.id} onClick={() => go(item.id)}>{item.section.title}</button>)}</nav>
        <article ref={sectionsContainer} onScroll={trackCurrentSection} className="report-sections markdown-body">{items.map(({ section, id }) => <section id={id} key={id} className={query && matches.some(item => item.id === id) ? 'search-match' : ''}><h2>{section.title}</h2><Markdown>{section.content}</Markdown>{section.claims.length > 0 && <ul className="report-claims">{section.claims.map((claim, index) => <li key={index}>{claim.text}</li>)}</ul>}<div className="evidence-tags">{section.evidence_ids.map(evidenceId => <button type="button" key={evidenceId} onClick={event => void openEvidence(evidenceId, event.currentTarget)}>Evidence {evidenceId}</button>)}</div></section>)}</article>
      </div>
    </>}
    {selected && <aside className="evidence-drawer" role="dialog" aria-modal="true" aria-label={`Evidence ${selected}`}><header><strong>Evidence {selected}</strong><button ref={closeButton} aria-label="Close evidence" onClick={() => { setSelected(null); returnFocus.current?.focus() }}>×</button></header>{evidenceLoading && <p>Loading evidence…</p>}{evidenceError && <p role="alert">{evidenceError} <button onClick={() => { const target = returnFocus.current; if (target instanceof HTMLButtonElement) void openEvidence(selected, target) }}>Retry</button></p>}{selectedItem && <><dl><dt>Section</dt><dd>{selectedItem.section || '—'}</dd><dt>Pages</dt><dd>{selectedItem.page_start ?? '—'}–{selectedItem.page_end ?? '—'}</dd><dt>Chunk</dt><dd>{selectedItem.chunk_id || '—'}</dd></dl><blockquote>{selectedItem.text}</blockquote></>}</aside>}
  </div>
}
