import { useEffect, useState } from 'react'

interface ReportActionsProps {
  markdown: string
  filename?: string
}

export function ReportActions({ markdown, filename = 'paper-analysis-report.md' }: ReportActionsProps) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const timeout = setTimeout(() => setCopied(false), 1800)
    return () => clearTimeout(timeout)
  }, [copied])

  const copyReport = async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(markdown)
      } else {
        const textarea = document.createElement('textarea')
        textarea.value = markdown
        textarea.style.position = 'fixed'
        textarea.style.opacity = '0'
        document.body.appendChild(textarea)
        textarea.select()
        const succeeded = document.execCommand('copy')
        textarea.remove()
        if (!succeeded) throw new Error('Copy command was rejected.')
      }
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  const downloadReport = () => {
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    URL.revokeObjectURL(url)
  }

  return <div className="report-actions" aria-label="Report actions">
    <button type="button" onClick={() => void copyReport()}>{copied ? 'Copied' : 'Copy'}</button>
    <button type="button" onClick={downloadReport}>Download .md</button>
  </div>
}
