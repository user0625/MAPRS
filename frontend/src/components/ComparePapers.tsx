import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  cancelComparison, comparisonArtifactUrl, createComparison, deleteComparison,
  comparisonEventsUrl,
  getComparison, getComparisonEvidence, getComparisonStructured, listComparisons,
  listTasks, retryComparison,
} from '../api/paperApi'
import type {
  ComparisonEvidence, ComparisonResponse, ComparisonStructuredReport,
  OutputLanguage, TaskStatusResponse,
} from '../types/api'

const FORMATS = ['markdown','json','html','pdf','docx'] as const
const DEFAULT_FOCUS = '方法、实验结果与局限的综合比较'

function errorText(error:unknown) { return error instanceof Error ? error.message : 'Unable to continue.' }

export function ComparePapers() {
  const [tasks,setTasks]=useState<TaskStatusResponse[]>([])
  const [taskSearch,setTaskSearch]=useState('')
  const [selected,setSelected]=useState<string[]>([])
  const [title,setTitle]=useState('')
  const [focus,setFocus]=useState(DEFAULT_FOCUS)
  const [language,setLanguage]=useState<OutputLanguage>('zh')
  const [history,setHistory]=useState<ComparisonResponse[]>([])
  const [historySearch,setHistorySearch]=useState('')
  const [active,setActive]=useState<ComparisonResponse|null>(null)
  const [report,setReport]=useState<ComparisonStructuredReport|null>(null)
  const [evidence,setEvidence]=useState<ComparisonEvidence|null>(null)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState<string|null>(null)

  const refreshHistory=useCallback(async(search=historySearch) => {
    const response=await listComparisons(search)
    setHistory(response.items)
  },[historySearch])

  useEffect(()=>{ void listTasks(100,0,'','completed').then(value=>setTasks(value.items)).catch(error=>setError(errorText(error))) },[])
  useEffect(()=>{ const timer=window.setTimeout(()=>void refreshHistory().catch(error=>setError(errorText(error))),180);return()=>window.clearTimeout(timer) },[refreshHistory])
  useEffect(()=>{
    if(!active || !['pending','running'].includes(active.status))return
    const poll=window.setInterval(()=>void getComparison(active.id).then(value=>{setActive(value);void refreshHistory()}).catch(error=>setError(errorText(error))),1500)
    return()=>window.clearInterval(poll)
  },[active,refreshHistory])
  useEffect(()=>{
    if(!active || !['pending','running'].includes(active.status) || typeof EventSource==='undefined')return
    const stream=new EventSource(comparisonEventsUrl(active.id,active.last_event_id))
    const refresh=()=>void getComparison(active.id).then(value=>{setActive(value);void refreshHistory()}).catch(error=>setError(errorText(error)))
    const eventTypes=['progress','completed','failed','canceled']
    eventTypes.forEach(type=>stream.addEventListener(type,refresh))
    stream.onerror=()=>stream.close()
    return()=>{eventTypes.forEach(type=>stream.removeEventListener(type,refresh));stream.close()}
  },[active,refreshHistory])
  useEffect(()=>{
    if(active?.status==='completed' && active.structured_available && report?.comparison_id!==active.id)
      void getComparisonStructured(active.id).then(setReport).catch(error=>setError(errorText(error)))
  },[active,report])

  const visibleTasks=useMemo(()=>{
    const query=taskSearch.trim().toLocaleLowerCase()
    return query ? tasks.filter(task=>(task.paper_title||task.task_id).toLocaleLowerCase().includes(query)) : tasks
  },[tasks,taskSearch])

  const toggle=(taskId:string)=>setSelected(current=>current.includes(taskId)?current.filter(id=>id!==taskId):current.length<5?[...current,taskId]:current)
  const perform=async(action:()=>Promise<void>)=>{setBusy(true);setError(null);try{await action()}catch(error){setError(errorText(error))}finally{setBusy(false)}}
  const submit=()=>perform(async()=>{
    if(selected.length<2)throw new Error('Select at least two completed papers.')
    const created=await createComparison(selected,title,focus,language)
    setActive(created);setReport(null);setSelected([]);setTitle('');await refreshHistory()
  })
  const open=async(item:ComparisonResponse)=>perform(async()=>{
    const detail=await getComparison(item.id);setActive(detail);setReport(null);setEvidence(null)
    if(detail.status==='completed')setReport(await getComparisonStructured(detail.id))
  })
  const showEvidence=(id:string)=>{if(active)void getComparisonEvidence(active.id,id).then(setEvidence).catch(error=>setError(errorText(error)))}

  return <section className="compare-workspace">
    <aside className="panel comparison-builder">
      <header><div><small>New comparison</small><h2>Compare Papers</h2></div><span>{selected.length}/5</span></header>
      <label>Find completed papers<input value={taskSearch} onChange={event=>setTaskSearch(event.target.value)} placeholder="Search title or task ID" /></label>
      <div className="comparison-task-list" aria-label="Completed papers">
        {visibleTasks.map(task=><label key={task.task_id} className={selected.includes(task.task_id)?'selected':''}>
          <input type="checkbox" checked={selected.includes(task.task_id)} disabled={!selected.includes(task.task_id)&&selected.length>=5} onChange={()=>toggle(task.task_id)} />
          <span><strong>{task.paper_title||'Untitled paper'}</strong><small>{task.task_id}</small></span>
        </label>)}
        {!visibleTasks.length&&<p className="comparison-empty">No completed papers found.</p>}
      </div>
      <label>Comparison title <span className="optional">Optional</span><input value={title} maxLength={200} onChange={event=>setTitle(event.target.value)} /></label>
      <label>Focus<textarea value={focus} maxLength={4000} onChange={event=>setFocus(event.target.value)} /></label>
      <label>Output language<select value={language} onChange={event=>setLanguage(event.target.value as OutputLanguage)}><option value="zh">中文</option><option value="en">English</option></select></label>
      <button className="primary-button" disabled={busy||selected.length<2||!focus.trim()} onClick={submit}>Create comparison</button>
      {selected.length===5&&<small className="selection-note">Maximum of five papers selected.</small>}
    </aside>

    <div className="comparison-main">
      {error&&<div className="request-error" role="alert"><strong>Unable to continue</strong><span>{error}</span></div>}
      <section className="panel comparison-history">
        <header><div><small>Persistent workspace</small><h2>Comparison history</h2></div><input aria-label="Search comparisons" value={historySearch} onChange={event=>setHistorySearch(event.target.value)} placeholder="Search" /></header>
        <div>{history.map(item=><button key={item.id} className={active?.id===item.id?'active':''} onClick={()=>void open(item)}>
          <span><strong>{item.title}</strong><small>{new Date(item.created_at).toLocaleString()}</small></span><span className={`status-badge status-${item.status}`}>{item.status}</span>
        </button>)}{!history.length&&<p className="comparison-empty">No comparisons yet.</p>}</div>
      </section>

      {active?<section className="panel comparison-result">
        <header><div><small>{active.id}</small><h2>{active.title}</h2><p>{active.focus}</p></div><span className={`status-badge status-${active.status}`}>{active.status}</span></header>
        <div className="source-paper-tags">{active.papers.map(paper=><span key={paper.source_task_id}>{paper.position+1}. {paper.title}</span>)}</div>
        {['pending','running'].includes(active.status)&&<div className="comparison-progress"><div><span style={{width:`${active.progress}%`}} /></div><p>{active.message} · {active.progress}%</p><button disabled={busy} onClick={()=>perform(async()=>{setActive(await cancelComparison(active.id));await refreshHistory()})}>Cancel</button></div>}
        {active.status==='failed'&&<div className="comparison-failure"><p>{active.error_message||active.message}</p><button disabled={busy} onClick={()=>perform(async()=>{const next=await retryComparison(active.id);setActive(next);setReport(null);await refreshHistory()})}>Retry</button></div>}
        {active.status==='canceled'&&<button className="task-action" disabled={busy} onClick={()=>perform(async()=>{const next=await retryComparison(active.id);setActive(next);setReport(null);await refreshHistory()})}>Retry comparison</button>}
        {report&&<>
          <div className="comparison-actions">{FORMATS.map(format=><a key={format} href={comparisonArtifactUrl(active.id,format)}>{format.toUpperCase()}</a>)}</div>
          <div className="comparison-matrix-wrap"><table className="comparison-matrix"><thead><tr><th>Dimension</th>{report.source_papers.map(paper=><th key={paper.source_task_id}>{paper.title}</th>)}</tr></thead><tbody>{report.matrix.map(row=><tr key={row.dimension}><th>{row.dimension.replaceAll('_',' ')}</th>{row.cells.map(cell=><td key={cell.source_task_id}><p>{cell.summary}</p><div>{cell.evidence_ids.map(id=><button key={id} onClick={()=>showEvidence(id)}>{id.split(':').slice(-2).join(':')}</button>)}</div></td>)}</tr>)}</tbody></table></div>
          <article className="comparison-synthesis"><h3>Synthesis</h3>{Object.entries(report.synthesis).map(([name,section])=><section key={name}><h4>{name.replaceAll('_',' ')}</h4><p>{section.content}</p><div className="evidence-tags">{section.evidence_ids.map(id=><button key={id} onClick={()=>showEvidence(id)}>{id}</button>)}</div></section>)}{report.quality_warnings.length>0&&<details><summary>Quality warnings ({report.quality_warnings.length})</summary><ul>{report.quality_warnings.map(item=><li key={item}>{item}</li>)}</ul></details>}</article>
        </>}
        {['completed','failed','canceled'].includes(active.status)&&<button className="task-action danger" disabled={busy} onClick={()=>perform(async()=>{await deleteComparison(active.id);setActive(null);setReport(null);await refreshHistory()})}>Delete comparison</button>}
        {evidence&&<aside className="evidence-drawer" aria-label="Comparison evidence"><header><h3>Evidence</h3><button aria-label="Close evidence" onClick={()=>setEvidence(null)}>×</button></header><dl><dt>Paper</dt><dd>{evidence.paper_title}</dd><dt>Task</dt><dd>{evidence.source_task_id}</dd><dt>Chunk</dt><dd>{evidence.chunk_id}</dd><dt>Pages</dt><dd>{evidence.page_start||'—'}{evidence.page_end&&evidence.page_end!==evidence.page_start?`–${evidence.page_end}`:''}</dd><dt>Section</dt><dd>{evidence.section||'—'}</dd></dl><blockquote>{evidence.text}</blockquote></aside>}
      </section>:<section className="panel comparison-result comparison-empty"><h2>Select or create a comparison</h2><p>Choose 2–5 completed papers to build an evidence-grounded matrix.</p></section>}
    </div>
  </section>
}
