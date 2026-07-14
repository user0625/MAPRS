import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ComparePapers } from './ComparePapers'

const tasks = Array.from({length:6},(_,index)=>({
  task_id:`task-${index}`,status:'completed',message:'done',created_at:'2026-01-01T00:00:00Z',updated_at:'2026-01-01T00:00:00Z',completed_at:'2026-01-01T00:00:00Z',paper_title:`Paper ${index}`,paper_id:`paper-${index}`,report_path:'/report.md',state_json_path:'/state.json',error_message:null,progress:100,current_step:'export',attempt_count:1,last_checkpoint_step:'export',last_event_id:2,metadata:{},
}))

const comparison = {
  id:'cmp-1',title:'Paper comparison',focus:'methods',language:'en',status:'completed',progress:100,current_step:'export',message:'done',error_message:null,retry_of:null,report_available:true,structured_available:true,artifact_formats:['markdown','json','html','pdf','docx'],last_event_id:4,created_at:'2026-01-01T00:00:00Z',updated_at:'2026-01-01T00:00:00Z',completed_at:'2026-01-01T00:00:00Z',papers:tasks.slice(0,2).map((task,index)=>({source_task_id:task.task_id,paper_id:task.paper_id,title:task.paper_title,authors:[],year:2025,position:index})),
}

const structured = {
  schema_version:'paper-comparison-v1',comparison_id:'cmp-1',title:'Paper comparison',focus:'methods',language:'en',source_papers:comparison.papers.map(({position:_,...paper})=>paper),profiles:[],matrix:[{dimension:'method',cells:comparison.papers.map(paper=>({source_task_id:paper.source_task_id,summary:`Method for ${paper.title}`,evidence_ids:[`cmp-1:ev:0${paper.position+1}:01`]}))}],synthesis:{differences:{content:'Different methods.',evidence_ids:['cmp-1:ev:01:01']}},claims:[],evidence_ids:['cmp-1:ev:01:01','cmp-1:ev:02:01'],quality_warnings:[],
}

function response(body:unknown,status=200) { return Promise.resolve(new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}})) }

describe('ComparePapers',()=>{
  beforeEach(()=>{
    vi.stubGlobal('fetch',vi.fn((input:RequestInfo|URL,init?:RequestInit)=>{
      const url=String(input)
      if(url.includes('/api/tasks?'))return response({items:tasks,total:tasks.length,limit:100,offset:0})
      if(url.includes('/report/structured'))return response(structured)
      if(url.endsWith('/api/comparisons')&&init?.method==='POST')return response(comparison,202)
      if(url.includes('/api/comparisons?'))return response({items:[],total:0,limit:100,offset:0})
      return response(comparison)
    }))
  })
  afterEach(()=>vi.unstubAllGlobals())

  it('enforces the 2–5 selection range and renders a completed matrix',async()=>{
    const user=userEvent.setup()
    render(<ComparePapers />)
    const create=await screen.findByRole('button',{name:'Create comparison'})
    expect(create).toBeDisabled()
    const boxes=await screen.findAllByRole('checkbox')
    await user.click(boxes[0]);expect(create).toBeDisabled()
    await user.click(boxes[1]);expect(create).toBeEnabled()
    await user.selectOptions(screen.getByLabelText('Output language'),'en')
    await user.clear(screen.getByLabelText('Focus'));await user.type(screen.getByLabelText('Focus'),'methods')
    await user.click(create)
    expect(await screen.findByRole('table')).toHaveTextContent('Method for Paper 0')
    expect(screen.getAllByRole('link')).toHaveLength(5)
  })

  it('disables remaining papers after five selections',async()=>{
    const user=userEvent.setup();render(<ComparePapers />)
    const boxes=await screen.findAllByRole('checkbox')
    for(const box of boxes.slice(0,5))await user.click(box)
    await waitFor(()=>expect(boxes[5]).toBeDisabled())
    expect(screen.getByText('Maximum of five papers selected.')).toBeInTheDocument()
  })
})
