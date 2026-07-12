import { useCallback, useEffect, useRef } from 'react'
import { messageEventsUrl } from '../api/paperApi'

type TerminalEvent = 'completed' | 'failed' | 'canceled'
interface Options {
  onToken: (messageId: string, token: string) => void
  onTerminal: (conversationId: string, messageId: string, event: TerminalEvent) => void | Promise<void>
  onError?: (error: Error) => void
  reconnectDelayMs?: number
}
interface StreamEvent { id: number | null; event: string; data: Record<string, unknown> }

export function decodeSseBlock(block: string): StreamEvent | null {
  let id: number | null = null
  let event = 'message'
  const data: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('id:')) {
      const parsed = Number(line.slice(3).trim())
      if (Number.isSafeInteger(parsed) && parsed >= 0) id = parsed
    } else if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (!data.length) return null
  try { return { id, event, data: JSON.parse(data.join('\n')) as Record<string, unknown> } }
  catch { return null }
}

export function useMessageStream({ onToken, onTerminal, onError, reconnectDelayMs = 800 }: Options) {
  const controller = useRef<AbortController | null>(null)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const generation = useRef(0)
  const callbacks = useRef({ onToken, onTerminal, onError })
  callbacks.current = { onToken, onTerminal, onError }

  const stop = useCallback(() => {
    generation.current += 1
    controller.current?.abort()
    controller.current = null
    if (retryTimer.current) clearTimeout(retryTimer.current)
    retryTimer.current = null
  }, [])

  const consume = useCallback((conversationId: string, messageId: string, initialCursor = 0) => {
    stop()
    const runId = generation.current
    let cursor = initialCursor
    const scheduleReconnect = (connect: () => Promise<void>) => {
      if (runId === generation.current) retryTimer.current = setTimeout(() => void connect(), reconnectDelayMs)
    }
    const connect = async (): Promise<void> => {
      if (runId !== generation.current) return
      const active = new AbortController()
      controller.current = active
      let terminal = false
      try {
        const response = await fetch(messageEventsUrl(conversationId, messageId, cursor), {
          signal: active.signal,
          headers: cursor ? { 'Last-Event-ID': String(cursor) } : undefined,
        })
        if (!response.ok || !response.body) throw new Error(`Stream unavailable (${response.status})`)
        const reader = response.body.getReader(), decoder = new TextDecoder()
        let buffer = ''
        while (!terminal) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
          const blocks = buffer.split('\n\n'); buffer = blocks.pop() ?? ''
          for (const block of blocks) {
            const parsed = decodeSseBlock(block)
            if (!parsed) continue
            if (parsed.id !== null) {
              if (parsed.id <= cursor) continue
              cursor = parsed.id
            }
            if (parsed.event === 'token') callbacks.current.onToken(messageId, typeof parsed.data.token === 'string' ? parsed.data.token : '')
            if (parsed.event === 'completed' || parsed.event === 'failed' || parsed.event === 'canceled') {
              terminal = true
              await callbacks.current.onTerminal(conversationId, messageId, parsed.event)
              stop()
              break
            }
          }
        }
        if (!terminal && !active.signal.aborted) scheduleReconnect(connect)
      } catch (error) {
        if (!active.signal.aborted && runId === generation.current) {
          callbacks.current.onError?.(error instanceof Error ? error : new Error(String(error)))
          scheduleReconnect(connect)
        }
      }
    }
    void connect()
  }, [reconnectDelayMs, stop])

  useEffect(() => stop, [stop])
  return { consume, stop }
}
