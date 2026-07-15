import { useEffect, useState } from 'react'
import { getEvaluationReport } from '../api/paperApi'
import type { EvaluationMetrics, EvaluationReport } from '../types/api'

const percentMetrics = new Set([
  'candidate_recall_at_20', 'recall_at_6', 'precision_at_6', 'mrr',
  'evidence_coverage', 'evidence_f1', 'unanswerable_refusal_rate',
  'answerable_false_refusal_rate',
])

function value(report: EvaluationReport, scenario: string, metric: string): string {
  const raw = report.scenarios.find(item => item.scenario === scenario)?.metrics[metric as keyof EvaluationMetrics]
  if (typeof raw !== 'number') return '—'
  if (percentMetrics.has(metric)) return `${(raw * 100).toFixed(1)}%`
  if (metric.includes('latency')) return `${raw.toFixed(1)} ms`
  return raw.toFixed(4)
}

const labels: Array<[string, string]> = [
  ['candidate_recall_at_20', 'Candidate R@20'], ['recall_at_6', 'Recall@6'],
  ['precision_at_6', 'Precision@6'], ['mrr', 'MRR'], ['evidence_f1', 'Evidence F1'],
  ['unanswerable_refusal_rate', 'Correct refusal'],
  ['answerable_false_refusal_rate', 'False refusal'], ['latency_p95_ms', 'p95 latency'],
  ['estimated_cost_usd', 'Est. cost (USD)'],
]

export function Evaluation() {
  const [report, setReport] = useState<EvaluationReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    void getEvaluationReport().then(setReport).catch(err => setError(err instanceof Error ? err.message : 'Unable to load evaluation report.'))
  }, [])
  if (error) return <section className="panel evaluation-empty"><h2>No benchmark report available</h2><p>{error}</p></section>
  if (!report) return <section className="panel evaluation-empty"><h2>Loading evaluation…</h2></section>
  const realRun = report.schema_version === 'public-paper-benchmark-v2'
  const gates = report.quality_gates
  const datasetPapers = report.dataset_paper_count ?? report.paper_count
  const evaluatedPapers = report.evaluated_paper_count ?? report.paper_count
  return <section className="evaluation-workspace">
    <header className="panel evaluation-header">
      <div><span className="intro-kicker">{realRun ? `Real retrieval ${report.run_level}` : 'Public evidence benchmark'}</span><h2>{report.benchmark} · {report.split}</h2>
        <p>{datasetPapers} dataset papers · {evaluatedPapers} evaluated papers · {report.case_count} questions · schema {report.schema_version}</p></div>
      <span className={`evaluation-grade grade-${report.result_status}`}>{report.result_status.replaceAll('_', ' ')}</span>
    </header>
    {!realRun && report.result_status !== 'public_benchmark_run' && <div className="evaluation-warning">This artifact is a format/CI result, not a real public benchmark claim. Import the official QASPER split to publish a public run.</div>}
    {realRun && report.quality_gate && <div className={`evaluation-warning ${report.quality_gate.passed ? 'evaluation-pass' : ''}`}>
      Overall {report.run_level} quality gate: <strong>{report.quality_gate.passed ? 'passed' : 'failed'}</strong>
      {!report.quality_gate.passed && report.quality_gate.failures.length > 0 && ` · ${report.quality_gate.failures.join(' · ')}`}
    </div>}
    {realRun && gates && <div className="evaluation-gates">
      {(['retrieval','reranker','refusal'] as const).map(name => gates[name] && <article className="panel evaluation-gate" key={name}>
        <header><strong>{name} gate</strong><span className={gates[name].passed ? 'gate-pass' : 'gate-fail'}>{gates[name].passed ? 'passed' : 'failed'}</span></header>
        <p>{gates[name].failures.length ? gates[name].failures.join(' · ') : 'All thresholds met.'}</p>
      </article>)}
    </div>}
    {realRun && report.production_recommendation && <div className="panel evaluation-scope">
      <div><strong>Embedding recommendation</strong><p>{report.production_recommendation.embedding.replaceAll('_',' ')}</p></div>
      <div><strong>Reranker recommendation</strong><p>{report.production_recommendation.reranker}</p></div>
      <div><strong>Validation authorization</strong><p>{report.validation_authorized ? `${(report.validation_scope ?? 'authorized').replaceAll('_',' ')} authorized` : report.run_level === 'validation' ? 'consumed by this run' : 'not authorized'}</p></div>
    </div>}
    <div className="panel evaluation-scope"><div><strong>Measured</strong><p>{report.scope.evaluates.join(' · ')}</p></div><div><strong>Out of scope</strong><p>{report.scope.does_not_evaluate.join(' · ')}</p></div><div><strong>Answer baseline</strong><p>{report.scope.answer_baseline}</p></div></div>
    <div className="panel evaluation-table-wrap"><table className="evaluation-table"><thead><tr><th>Metric</th>{report.scenarios.map(item => <th key={item.scenario}>{item.scenario.replaceAll('_', ' ')}</th>)}</tr></thead>
      <tbody>{labels.map(([metric,label]) => <tr key={metric}><th>{label}</th>{report.scenarios.map(item => <td key={item.scenario}>{value(report,item.scenario,metric)}</td>)}</tr>)}</tbody></table></div>
    <div className="evaluation-cards">
      {report.scenarios.map(item => <article className="panel evaluation-card" key={item.scenario}><header><h3>{item.scenario.replaceAll('_', ' ')}</h3><span>{item.case_count} cases</span></header>
        <dl><div><dt>Evidence coverage</dt><dd>{(item.metrics.evidence_coverage*100).toFixed(1)}%</dd></div><div><dt>p50 / p95</dt><dd>{item.metrics.latency_p50_ms.toFixed(1)} / {item.metrics.latency_p95_ms.toFixed(1)} ms</dd></div><div><dt>Failures</dt><dd>{item.failure_case_ids.length}</dd></div></dl>
        {item.degraded_reasons.length > 0 && <p className="degraded-note">{item.degraded_reasons.join(' · ')}</p>}</article>)}
    </div>
    {realRun && report.request_counts && <p className="evaluation-run-note">Upstream requests: {report.request_counts.embedding_batches} embedding batches · {report.request_counts.rerank_requests} reranks. Threshold combinations were replayed offline.</p>}
  </section>
}
