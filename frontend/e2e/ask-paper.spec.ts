import { expect, test, type Page } from '@playwright/test'

const now = '2026-07-12T00:00:00Z'
const task = {
  task_id: 'task-1', status: 'completed', message: 'done', created_at: now, updated_at: now,
  completed_at: now, paper_title: 'Reliable Paper', paper_id: 'paper-1', report_path: '/report.md',
  state_json_path: '/state.json', error_message: null, progress: 100, current_step: 'export',
  attempt_count: 1, last_checkpoint_step: 'export', last_event_id: 1,
  metadata: { paper_sections: ['Methods', 'Results'] },
}
const baseMessage = { conversation_id: 'conv-1', language: 'en', section: 'Methods', citation_ids: [], error: null, retry_of: null, created_at: now, updated_at: now }

async function installFixtures(page: Page) {
  let title = 'Methods discussion'
  let conversations = [
    { id: 'conv-1', task_id: 'task-1', title, language: 'en', created_at: now, updated_at: now },
    { id: 'conv-2', task_id: 'task-1', title: 'Limitations chat', language: 'auto', created_at: now, updated_at: '2026-07-11T00:00:00Z' },
  ]
  let messages = [
    { ...baseMessage, id: 'user-old', role: 'user', content: 'Summarize it', status: 'completed' },
    { ...baseMessage, id: 'answer-old', role: 'assistant', content: 'The method is robust.', status: 'completed', citation_ids: ['ev:1'] },
  ]
  await page.route(/^https?:\/\/[^/]+\/api\//, async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/api/tasks') return json({ items: [task], total: 1, limit: 100, offset: 0 })
    if (path === '/api/tasks/task-1/conversations') {
      if (request.method() === 'POST') return json({ id: 'conv-new', task_id: 'task-1', title: 'New conversation', language: 'auto', created_at: now, updated_at: now }, 201)
      const search = (url.searchParams.get('search') || '').trim().toLowerCase()
      const items = conversations.filter(conversation => {
        if (!search) return true
        const content = conversation.id === 'conv-1' ? messages.map(message => message.content).join(' ') : ''
        return `${conversation.title} ${content}`.toLowerCase().includes(search)
      })
      return json({ items })
    }
    if (path === '/api/conversations/conv-1' && request.method() === 'PATCH') {
      title = (request.postDataJSON() as { title: string }).title
      conversations = conversations.map(conversation => conversation.id === 'conv-1' ? { ...conversation, title } : conversation)
      return json({ id: 'conv-1', task_id: 'task-1', title, language: 'en', created_at: now, updated_at: now })
    }
    if (path === '/api/conversations/conv-1' && request.method() === 'GET') return json({ id: 'conv-1', task_id: 'task-1', title, language: 'en', created_at: now, updated_at: now, messages, total: messages.length, limit: 50, offset: 0 })
    if (path === '/api/conversations/conv-2' && request.method() === 'GET') return json({ ...conversations.find(item => item.id === 'conv-2'), messages: [], total: 0, limit: 50, offset: 0 })
    if (path.startsWith('/api/conversations/') && request.method() === 'DELETE') {
      conversations = conversations.filter(conversation => `/api/conversations/${conversation.id}` !== path)
      return route.fulfill({ status: 204, body: '' })
    }
    if (path.match(/^\/api\/conversations\/conv-[12]\/artifacts\/(markdown|json)$/)) {
      const format = path.endsWith('/markdown') ? 'markdown' : 'json'
      return route.fulfill({
        status: 200,
        contentType: format === 'markdown' ? 'text/markdown; charset=utf-8' : 'application/json; charset=utf-8',
        headers: { 'Content-Disposition': `attachment; filename="ask-paper-${path.split('/')[3]}.${format === 'markdown' ? 'md' : 'json'}"` },
        body: format === 'markdown' ? '# Conversation' : '{"schema_version":"ask-paper-conversation-v1"}',
      })
    }
    if (path === '/api/conversations/conv-1/messages' && request.method() === 'POST') {
      messages = [...messages,
        { ...baseMessage, id: 'user-new', role: 'user', content: request.postDataJSON().content, status: 'completed' },
        { ...baseMessage, id: 'answer-new', role: 'assistant', content: '', status: 'generating' },
      ]
      return json({ user_message_id: 'user-new', assistant_message_id: 'answer-new', status: 'generating' }, 202)
    }
    if (path.endsWith('/answer-new/events')) {
      await new Promise(resolve => setTimeout(resolve, 750))
      const after = Number(url.searchParams.get('after') || 0)
      const events = [
        { id: 1, event: 'token', data: { token: 'Alpha ' } },
        { id: 1, event: 'token', data: { token: 'duplicate' } },
        { id: 2, event: 'token', data: { token: 'answer' } },
        { id: 3, event: 'completed', data: {} },
      ].filter(event => event.id > after).map(event => `id: ${event.id}\nevent: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`).join('')
      messages = messages.map(message => message.id === 'answer-new' ? { ...message, content: 'Alpha answer', status: 'completed', citation_ids: ['ev:1'] } : message)
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: events })
    }
    if (path === '/api/tasks/task-1/evidence/ev%3A1' || path === '/api/tasks/task-1/evidence/ev:1') return json({ task_id: 'task-1', evidence_id: 'ev:1', chunk_id: 'chunk-1', page_start: 2, page_end: 3, section: 'Methods', text: 'Grounding passage' })
    if (path === '/api/tasks/task-1') return json(task)
    if (path === '/api/tasks/task-1/report') return json({ task_id: 'task-1', status: 'completed', report_markdown: '# Report', report_path: '/report.md' })
    if (path === '/api/tasks/task-1/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'id: 1\nevent: completed\ndata: {}\n\n' })
    return json({ detail: `Unhandled fixture: ${request.method()} ${path}` }, 404)
  })
}

