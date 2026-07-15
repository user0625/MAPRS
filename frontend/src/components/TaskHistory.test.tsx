import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { TaskHistory } from './TaskHistory'

const mocks = vi.hoisted(() => ({
  cancelTask: vi.fn(), deleteTask: vi.fn(), getTaskDetail: vi.fn(), listTasks: vi.fn(),
  rerunTask: vi.fn(), resumeTask: vi.fn(), retryTask: vi.fn(),
}))
vi.mock('../api/paperApi', () => mocks)
vi.mock('./InteractiveReport', () => ({ InteractiveReport: () => <div>Report</div> }))

const now = '2026-07-14T00:00:00Z'
const task = {
  task_id: 'task-1', status: 'completed' as const, message: 'done', created_at: now,
  updated_at: now, completed_at: now, paper_title: 'Completed Paper', paper_id: 'paper-1',
  report_path: '/report.md', state_json_path: '/state.json', error_message: null,
  progress: 100, current_step: null, attempt_count: 1, last_checkpoint_step: null,
  last_event_id: 1, metadata: {},
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.listTasks.mockResolvedValue({ items: [task], total: 1, limit: 3, offset: 0 })
  mocks.getTaskDetail.mockResolvedValue({
    ...task, paper_authors: [], report_markdown: '# report', report_available: true,
    state_available: true, workflow_status: 'completed', workflow_created_at: now,
    workflow_updated_at: now, workflow_completed_at: now, workflow_metadata: {
      document_parsing: { mode: 'auto', layout_version: 'pymupdf-layout-v1', layout_pages: 3,
        fallback_pages: 0, single_column_pages: 0, double_column_pages: 3,
        blocks_retained: 18, header_footer_blocks_removed: 9, dehyphenations: 3 },
    }, step_history: [],
  })
})

it('shows compact document parsing diagnostics', async () => {
  render(<TaskHistory refreshToken={0} />)
  expect(await screen.findByRole('heading', { name: 'Document parsing' })).toBeInTheDocument()
  expect(screen.getByText('0 single · 3 double')).toBeInTheDocument()
  expect(screen.getByText('18')).toBeInTheDocument()
})

it('opens a completed task in document search', async () => {
  const onSearchDocument = vi.fn()
  render(<TaskHistory refreshToken={0} onSearchDocument={onSearchDocument} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Search document' }))
  expect(onSearchDocument).toHaveBeenCalledWith('task-1')
})
