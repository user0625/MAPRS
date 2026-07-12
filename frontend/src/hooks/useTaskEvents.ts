import { useEffect, useRef, useState } from 'react'
import { getTaskStatus, taskEventsUrl } from '../api/paperApi'
import type { TaskEvent, TaskStatusResponse } from '../types/api'

export function useTaskEvents(taskId: string | null) {
  const [task, setTask] = useState<TaskStatusResponse | null>(null)
  const [connection, setConnection] = useState<'idle'|'connected'|'reconnecting'|'offline'>('idle')
  const [events, setEvents] = useState<TaskEvent[]>([])
  const lastId = useRef(0)
  useEffect(() => {
    if (!taskId) { setTask(null); setEvents([]); return }
    let closed = false, source: EventSource | null = null, retry = 1000
    let fallback: ReturnType<typeof setInterval> | undefined
    const sync = async () => { try { if (!closed) setTask(await getTaskStatus(taskId)) } catch { /* reconnect handles it */ } }
    const connect = () => {
      if (closed || !navigator.onLine) { setConnection('offline'); return }
      source = new EventSource(taskEventsUrl(taskId, lastId.current))
      source.onopen = () => { setConnection('connected'); retry = 1000 }
      const receive = (raw: MessageEvent<string>) => {
        const event = JSON.parse(raw.data) as TaskEvent
        lastId.current = Math.max(lastId.current, event.id || 0)
        setEvents(current => [...current.filter(item => item.id !== event.id), event].slice(-200))
        void sync()
      }
      for (const name of ['snapshot','queued','started','progress','checkpointed','completed','failed','canceled','deleted'])
        source.addEventListener(name, receive as EventListener)
      source.onerror = () => {
        source?.close(); setConnection('reconnecting')
        if (!fallback) fallback = setInterval(sync, 15000)
        setTimeout(connect, retry); retry = Math.min(retry * 2, 30000)
      }
    }
    const wake = () => { void sync(); source?.close(); connect() }
    void sync(); connect()
    window.addEventListener('online', wake); window.addEventListener('offline', wake)
    document.addEventListener('visibilitychange', wake)
    return () => { closed = true; source?.close(); if (fallback) clearInterval(fallback)
      window.removeEventListener('online', wake); window.removeEventListener('offline', wake)
      document.removeEventListener('visibilitychange', wake) }
  }, [taskId])
  return { task, events, connection, refresh: () => taskId ? getTaskStatus(taskId).then(setTask) : Promise.resolve() }
}