test.beforeEach(async ({ page }) => {
  const browserErrors: string[] = []
  page.on('pageerror', error => browserErrors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') browserErrors.push(message.text()) })
  page.on('response', response => { if (!response.ok()) browserErrors.push(`${response.status()} ${response.url()}`) })
  await installFixtures(page)
  const response = await page.goto('/')
  expect(response?.ok(), `Failed to load ${page.url()}`).toBeTruthy()
  await expect(page.locator('#root'), `Browser errors: ${browserErrors.join(' | ')}`).not.toBeEmpty()
  await page.getByRole('button', { name: 'Ask Paper' }).click({ timeout: 5_000 })
})

test('complete grounded question flow survives refresh and opens report', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Methods discussion' })).toBeVisible()
  await page.getByRole('button', { name: 'Rename conversation' }).click()
  await page.getByLabel('Conversation title').fill('Renamed research chat')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByRole('heading', { name: 'Renamed research chat' })).toBeVisible()

  await page.getByLabel('Question').fill('What is the finding?')
  await page.getByLabel('Paper section').selectOption('Methods')
  await page.getByLabel('Answer language').selectOption('en')
  await page.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Markdown' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'JSON', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Delete' })).toBeDisabled()
  await expect(page.getByText('Alpha answer')).toBeVisible()
  await expect(page.getByText('duplicate')).toHaveCount(0)
  await page.getByRole('button', { name: 'Evidence ev:1' }).last().click()
  await expect(page.getByRole('dialog', { name: 'Evidence ev:1' })).toContainText('Grounding passage')
  await page.getByRole('button', { name: 'Close evidence' }).click()

  await page.reload()
  await page.getByRole('button', { name: 'Ask Paper' }).click()
  await expect(page.getByText('Alpha answer')).toBeVisible()
  await page.getByRole('button', { name: 'View report' }).click()
  await expect(page.getByRole('button', { name: 'New Analysis' })).toHaveClass(/active/)
})

test('mobile selectors, scrolling, evidence and composer remain usable', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('mobile'), 'mobile-only layout check')
  await expect(page.getByTestId('paper-select')).toBeVisible()
  await expect(page.getByTestId('conversation-list')).toBeVisible()
  await page.getByRole('button', { name: 'Evidence ev:1' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.getByRole('button', { name: 'Close evidence' }).click()
  await page.getByLabel('Question').fill('Mobile question')
  await expect(page.getByRole('button', { name: 'Ask', exact: true })).toBeEnabled()
  await page.getByTestId('message-list').evaluate(element => { element.scrollTop = element.scrollHeight })
})

test('searches conversations, shows no matches, downloads, and deletes with reselection', async ({ page }) => {
  await page.getByLabel('Search conversations').fill('robust')
  await expect(page.getByRole('button', { name: 'Methods discussion' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Limitations chat' })).toHaveCount(0)

  await page.getByLabel('Search conversations').fill('nothing here')
  await expect(page.getByText('No conversations match “nothing here”.')).toBeVisible()

  await page.getByLabel('Search conversations').fill('')
  await expect(page.getByRole('button', { name: 'Methods discussion' })).toBeVisible()
  const markdownDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Markdown' }).click()
  expect((await markdownDownload).suggestedFilename()).toBe('ask-paper-conv-1.md')
  const jsonDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: 'JSON', exact: true }).click()
  expect((await jsonDownload).suggestedFilename()).toBe('ask-paper-conv-1.json')

  page.once('dialog', async dialog => {
    expect(dialog.message()).toContain('Methods discussion')
    await dialog.accept()
  })
  await page.getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByRole('heading', { name: 'Limitations chat' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Methods discussion' })).toHaveCount(0)
})
