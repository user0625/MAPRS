import { expect, test, type Page } from '@playwright/test'

const now='2026-07-13T00:00:00Z'
const tasks=[0,1,2].map(index=>({task_id:`task-${index}`,status:'completed',message:'done',created_at:now,updated_at:now,completed_at:now,paper_title:`Paper ${index}`,paper_id:`paper-${index}`,report_path:'/report.md',state_json_path:'/state.json',error_message:null,progress:100,current_step:'export',attempt_count:1,last_checkpoint_step:'export',last_event_id:2,metadata:{}}))
const comparison={id:'cmp-e2e',title:'Evidence comparison',focus:'methods and limitations',language:'en',status:'completed',progress:100,current_step:'export',message:'done',error_message:null,retry_of:null,report_available:true,structured_available:true,artifact_formats:['markdown','json','html','pdf','docx'],last_event_id:4,created_at:now,updated_at:now,completed_at:now,papers:tasks.slice(0,2).map((task,position)=>({source_task_id:task.task_id,paper_id:task.paper_id,title:task.paper_title,authors:[],year:2025+position,position}))}
const report={schema_version:'paper-comparison-v1',comparison_id:'cmp-e2e',title:comparison.title,focus:comparison.focus,language:'en',source_papers:comparison.papers.map(({position:_,...paper})=>paper),profiles:[],matrix:[{dimension:'method',cells:comparison.papers.map((paper,index)=>({source_task_id:paper.source_task_id,summary:`Grounded method ${index}`,evidence_ids:[`cmp-e2e:ev:0${index+1}:01`]}))},{dimension:'limitations',cells:comparison.papers.map((paper,index)=>({source_task_id:paper.source_task_id,summary:`Limitation ${index}`,evidence_ids:[`cmp-e2e:ev:0${index+1}:01`]}))}],synthesis:{differences:{content:'The methods differ.',evidence_ids:['cmp-e2e:ev:01:01']}},claims:[],evidence_ids:['cmp-e2e:ev:01:01','cmp-e2e:ev:02:01'],quality_warnings:[]}

async function fixtures(page:Page){
  await page.route(/^https?:\/\/[^/]+\/api\//,async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname
    const json=(body:unknown,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)})
    if(path==='/api/tasks')return json({items:tasks,total:tasks.length,limit:100,offset:0})
    if(path==='/api/comparisons'&&request.method()==='POST')return json(comparison,202)
    if(path==='/api/comparisons')return json({items:[],total:0,limit:100,offset:0})
    if(path==='/api/comparisons/cmp-e2e/report/structured')return json(report)
    if(path.includes('/api/comparisons/cmp-e2e/evidence/'))return json({comparison_id:'cmp-e2e',evidence_id:'cmp-e2e:ev:01:01',source_task_id:'task-0',paper_id:'paper-0',paper_title:'Paper 0',chunk_id:'chunk-0',page_start:2,page_end:3,section:'Methods',text:'Full source evidence.',score:0.9})
    if(path==='/api/comparisons/cmp-e2e')return json(comparison)
    return json({detail:`Unhandled ${request.method()} ${path}`},404)
  })
}

test.beforeEach(async({page})=>{await fixtures(page);await page.goto('/');await page.getByRole('button',{name:'Compare Papers'}).click()})

test('creates a comparison, renders matrix, and opens source evidence',async({page})=>{
  await page.getByRole('checkbox',{name:/Paper 0/}).check()
  await page.getByRole('checkbox',{name:/Paper 1/}).check()
  await page.getByLabel('Comparison title',{exact:false}).fill('Evidence comparison')
  await page.getByLabel('Focus').fill('methods and limitations')
  await page.getByLabel('Output language').selectOption('en')
  await page.getByRole('button',{name:'Create comparison'}).click()
  await expect(page.getByRole('table')).toContainText('Grounded method 0')
  await expect(page.locator('.comparison-actions').getByRole('link')).toHaveCount(5)
  await page.getByRole('button',{name:'01:01'}).first().click()
  await expect(page.getByLabel('Comparison evidence')).toContainText('Full source evidence.')
  await expect(page.getByLabel('Comparison evidence')).toContainText('task-0')
})

test('mobile matrix remains horizontally scrollable',async({page},testInfo)=>{
  test.skip(!testInfo.project.name.startsWith('mobile'),'mobile-only layout check')
  await page.getByRole('checkbox',{name:/Paper 0/}).check()
  await page.getByRole('checkbox',{name:/Paper 1/}).check()
  await page.getByRole('button',{name:'Create comparison'}).click()
  const wrapper=page.locator('.comparison-matrix-wrap')
  await expect(wrapper).toBeVisible()
  expect(await wrapper.evaluate(element=>element.scrollWidth>element.clientWidth)).toBeTruthy()
})
