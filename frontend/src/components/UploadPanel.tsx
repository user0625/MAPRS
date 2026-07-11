import { useState, type FormEvent } from 'react'
import type { OutputLanguage } from '../types/api'

interface UploadPanelProps {
  disabled: boolean
  onSubmit: (file: File, query: string, language: OutputLanguage) => Promise<void>
}

const DEFAULT_QUERY = 'Analyze this paper and generate a structured reading report.'

export function UploadPanel({ disabled, onSubmit }: UploadPanelProps) {
  const [file, setFile] = useState<File | null>(null)
  const [query, setQuery] = useState(DEFAULT_QUERY)
  const [language, setLanguage] = useState<OutputLanguage>('zh')
  const [validationError, setValidationError] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    if (!file) {
      setValidationError('Please select a PDF file first.')
      return
    }

    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setValidationError('Only PDF files are supported.')
      return
    }

    setValidationError(null)
    await onSubmit(file, query.trim(), language)
  }

  return (
    <section className="panel upload-panel" aria-labelledby="upload-title">
      <div className="panel-heading">
        <span className="eyebrow">New analysis</span>
        <h2 id="upload-title">Upload a research paper</h2>
        <p>Send a PDF to the agent team for structured reading and review.</p>
      </div>

      <form onSubmit={handleSubmit}>
        <label className={`file-drop ${file ? 'has-file' : ''}`}>
          <input
            type="file"
            accept="application/pdf,.pdf"
            disabled={disabled}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null)
              setValidationError(null)
            }}
          />
          <span className="file-icon" aria-hidden="true">PDF</span>
          <span className="file-copy">
            <strong>{file ? file.name : 'Choose a PDF paper'}</strong>
            <small>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : 'Click to browse from your computer'}</small>
          </span>
        </label>

        <label className="field">
          <span>Research question</span>
          <textarea
            value={query}
            disabled={disabled}
            rows={4}
            placeholder="What would you like the agents to focus on?"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>

        <fieldset className="language-field" disabled={disabled}>
          <legend>Report language</legend>
          <div className="segmented-control">
            <label>
              <input
                type="radio"
                name="language"
                value="zh"
                checked={language === 'zh'}
                onChange={() => setLanguage('zh')}
              />
              <span>中文</span>
            </label>
            <label>
              <input
                type="radio"
                name="language"
                value="en"
                checked={language === 'en'}
                onChange={() => setLanguage('en')}
              />
              <span>English</span>
            </label>
          </div>
        </fieldset>

        {validationError && <p className="form-error" role="alert">{validationError}</p>}

        <button className="primary-button" type="submit" disabled={disabled}>
          {disabled ? <span className="spinner" aria-hidden="true" /> : <span aria-hidden="true">✦</span>}
          {disabled ? 'Analysis in progress' : 'Start Analysis'}
        </button>
      </form>
    </section>
  )
}
