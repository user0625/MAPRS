import type { TaskStatus, TaskStatusResponse } from '../types/api'

interface TaskStatusCardProps {
  task: TaskStatusResponse | null
  taskId: string | null
}

const statusLabels: Record<TaskStatus, string> = {
  pending: 'Pending',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
}

export function TaskStatusCard({ task, taskId }: TaskStatusCardProps) {
  if (!taskId) {
    return (
      <section className="panel status-card status-empty">
        <span className="status-orbit" aria-hidden="true">◎</span>
        <h2>Ready when you are</h2>
        <p>Upload a paper to see live progress from the analysis agents.</p>
      </section>
    )
  }

  const status = task?.status ?? 'pending'

  return (
    <section className="panel status-card" aria-live="polite">
      <div className="status-header">
        <div>
          <span className="eyebrow">Analysis task</span>
          <h2>{task?.paper_title ?? 'Processing your paper'}</h2>
        </div>
        <span className={`status-badge status-${status}`}>
          <span className="status-dot" aria-hidden="true" />
          {statusLabels[status]}
        </span>
      </div>

      <dl className="task-details">
        <div>
          <dt>Task ID</dt>
          <dd>{taskId}</dd>
        </div>
        <div>
          <dt>Agent update</dt>
          <dd>{task?.message ?? 'Task created. Waiting for an agent…'}</dd>
        </div>
      </dl>

      {(status === 'pending' || status === 'running') && (
        <div className="progress-track" aria-label="Analysis is in progress">
          <span />
        </div>
      )}

      {status === 'failed' && task?.error_message && (
        <div className="error-message" role="alert">
          <strong>Analysis failed</strong>
          <span>{task.error_message}</span>
        </div>
      )}
    </section>
  )
}
