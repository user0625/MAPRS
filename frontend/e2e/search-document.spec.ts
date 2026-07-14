import { expect, test } from '@playwright/test'

const now = '2026-07-14T00:00:00Z'
const task = {
  task_id: 'task-search', status: 'completed', message: 'done', created_at: now,
  updated_at: now, completed_at: now, paper_title: 'Retrieval Paper', paper_id: 'paper-search',
  report_path: '/report.md', state_json_path: '/state.json', error_message: null,
  progress: 100, current_step: null, attempt_count: 1, last_checkpoint_step: null,
  last_event_id: 1, metadata: { num_pages: 8, paper_sections: ['Methods'] },
}

test.beforeEach(async ({ page }) => {
  await page.route(url => new URL(url).pathname.startsWith('/api/'), async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/tasks' && request.method() === 'GET') {
      await route.fulfill({ json: { items: [task], total: 1, limit: 100, offset: 0 } })
    } else if (path === '/api/tasks/task-search/search' && request.method() === 'POST') {
      await route.fulfill({ json: {
        task_id: 'task-search', query: 'target method', mode_used: 'hybrid',
        diagnostics: { actual_mode: 'hybrid', candidate_count: 2, elapsed_ms: 4.2, index_source: 'disk_hit', fallback_reason: null },
        hits: [{
          rank: 1, chunk_id: 'chunk-2', text: 'The target method improves retrieval quality.',
          section: 'Methods', page_start: 3, page_end: 3, sources: ['bm25', 'vector'],
          bm25_score: 2.1, vector_score: 0.88, hybrid_score: 0.032,
          context: [{ relation: 'before', chunk_id: 'chunk-1', text: 'The preceding setup context.', section: 'Methods', page_start: 2, page_end: 2 }],
        }],
      } })
    } else if (path === '/api/tasks/task-search/conversations' && request.method() === 'GET') {
      await route.fulfill({ json: { items: [{ id: 'conv-1', task_id: 'task-search', title: 'Existing conversation', language: 'auto', created_at: now, updated_at: now }] } })
    } else if (path === '/api/conversations/conv-1' && request.method() === 'GET') {
      await route.fulfill({ json: { id: 'conv-1', task_id: 'task-search', title: 'Existing conversation', language: 'auto', created_at: now, updated_at: now, messages: [], total: 0, limit: 50, offset: 0 } })
    } else {
      await route.fulfill({ status: 404, json: { detail: `Unhandled ${request.method()} ${path}` } })
    }
  })
})

test('searches a paper, inspects adjacent context, and prefills Ask Paper', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Search Document' }).click()
  await expect(page.getByLabel('Search paper')).toHaveValue('task-search')
  await page.getByLabel('Search query').fill('target method')
  await page.getByLabel('Search section').selectOption('Methods')
  await page.getByLabel('Search from page').fill('2')
  await page.getByLabel('Search to page').fill('4')
  await page.getByRole('button', { name: 'Search paper', exact: true }).click()

  await expect(page.getByText('1 ranked passages')).toBeVisible()
  await expect(page.locator('.search-mode-badge')).toHaveText('Hybrid')
  await expect(page.getByText('Disk cache hit')).toBeVisible()
  await page.getByRole('button', { name: 'Open passage' }).click()
  await expect(page.getByRole('dialog', { name: 'Passage chunk-2' })).toContainText('The preceding setup context.')
  await page.getByRole('button', { name: 'Close passage' }).click()
  await page.getByRole('button', { name: 'Continue in Ask Paper' }).click()

  await expect(page.getByRole('button', { name: 'Ask Paper' })).toHaveClass(/active/)
  await expect(page.getByLabel('Question')).toHaveValue('target method')
})
