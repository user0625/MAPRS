import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { askQuestion, cancelAnswer, createConversation, evidence, getConversation, listConversations, listTasks, messageEventsUrl, retryAnswer, updateConversationTitle } from '../api/paperApi'
import type { AskLanguage, AskMessage, Conversation, EvidenceItem, TaskStatusResponse } from '../types/api'

export function AskPaper({ initialTaskId, onOpenReport }:{initialTaskId?:string|null;onOpenReport:(taskId:string,section?:string|null)=>void}) {
  const [tasks,setTasks]=useState<TaskStatusResponse[]>([]), [taskId,setTaskId]=useState(initialTaskId||'')
  const [conversations,setConversations]=useState<Conversation[]>([]), [conversationId,setConversationId]=useState('')
  const [messages,setMessages]=useState<AskMessage[]>([]), [question,setQuestion]=useState(''), [language,setLanguage]=useState<AskLanguage>('auto')
  const [section,setSection]=useState('')
  const [editingTitle,setEditingTitle]=useState(false), [titleDraft,setTitleDraft]=useState('')
  const [error,setError]=useState(''), [busy,setBusy]=useState(false), [drawer,setDrawer]=useState<EvidenceItem|null>(null)
  const abort=useRef<AbortController|null>(null)
  useEffect(()=>{listTasks(100,0,'','completed').then(x=>{setTasks(x.items);if(!taskId&&x.items[0])setTaskId(x.items[0].task_id)}).catch(e=>setError(String(e)))},[taskId])
  useEffect(()=>{setConversationId('');setMessages([]);if(taskId)listConversations(taskId).then(x=>{setConversations(x.items);if(x.items[0])setConversationId(x.items[0].id)}).catch(e=>setError(String(e)))},[taskId])
  const refresh=useCallback(async(id:string)=>{const x=await getConversation(id);setMessages(x.messages);setConversations(items=>items.map(c=>c.id===id?{...c,title:x.title,updated_at:x.updated_at}:c))},[])
  useEffect(()=>{if(conversationId)void refresh(conversationId)},[conversationId,refresh])

  const consume=useCallback(async(cid:string,mid:string,after=0)=>{
    abort.current?.abort(); const controller=new AbortController();abort.current=controller
    let cursor=after
    try {
      const response=await fetch(messageEventsUrl(cid,mid,after),{signal:controller.signal});if(!response.ok||!response.body)throw new Error('Stream unavailable')
      const reader=response.body.getReader(), decoder=new TextDecoder();let buffer=''
      while(true){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});const blocks=buffer.split('\n\n');buffer=blocks.pop()||''
        for(const block of blocks){const id=block.match(/^id: (\d+)/m);if(id&&+id[1]<=cursor)continue;if(id)cursor=+id[1];const event=block.match(/^event: (.+)$/m)?.[1];const raw=block.match(/^data: (.+)$/m)?.[1];if(!raw)continue;const data=JSON.parse(raw)
          if(event==='token')setMessages(ms=>ms.map(m=>m.id===mid?{...m,content:m.content+(data.token||'')}:m))
          if(['completed','failed','canceled'].includes(event||'')){await refresh(cid);setBusy(false)}
        }}
    } catch{if(!controller.signal.aborted){setTimeout(()=>void consume(cid,mid,cursor),800)}}
  },[refresh])
  const generatingId=messages.find(m=>m.status==='generating')?.id
  useEffect(()=>{if(generatingId&&conversationId){setBusy(true);void consume(conversationId,generatingId)}return()=>abort.current?.abort()},[generatingId,conversationId,consume])
  async function newConversation(){if(!taskId)return;const c=await createConversation(taskId,language);setConversations(x=>[c,...x]);setConversationId(c.id);setMessages([])}
  async function submit(e:FormEvent){e.preventDefault();if(!question.trim()||!conversationId)return;setBusy(true);setError('');const text=question;setQuestion('');try{const accepted=await askQuestion(conversationId,text,section||null,language);await refresh(conversationId);void consume(conversationId,accepted.assistant_message_id)}catch(e){setBusy(false);setError(e instanceof Error?e.message:String(e))}}
  async function showEvidence(id:string){try{setDrawer(await evidence(taskId,id))}catch(e){setError(String(e))}}
  function beginRename(){setTitleDraft(conversations.find(c=>c.id===conversationId)?.title||'');setEditingTitle(true)}
  async function saveTitle(){const title=titleDraft.trim();if(!conversationId||!title)return;try{const updated=await updateConversationTitle(conversationId,title);setConversations(items=>items.map(c=>c.id===updated.id?updated:c));setEditingTitle(false)}catch(e){setError(e instanceof Error?e.message:String(e))}}
  return <section className="ask-layout">
    <aside className="panel ask-sidebar"><h2>Ask Paper</h2><label>Completed paper<select className="paper-select" title={tasks.find(t=>t.task_id===taskId)?.paper_title||taskId} value={taskId} onChange={e=>setTaskId(e.target.value)}><option value="">Select a paper</option>{tasks.map(t=><option title={t.paper_title||t.task_id} key={t.task_id} value={t.task_id}>{t.paper_title||t.task_id}</option>)}</select></label><button onClick={newConversation} disabled={!taskId}>+ New conversation</button><div className="conversation-list">{conversations.map(c=><button title={c.title} className={c.id===conversationId?'active':''} onClick={()=>setConversationId(c.id)} key={c.id}>{c.title}</button>)}</div></aside>
    <div className="panel chat-panel"><header><div className="conversation-heading"><small>Evidence-grounded conversation</small>{editingTitle?<div className="title-editor"><input aria-label="Conversation title" maxLength={200} value={titleDraft} onChange={e=>setTitleDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')void saveTitle();if(e.key==='Escape')setEditingTitle(false)}} autoFocus/><button onClick={()=>void saveTitle()} disabled={!titleDraft.trim()}>Save</button><button onClick={()=>setEditingTitle(false)}>Cancel</button></div>:<div className="title-display"><h2>{conversations.find(c=>c.id===conversationId)?.title||'Start a conversation'}</h2>{conversationId&&<button aria-label="Rename conversation" onClick={beginRename}>Rename</button>}</div>}</div>{taskId&&<button onClick={()=>onOpenReport(taskId)}>View report</button>}</header>
      <div className="message-list">{!messages.length&&<div className="chat-empty"><strong>Ask a question about this paper</strong><span>Answers are grounded in retrieved passages and remain available after refresh.</span></div>}{messages.map(m=><article key={m.id} className={`chat-message ${m.role}`}><small>{m.role==='user'?'You':'Paper assistant'}</small><p>{m.content|| (m.status==='generating'?'Thinking…':'')}</p>{m.citation_ids.length>0&&<div className="evidence-tags">{m.citation_ids.map(id=><button key={id} onClick={()=>showEvidence(id)}>{id.split(':').pop()}</button>)}</div>}{m.error&&<span className="message-error">{m.error}</span>}{m.status==='generating'&&<button onClick={()=>cancelAnswer(conversationId,m.id)}>Cancel</button>}{['failed','canceled'].includes(m.status)&&<button onClick={async()=>{const x=await retryAnswer(conversationId,m.id);await refresh(conversationId);void consume(conversationId,x.assistant_message_id)}}>Retry</button>}</article>)}</div>
      <form className="ask-composer" onSubmit={submit}><textarea aria-label="Question" value={question} onChange={e=>setQuestion(e.target.value)} placeholder="Ask about methods, findings, limitations…"/><div><select aria-label="Paper section" value={section} onChange={e=>setSection(e.target.value)}><option value="">Whole paper</option>{sectionNames(tasks.find(t=>t.task_id===taskId)).map(name=><option key={name}>{name}</option>)}</select><select aria-label="Answer language" value={language} onChange={e=>setLanguage(e.target.value as AskLanguage)}><option value="auto">Auto language</option><option value="zh">中文</option><option value="en">English</option></select><button className="primary-button" disabled={busy||!conversationId||!question.trim()}>Ask</button></div></form>{error&&<div className="request-error">{error}</div>}
      {drawer&&<aside className="evidence-drawer"><header><strong>{drawer.evidence_id}</strong><button onClick={()=>setDrawer(null)}>×</button></header><dl><dt>Section</dt><dd>{drawer.section||'—'}</dd><dt>Pages</dt><dd>{drawer.page_start||'—'}–{drawer.page_end||'—'}</dd></dl><blockquote>{drawer.text}</blockquote>{drawer.section&&<button onClick={()=>onOpenReport(taskId,drawer.section)}>View report section</button>}</aside>}
    </div></section>
}

function sectionNames(task:TaskStatusResponse|undefined):string[]{
  const raw=task?.metadata.paper_sections
  if(!Array.isArray(raw))return []
  return [...new Set(raw.map(item=>typeof item==='string'?item:(item&&typeof item==='object'&&'name' in item?String(item.name):'')).filter(Boolean))]
}
