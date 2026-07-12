import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { InteractiveReport } from './InteractiveReport'
import { evidence, getStructuredReport } from '../api/paperApi'
import type { StructuredReportResponse } from '../types/api'

vi.mock('../api/paperApi', () => ({
  artifactUrl: (taskId: string, format: string) => `/api/tasks/${taskId}/artifacts/${format}`,
  evidence: vi.fn(),
  getStructuredReport: vi.fn(),
}))

const structured: StructuredReportResponse = {
  task_id: 'task-1',
  report: {
    title: 'Report', paper_title: 'Paper', quality_summary: null,
    sections: [
      { title: 'Methods', content: 'Alpha method', order: 1, evidence_ids: ['ev-1'], claims: [{ text: 'MixedCase Claim', evidence_ids: ['ev-1'] }] },
      { title: 'Methods', content: 'Beta result', order: 2, evidence_ids: [], claims: [] },
      { title: 'Discussion', content: 'Gamma limits', order: 3, evidence_ids: [], claims: [] },
    ],
  },
  quality_summary: {}, evidence_index: [],
}

describe('InteractiveReport', () => {
  beforeEach(() => {
    vi.mocked(getStructuredReport).mockResolvedValue(structured)
    vi.mocked(evidence).mockResolvedValue({ task_id: 'task-1', evidence_id: 'ev-1', chunk_id: 'chunk-1', page_start: 2, page_end: 3, section: 'Methods', text: 'Source passage' })
  })

  it('keeps the full unique navigation visible during search and resets it', async () => {
    const user = userEvent.setup()
    render(<InteractiveReport taskId="task-1" markdown="# fallback" />)
    const navigation = await screen.findByRole('navigation', { name: 'Report contents' })
    expect(within(navigation).getAllByRole('button')).toHaveLength(4)
    expect(document.querySelector('#report-methods')).toBeInTheDocument()
    expect(document.querySelector('#report-methods-2')).toBeInTheDocument()

    await user.type(screen.getByRole('searchbox'), 'mixedcase')
    expect(screen.getByText('1 matching sections')).toBeInTheDocument()
    expect(within(navigation).getAllByRole('button')).toHaveLength(4)

    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(within(navigation).getAllByRole('button', { name: 'Methods' })[0]).toHaveAttribute('aria-current', 'location')
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(screen.getByText('3 sections')).toBeInTheDocument()
    expect(within(navigation).getAllByRole('button')).toHaveLength(4)
    expect(within(navigation).getByRole('button', { name: 'Overview' })).toHaveAttribute('aria-current', 'location')
  })

  it('loads evidence once, closes with Escape, and returns focus', async () => {
    const user = userEvent.setup()
    render(<InteractiveReport taskId="task-1" markdown="# fallback" />)
    const trigger = await screen.findByRole('button', { name: 'Evidence ev-1' })
    await user.click(trigger)
    expect(await screen.findByText('Source passage')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
    await user.click(trigger)
    expect(evidence).toHaveBeenCalledTimes(1)
  })

  it('falls back to markdown and retries a failed structured request', async () => {
    const user = userEvent.setup()
    vi.mocked(getStructuredReport).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(structured)
    render(<InteractiveReport taskId="task-1" markdown="# Existing markdown" />)
    expect(await screen.findByRole('heading', { name: 'Existing markdown' })).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByRole('navigation', { name: 'Report contents' })).toBeInTheDocument())
    expect(getStructuredReport).toHaveBeenCalledTimes(2)
  })

  it('clears search and evidence state when the task changes', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<InteractiveReport taskId="task-1" markdown="# first" />)
    await user.type(await screen.findByRole('searchbox'), 'alpha')
    await user.click(screen.getByRole('button', { name: 'Evidence ev-1' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()

    vi.mocked(getStructuredReport).mockResolvedValueOnce({ ...structured, task_id: 'task-2' })
    rerender(<InteractiveReport taskId="task-2" markdown="# second" />)
    await waitFor(() => expect(screen.getByRole('searchbox')).toHaveValue(''))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText('3 sections')).toBeInTheDocument()
  })
})
