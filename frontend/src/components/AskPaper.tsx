import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import {
  askQuestion,
  cancelAnswer,
  createConversation,
  deleteConversation,
  downloadConversationArtifact,
  evidence,
  getConversation,
  listConversations,
  listTasks,
  retryAnswer,
  updateConversationTitle,
} from '../api/paperApi'
import type { AskLanguage, AskMessage, Conversation, EvidenceItem, TaskStatusResponse } from '../types/api'
import { useMessageStream } from '../hooks/useMessageStream'
import type { AskHandoff } from './SearchDocument'

type ConversationAction = '' | 'delete' | 'markdown' | 'json'

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function AskPaper({ initialTaskId, handoff, onOpenReport }:{initialTaskId?:string|null;handoff?:AskHandoff|null;onOpenReport:(taskId:string,section?:string|null)=>void}) {
  const [tasks,setTasks]=useState<TaskStatusResponse[]>([]), [taskId,setTaskId]=useState(initialTaskId||'')
  const [conversations,setConversations]=useState<Conversation[]>([]), [conversationId,setConversationId]=useState('')
  const [conversationSearch,setConversationSearch]=useState('')
  const [messages,setMessages]=useState<AskMessage[]>([]), [question,setQuestion]=useState(''), [language,setLanguage]=useState<AskLanguage>('auto')
  const [section,setSection]=useState('')
  const [pageStart,setPageStart]=useState(''), [pageEnd,setPageEnd]=useState('')
  const [editingTitle,setEditingTitle]=useState(false), [titleDraft,setTitleDraft]=useState('')
  const [error,setError]=useState(''), [busy,setBusy]=useState(false), [action,setAction]=useState<ConversationAction>('')
  const [drawer,setDrawer]=useState<EvidenceItem|null>(null)

  useEffect(()=>{
    let active=true
    listTasks(100,0,'','completed').then(x=>{
      if(!active)return
      setTasks(x.items)
      setTaskId(current=>current||initialTaskId||x.items[0]?.task_id||'')
    }).catch(e=>{if(active)setError(errorMessage(e))})
    return()=>{active=false}
  },[initialTaskId])

  useEffect(()=>{if(initialTaskId)setTaskId(initialTaskId)},[initialTaskId])

  useEffect(()=>{
    if(!handoff)return
    setTaskId(handoff.taskId)
    setQuestion(handoff.query)
    setSection('');setPageStart('');setPageEnd('');setDrawer(null);setError('')
  },[handoff])

  useEffect(()=>{
    let active=true
    setEditingTitle(false)
    if(!taskId){setConversations([]);setConversationId('');setMessages([]);return()=>{active=false}}
    const timeout=window.setTimeout(()=>{
      listConversations(taskId,conversationSearch).then(x=>{
        if(!active)return
        setConversations(x.items)
        setConversationId(current=>x.items.some(item=>item.id===current)?current:(x.items[0]?.id||''))
      }).catch(e=>{if(active)setError(errorMessage(e))})
    },200)
    return()=>{active=false;window.clearTimeout(timeout)}
  },[taskId,conversationSearch])

  const refresh=useCallback(async(id:string)=>{
    const x=await getConversation(id)
    setMessages(x.messages)
    setConversations(items=>items.map(c=>c.id===id?{...c,title:x.title,updated_at:x.updated_at}:c))
  },[])

  useEffect(()=>{
    if(conversationId)void refresh(conversationId).catch(e=>setError(errorMessage(e)))
    else setMessages([])
  },[conversationId,refresh])

  const {consume,stop}=useMessageStream({
    onToken:(mid,token)=>setMessages(ms=>ms.map(m=>m.id===mid?{...m,content:m.content+token}:m)),
    onTerminal:async(cid)=>{await refresh(cid);setBusy(false);setError('')},
    onError:()=>setError('Connection interrupted. Reconnecting…'),
  })
  const generatingId=messages.find(m=>m.status==='generating')?.id
  useEffect(()=>{
    if(generatingId&&conversationId){setBusy(true);consume(conversationId,generatingId)}
    else stop()
    return stop
  },[generatingId,conversationId,consume,stop])

  const currentConversation=conversations.find(c=>c.id===conversationId)
  const totalPages=taskPageCount(tasks.find(t=>t.task_id===taskId))
  const pageRangeError=validatePageRange(pageStart,pageEnd,totalPages)
  const actionsDisabled=!conversationId||busy||Boolean(generatingId)||Boolean(action)

  async function newConversation(){
    if(!taskId)return
    setError('')
    try{
      const created=await createConversation(taskId,language)
      setConversationSearch('')
      setConversations(items=>[created,...items])
      setConversationId(created.id)
      setMessages([])
      setDrawer(null)
    }catch(e){setError(errorMessage(e))}
  }

  async function submit(e:FormEvent){
    e.preventDefault()
    if(!question.trim()||!conversationId)return
    setBusy(true);setError('')
    const text=question
    setQuestion('')
    if(pageRangeError){setBusy(false);setQuestion(text);setError(pageRangeError);return}
    const start=pageStart?Number(pageStart):null, end=pageEnd?Number(pageEnd):null
    try{await askQuestion(conversationId,text,section||null,language,start,end);await refresh(conversationId)}
    catch(e){setBusy(false);setError(errorMessage(e))}
  }

  async function showEvidence(id:string){
    try{setDrawer(await evidence(taskId,id))}catch(e){setError(errorMessage(e))}
  }

  function beginRename(){setTitleDraft(currentConversation?.title||'');setEditingTitle(true)}

  async function saveTitle(){
    const title=titleDraft.trim()
    if(!conversationId||!title)return
    try{
      const updated=await updateConversationTitle(conversationId,title)
      setConversations(items=>items.map(c=>c.id===updated.id?updated:c))
      setEditingTitle(false)
    }catch(e){setError(errorMessage(e))}
  }

  async function exportConversation(format:'markdown'|'json'){
    if(!conversationId||generatingId)return
    setAction(format);setError('')
    try{await downloadConversationArtifact(conversationId,format)}
    catch(e){setError(errorMessage(e))}
    finally{setAction('')}
  }

  async function removeConversation(){
    if(!conversationId||generatingId||!currentConversation)return
    if(!window.confirm(`Permanently delete “${currentConversation.title}”? This cannot be undone.`))return
    const removedId=conversationId
    const oldIndex=conversations.findIndex(item=>item.id===removedId)
    const preferredId=conversations[oldIndex+1]?.id||conversations[oldIndex-1]?.id||''
    setAction('delete');setError('')
    try{
      await deleteConversation(removedId)
      setDrawer(null)
      const refreshed=await listConversations(taskId,conversationSearch)
      setConversations(refreshed.items)
      const nextId=refreshed.items.some(item=>item.id===preferredId)?preferredId:(refreshed.items[0]?.id||'')
      setConversationId(nextId)
      if(!nextId)setMessages([])
    }catch(e){setError(errorMessage(e))}
    finally{setAction('')}
  }

  return <section className="ask-layout">
    <aside className="panel ask-sidebar">
      <h2>Ask Paper</h2>
      <label>Completed paper<select data-testid="paper-select" className="paper-select" title={tasks.find(t=>t.task_id===taskId)?.paper_title||taskId} value={taskId} onChange={e=>{setTaskId(e.target.value);setSection('');setPageStart('');setPageEnd('');setDrawer(null)}}><option value="">Select a paper</option>{tasks.map(t=><option title={t.paper_title||t.task_id} key={t.task_id} value={t.task_id}>{t.paper_title||t.task_id}</option>)}</select></label>
      <button onClick={newConversation} disabled={!taskId}>+ New conversation</button>
      <label className="conversation-search">Search conversations<input aria-label="Search conversations" type="search" value={conversationSearch} onChange={e=>setConversationSearch(e.target.value)} placeholder="Title or message text" disabled={!taskId}/></label>
      <div data-testid="conversation-list" className="conversation-list">
        {conversations.map(c=><button title={c.title} className={c.id===conversationId?'active':''} onClick={()=>setConversationId(c.id)} key={c.id}>{c.title}</button>)}
        {taskId&&!conversations.length&&<div className="conversation-empty">{conversationSearch.trim()?`No conversations match “${conversationSearch.trim()}”.`:'No conversations yet.'}</div>}
      </div>
    </aside>
    <div className="panel chat-panel">
      <header>
        <div className="conversation-heading">
          <small>Evidence-grounded conversation</small>
          {editingTitle?<div className="title-editor"><input aria-label="Conversation title" maxLength={200} value={titleDraft} onChange={e=>setTitleDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')void saveTitle();if(e.key==='Escape')setEditingTitle(false)}} autoFocus/><button onClick={()=>void saveTitle()} disabled={!titleDraft.trim()}>Save</button><button onClick={()=>setEditingTitle(false)}>Cancel</button></div>:<div className="title-display"><h2>{currentConversation?.title||'Start a conversation'}</h2>{conversationId&&<button aria-label="Rename conversation" onClick={beginRename}>Rename</button>}</div>}
        </div>
        <div className="conversation-actions">
          {conversationId&&<><button onClick={()=>void exportConversation('markdown')} disabled={actionsDisabled}>{action==='markdown'?'Downloading…':'Markdown'}</button><button onClick={()=>void exportConversation('json')} disabled={actionsDisabled}>{action==='json'?'Downloading…':'JSON'}</button><button className="danger" onClick={()=>void removeConversation()} disabled={actionsDisabled}>{action==='delete'?'Deleting…':'Delete'}</button></>}
          {taskId&&<button onClick={()=>onOpenReport(taskId)}>View report</button>}
        </div>
      </header>
      <div data-testid="message-list" className="message-list">{!messages.length&&<div className="chat-empty"><strong>Ask a question about this paper</strong><span>Answers are grounded in retrieved passages and remain available after refresh.</span></div>}{messages.map(m=><article data-message-id={m.id} key={m.id} className={`chat-message ${m.role}`}><small>{m.role==='user'?'You':'Paper assistant'}</small>{(m.section||m.page_start)&&<span className="message-scope">{[m.section,m.page_start?`Pages ${m.page_start}–${m.page_end}`:''].filter(Boolean).join(' · ')}</span>}<p>{m.content||(m.status==='generating'?'Thinking…':'')}</p>{m.citation_ids.length>0&&<div className="evidence-tags">{m.citation_ids.map(id=><button aria-label={`Evidence ${id}`} key={id} onClick={()=>showEvidence(id)}>{id.split(':').pop()}</button>)}</div>}{m.error&&<span className="message-error">{m.error}</span>}{m.status==='generating'&&<button onClick={async()=>{await cancelAnswer(conversationId,m.id);await refresh(conversationId)}}>Cancel</button>}{['failed','canceled'].includes(m.status)&&<button onClick={async()=>{await retryAnswer(conversationId,m.id);await refresh(conversationId)}}>Retry</button>}</article>)}</div>
      <form className="ask-composer" onSubmit={submit}><textarea aria-label="Question" value={question} onChange={e=>setQuestion(e.target.value)} placeholder="Ask about methods, findings, limitations…"/><div><select aria-label="Paper section" value={section} onChange={e=>setSection(e.target.value)}><option value="">Whole paper</option>{sectionNames(tasks.find(t=>t.task_id===taskId)).map(name=><option key={name}>{name}</option>)}</select><div className="page-range" aria-label="Page range"><input aria-label="From page" type="number" min="1" max={totalPages||undefined} step="1" value={pageStart} onChange={e=>setPageStart(e.target.value)} placeholder="From page"/><span>to</span><input aria-label="To page" type="number" min="1" max={totalPages||undefined} step="1" value={pageEnd} onChange={e=>setPageEnd(e.target.value)} placeholder={totalPages?`To ${totalPages}`:'To page'}/></div><select aria-label="Answer language" value={language} onChange={e=>setLanguage(e.target.value as AskLanguage)}><option value="auto">Auto language</option><option value="zh">中文</option><option value="en">English</option></select><button className="primary-button" disabled={busy||!conversationId||!question.trim()||Boolean(pageRangeError)}>Ask</button></div>{pageRangeError&&<small className="scope-error">{pageRangeError}</small>}</form>
      {error&&<div className="request-error">{error}</div>}
      {drawer&&<aside role="dialog" aria-label={`Evidence ${drawer.evidence_id}`} className="evidence-drawer"><header><strong>{drawer.evidence_id}</strong><button aria-label="Close evidence" onClick={()=>setDrawer(null)}>×</button></header><dl><dt>Section</dt><dd>{drawer.section||'—'}</dd><dt>Pages</dt><dd>{drawer.page_start||'—'}–{drawer.page_end||'—'}</dd></dl><blockquote>{drawer.text}</blockquote>{drawer.section&&<button onClick={()=>onOpenReport(taskId,drawer.section)}>View report section</button>}</aside>}
    </div>
  </section>
}

function sectionNames(task:TaskStatusResponse|undefined):string[]{
  const raw=task?.metadata.paper_sections
  if(!Array.isArray(raw))return []
  return [...new Set(raw.map(item=>typeof item==='string'?item:(item&&typeof item==='object'&&'name' in item?String(item.name):'')).filter(Boolean))]
}

function taskPageCount(task:TaskStatusResponse|undefined):number{
  const value=task?.metadata.num_pages
  return typeof value==='number'&&Number.isInteger(value)&&value>0?value:0
}

function validatePageRange(startValue:string,endValue:string,totalPages:number):string{
  if(!startValue&&!endValue)return ''
  if(!startValue||!endValue)return 'Enter both the first and last page, or clear both.'
  const start=Number(startValue),end=Number(endValue)
  if(!Number.isInteger(start)||!Number.isInteger(end)||start<1||end<1)return 'Pages must be positive whole numbers.'
  if(start>end)return 'The first page cannot be after the last page.'
  if(totalPages&&end>totalPages)return `This paper has ${totalPages} pages.`
  return ''
}
