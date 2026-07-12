import { useRef, useState, type DragEvent, type FormEvent, type KeyboardEvent } from 'react'
import type { AnalysisDepth, OutputLanguage, ReportConfiguration, ReportTemplate, TargetAudience } from '../types/api'

interface UploadPanelProps {
  disabled: boolean
  onSubmit: (file: File, query: string, language: OutputLanguage, configuration: ReportConfiguration) => Promise<void>
}

const DEFAULT_QUERY = 'Analyze this paper and generate a structured reading report.'
const PRESETS = {
  'Quick Reading': ['quick', 'general', 'standard'],
  'Research Review': ['standard', 'researcher', 'standard'],
  'Peer Review': ['deep', 'reviewer', 'review'],
  'Reproducibility': ['deep', 'reviewer', 'reproducibility'],
} as const

export function UploadPanel({ disabled, onSubmit }: UploadPanelProps) {
  const [file, setFile] = useState<File | null>(null)
  const [query, setQuery] = useState(DEFAULT_QUERY)
  const [language, setLanguage] = useState<OutputLanguage>('zh')
  const [depth, setDepth] = useState<AnalysisDepth>('standard')
  const [audience, setAudience] = useState<TargetAudience>('researcher')
  const [template, setTemplate] = useState<ReportTemplate>('standard')
  const [customSections, setCustomSections] = useState('')
  const [validationError, setValidationError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const chooseFile = (next: File | null) => {
    if (next && (!next.name.toLowerCase().endsWith('.pdf') || (next.type && next.type !== 'application/pdf'))) {
      setValidationError('Only PDF files are supported.'); return
    }
    if (next && next.size > 50 * 1024 * 1024) { setValidationError('PDF files must be 50 MB or smaller.'); return }
    setFile(next); setValidationError(null)
  }
  const drop = (event: DragEvent) => { event.preventDefault(); setDragging(false); chooseFile(event.dataTransfer.files[0] || null) }
  const activate = (event: KeyboardEvent) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); inputRef.current?.click() }
  }

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
    const sections = customSections.split(/[,\n]/).map(value => value.trim()).filter(Boolean)
    if (sections.length > 20 || sections.some(value => value.length > 80)) {
      setValidationError('Use at most 20 custom sections, each no longer than 80 characters.')
      return
    }
    await onSubmit(file, query.trim(), language, {
      analysis_depth: depth, target_audience: audience, report_template: template,
      custom_sections: sections,
    })
  }

  return (
    <section className="panel upload-panel" aria-labelledby="upload-title">
      <div className="panel-heading">
        <span className="eyebrow">New analysis</span>
        <h2 id="upload-title">Upload a research paper</h2>
        <p>Send a PDF to the agent team for structured reading and review.</p>
      </div>

      <form onSubmit={handleSubmit}>
        <label className={`file-drop ${file ? 'has-file' : ''} ${dragging ? 'dragging' : ''}`}
          tabIndex={0} role="button" onKeyDown={activate} onDragOver={event => { event.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)} onDrop={drop} aria-label="Drop a PDF or press Enter to browse">
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            disabled={disabled}
            onChange={(event) => {
              chooseFile(event.target.files?.[0] ?? null)
            }}
          />
          <span className="file-icon" aria-hidden="true">PDF</span>
          <span className="file-copy">
            <strong>{file ? file.name : 'Choose a PDF paper'}</strong>
            <small>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : 'Drop here or click to browse · PDF up to 50 MB'}</small>
          </span>
        </label>

        <div className="preset-row" aria-label="Analysis presets">{Object.entries(PRESETS).map(([name, values]) =>
          <button type="button" key={name} disabled={disabled} onClick={() => {
            setDepth(values[0]); setAudience(values[1]); setTemplate(values[2])
          }}>{name}</button>)}</div>

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

        <div className="report-options">
          <label className="field"><span>Analysis depth</span>
            <select value={depth} disabled={disabled} onChange={event => setDepth(event.target.value as AnalysisDepth)}>
              <option value="quick">Quick</option><option value="standard">Standard</option><option value="deep">Deep</option>
            </select>
          </label>
          <label className="field"><span>Target audience</span>
            <select value={audience} disabled={disabled} onChange={event => setAudience(event.target.value as TargetAudience)}>
              <option value="general">General</option><option value="researcher">Researcher</option><option value="reviewer">Reviewer</option>
            </select>
          </label>
          <label className="field"><span>Report template</span>
            <select value={template} disabled={disabled} onChange={event => setTemplate(event.target.value as ReportTemplate)}>
              <option value="standard">Standard</option><option value="review">Peer review</option><option value="reproducibility">Reproducibility</option>
            </select>
          </label>
        </div>
        <label className="field"><span>Custom sections (optional, comma or line separated)</span>
          <textarea rows={2} value={customSections} disabled={disabled}
            onChange={event => setCustomSections(event.target.value)} placeholder="Ablations, Ethical considerations" />
        </label>

        {validationError && <p className="form-error" role="alert">{validationError}</p>}

        <button className="primary-button" type="submit" disabled={disabled}>
          {disabled ? <span className="spinner" aria-hidden="true" /> : <span aria-hidden="true">✦</span>}
          {disabled ? 'Analysis in progress' : 'Start Analysis'}
        </button>
      </form>
    </section>
  )
}
