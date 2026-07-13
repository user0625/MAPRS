import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AskPaper } from './AskPaper'

const mocks = vi.hoisted(() => ({
  askQuestion: vi.fn(),
  cancelAnswer: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  downloadConversationArtifact: vi.fn(),
  evidence: vi.fn(),
  getConversation: vi.fn(),
  listConversations: vi.fn(),
  listTasks: vi.fn(),
  retryAnswer: vi.fn(),
  updateConversationTitle: vi.fn(),
  consume: vi.fn(),
  stop: vi.fn(),
}))

vi.mock('../api/paperApi', () => mocks)
vi.mock('../hooks/useMessageStream', () => ({
  useMessageStream: () => ({ consume: mocks.consume, stop: mocks.stop }),
}))

const now = '2026-07-13T00:00:00Z'
const task = {
  task_id: 'task-1', status: 'completed' as const, message: 'done', created_at: now,
  updated_at: now, completed_at: now, paper_title: 'Paper', paper_id: 'paper-1',
  report_path: '/report.md', state_json_path: '/state.json', error_message: null,
  progress: 100, current_step: null, attempt_count: 1, last_checkpoint_step: null,
  last_event_id: 1, metadata: {},
}
const conversations = [
  { id: 'conv-1', task_id: 'task-1', title: 'Methods chat', language: 'en' as const, created_at: now, updated_at: now },
  { id: 'conv-2', task_id: 'task-1', title: 'Limitations chat', language: 'auto' as const, created_at: now, updated_at: now },
]

function detail(id = 'conv-1', messages: unknown[] = []) {
  const conversation = conversations.find(item => item.id === id) || conversations[0]
  return { ...conversation, messages, total: messages.length, limit: 50, offset: 0 }
}

describe('AskPaper conversation management', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.listTasks.mockResolvedValue({ items: [task], total: 1, limit: 100, offset: 0 })
    mocks.listConversations.mockResolvedValue({ items: conversations })
    mocks.getConversation.mockImplementation(async (id:string) => detail(id))
    mocks.deleteConversation.mockResolvedValue(undefined)
    mocks.downloadConversationArtifact.mockResolvedValue(undefined)
  })

  it('searches on the server, shows no matches, downloads, and reselects after delete', async () => {
    let deleted = false
    mocks.listConversations.mockImplementation(async (_taskId:string, search='') => ({
      items: deleted
        ? [conversations[1]]
        : conversations.filter(item => !search.trim() || item.title.toLowerCase().includes(search.trim().toLowerCase())),
    }))
    mocks.deleteConversation.mockImplementation(async () => { deleted = true })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<AskPaper initialTaskId="task-1" onOpenReport={vi.fn()} />)
    await screen.findByRole('heading', { name: 'Methods chat' })

    fireEvent.change(screen.getByLabelText('Search conversations'), { target: { value: 'missing' } })
    await screen.findByText('No conversations match “missing”.')
    expect(mocks.listConversations).toHaveBeenLastCalledWith('task-1', 'missing')

    fireEvent.change(screen.getByLabelText('Search conversations'), { target: { value: '' } })
    await screen.findByRole('heading', { name: 'Methods chat' })
    fireEvent.click(screen.getByRole('button', { name: 'Markdown' }))
    await waitFor(() => expect(mocks.downloadConversationArtifact).toHaveBeenCalledWith('conv-1', 'markdown'))
    fireEvent.click(screen.getByRole('button', { name: 'JSON' }))
    await waitFor(() => expect(mocks.downloadConversationArtifact).toHaveBeenCalledWith('conv-1', 'json'))

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await screen.findByRole('heading', { name: 'Limitations chat' })
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Methods chat'))
    expect(mocks.deleteConversation).toHaveBeenCalledWith('conv-1')
  })

  it('disables destructive and export actions while an answer is generating', async () => {
    mocks.getConversation.mockResolvedValue(detail('conv-1', [{
      id: 'answer-1', conversation_id: 'conv-1', role: 'assistant', content: '',
      status: 'generating', language: 'en', section: null, citation_ids: [], error: null,
      retry_of: null, created_at: now, updated_at: now,
    }]))

    render(<AskPaper initialTaskId="task-1" onOpenReport={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Markdown' })).toBeDisabled())
    expect(screen.getByRole('button', { name: 'JSON' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled()
  })
})
