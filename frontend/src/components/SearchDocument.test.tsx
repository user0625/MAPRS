import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SearchDocument } from './SearchDocument'
import type { DocumentSearchHit, DocumentSearchResponse } from '../types/api'

const mocks = vi.hoisted(() => ({
  listTasks: vi.fn(),
  searchDocument: vi.fn(),
  writeText: vi.fn(),
}))

vi.mock('../api/paperApi', () => ({
  listTasks: mocks.listTasks,
  searchDocument: mocks.searchDocument,
}))

const now = '2026-07-14T00:00:00Z'
const task = {
  task_id: 'task-1', status: 'completed' as const, message: 'done', created_at: now,
  updated_at: now, completed_at: now, paper_title: 'Search Paper', paper_id: 'paper-1',
  report_path: '/report.md', state_json_path: '/state.json', error_message: null,
  progress: 100, current_step: null, attempt_count: 1, last_checkpoint_step: null,
  last_event_id: 1, metadata: { num_pages: 12, paper_sections: ['Methods', { name: 'Results' }] },
}

const hit = {
  rank: 1, chunk_id: 'chunk-2', text: 'The target method improves retrieval.', section: 'Methods',
  page_start: 5, page_end: 5, sources: ['bm25', 'vector'] as Array<'bm25'|'vector'>,
  bm25_score: 2.5, vector_score: 0.85, hybrid_score: 0.032,
  context: [{ relation: 'before' as const, chunk_id: 'chunk-1', text: 'Earlier context.', section: 'Methods', page_start: 4, page_end: 4 }],
}

function response(mode: 'hybrid'|'bm25'|'degraded_to_bm25' = 'hybrid', hits: DocumentSearchHit[] = [hit]): DocumentSearchResponse {
  return {
    task_id: 'task-1', query: 'target method', mode_used: mode, hits,
    diagnostics: {
      actual_mode: mode, candidate_count: hits.length, elapsed_ms: 12.34,
      index_source: mode === 'hybrid' ? 'memory_hit' as const : 'unavailable' as const,
      fallback_reason: mode === 'degraded_to_bm25' ? 'query_embedding_unavailable' as const : null,
    },
  }
}

describe('SearchDocument', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.listTasks.mockResolvedValue({ items: [task], total: 1, limit: 100, offset: 0 })
    mocks.searchDocument.mockResolvedValue(response())
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: mocks.writeText } })
    mocks.writeText.mockResolvedValue(undefined)
  })

  it('validates scope, searches, opens context, copies, and hands off to Ask Paper', async () => {
    const onContinueAsk = vi.fn()
    render(<SearchDocument initialTaskId="task-1" onContinueAsk={onContinueAsk} />)
    await waitFor(() => expect(screen.getByLabelText('Search paper')).toHaveValue('task-1'))

    fireEvent.change(screen.getByLabelText('Search query'), { target: { value: ' target method ' } })
    fireEvent.change(screen.getByLabelText('Search from page'), { target: { value: '5' } })
    expect(screen.getByText('Enter both the first and last page, or clear both.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Search paper' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Search to page'), { target: { value: '8' } })
    fireEvent.change(screen.getByLabelText('Search section'), { target: { value: 'Methods' } })
    fireEvent.change(screen.getByLabelText('Search Top K'), { target: { value: '10' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search paper' }))

    await screen.findByText('1 ranked passages')
    expect(mocks.searchDocument).toHaveBeenCalledWith('task-1', {
      query: 'target method', mode: 'auto', section: 'Methods', page_start: 5, page_end: 8, top_k: 10,
    })
    expect(screen.getAllByText('Hybrid')[0]).toBeVisible()
    expect(screen.getByText('Memory cache hit')).toBeVisible()
    expect(screen.getAllByText('BM25').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Vector').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: 'Open passage' }))
    expect(screen.getByRole('dialog', { name: 'Passage chunk-2' })).toBeVisible()
    expect(screen.getByText('Earlier context.')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Copy passage and context' }))
    await waitFor(() => expect(mocks.writeText).toHaveBeenCalledWith(expect.stringContaining('Earlier context.')))
    expect(screen.getByRole('button', { name: 'Copied' })).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Continue in Ask Paper' }))
    expect(onContinueAsk).toHaveBeenCalledWith(expect.objectContaining({ taskId: 'task-1', query: 'target method' }))
    expect(onContinueAsk.mock.calls[0][0].nonce).toEqual(expect.any(Number))
  })

  it('shows empty and degraded states without exposing upstream detail', async () => {
    mocks.searchDocument.mockResolvedValueOnce(response('degraded_to_bm25', [
      { ...hit, sources: ['bm25'], vector_score: null, hybrid_score: null },
    ])).mockResolvedValueOnce(response('bm25', []))
    render(<SearchDocument onContinueAsk={vi.fn()} />)
    await waitFor(() => expect(screen.getByLabelText('Search paper')).toHaveValue('task-1'))
    fireEvent.change(screen.getByLabelText('Search query'), { target: { value: 'target method' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search paper' }))
    await screen.findByText('Degraded to BM25')
    expect(screen.getByText(/safely used BM25/)).toBeVisible()
    expect(screen.queryByText(/upstream/i)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Search paper' }))
    await screen.findByText('No matching passages')
  })

  it('renders request failures', async () => {
    mocks.searchDocument.mockRejectedValue(new Error('Paper state is unavailable.'))
    render(<SearchDocument onContinueAsk={vi.fn()} />)
    await waitFor(() => expect(screen.getByLabelText('Search paper')).toHaveValue('task-1'))
    fireEvent.change(screen.getByLabelText('Search query'), { target: { value: 'target' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search paper' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Paper state is unavailable.')
  })
})
