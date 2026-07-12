import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { decodeSseBlock, useMessageStream } from './useMessageStream'

function stream(chunks: string[]) {
  const encoder = new TextEncoder()
  return new Response(new ReadableStream({
    start(controller) { chunks.forEach(chunk => controller.enqueue(encoder.encode(chunk))); controller.close() },
  }), { status: 200 })
}

describe('useMessageStream', () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

  it('decodes CRLF and multiline data safely', () => {
    expect(decodeSseBlock('id: 4\r\nevent: token\r\ndata: {"token":"ok"}')).toEqual({ id: 4, event: 'token', data: { token: 'ok' } })
    expect(decodeSseBlock('event: token\ndata: nope')).toBeNull()
  })

  it('deduplicates event ids and refreshes once on terminal state', async () => {
    const onToken = vi.fn(), onTerminal = vi.fn()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream([
      'id: 1\nevent: token\ndata: {"token":"A"}\n\n',
      'id: 1\nevent: token\ndata: {"token":"A"}\n\nid: 2\nevent: token\ndata: {"token":"B"}\n\n',
      'id: 3\nevent: completed\ndata: {}\n\n',
    ])))
    const { result } = renderHook(() => useMessageStream({ onToken, onTerminal, reconnectDelayMs: 1 }))
    act(() => result.current.consume('conv-1', 'msg-1'))
    await waitFor(() => expect(onTerminal).toHaveBeenCalledWith('conv-1', 'msg-1', 'completed'))
    expect(onToken.mock.calls.map(call => call[1]).join('')).toBe('AB')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('reconnects from the confirmed cursor and aborts the old conversation', async () => {
    const onToken = vi.fn(), onTerminal = vi.fn()
    const pending = new ReadableStream<Uint8Array>({ start() {} })
    const mockedFetch = vi.fn()
      .mockResolvedValueOnce(stream(['id: 7\nevent: token\ndata: {"token":"first"}\n\n']))
      .mockResolvedValueOnce(stream(['id: 7\nevent: token\ndata: {"token":"duplicate"}\n\nid: 8\nevent: completed\ndata: {}\n\n']))
      .mockResolvedValueOnce(new Response(pending))
      .mockResolvedValueOnce(stream(['id: 1\nevent: completed\ndata: {}\n\n']))
    vi.stubGlobal('fetch', mockedFetch)
    const { result } = renderHook(() => useMessageStream({ onToken, onTerminal, reconnectDelayMs: 1 }))
    act(() => result.current.consume('conv-1', 'msg-1'))
    await waitFor(() => expect(mockedFetch).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(onTerminal).toHaveBeenCalledTimes(1))
    expect(mockedFetch.mock.calls[1][0]).toContain('after=7')
    expect(mockedFetch.mock.calls[1][1].headers).toEqual({ 'Last-Event-ID': '7' })
    expect(onToken).toHaveBeenCalledTimes(1)

    act(() => result.current.consume('conv-old', 'msg-old'))
    await waitFor(() => expect(mockedFetch).toHaveBeenCalledTimes(3))
    const oldSignal = mockedFetch.mock.calls[2][1].signal as AbortSignal
    act(() => result.current.consume('conv-new', 'msg-new'))
    expect(oldSignal.aborted).toBe(true)
    await waitFor(() => expect(onTerminal).toHaveBeenCalledWith('conv-new', 'msg-new', 'completed'))
  })
})
