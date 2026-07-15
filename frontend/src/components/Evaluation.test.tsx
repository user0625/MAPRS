import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Evaluation } from './Evaluation'


const fixture = {
  schema_version:'public-paper-benchmark-v1', benchmark:'QASPER', result_status:'unverified_local_run',
  dataset_adapter_version:'qasper-adapter-v1', split:'validation', generated_at:'2026-01-01T00:00:00Z',
  scope:{evaluates:['full-text retrieval'],does_not_evaluate:['PDF parsing'],answer_baseline:'transparent heuristic'},
  paper_count:1, case_count:3, exclusions:{figure_or_table:1}, configuration:{candidate_k:20},
  scenarios:[{scenario:'bm25',effective_modes:['bm25'],case_count:3,metrics:{
    candidate_recall_at_20:.9,recall_at_6:.8,precision_at_6:.5,mrr:.8,evidence_coverage:.75,evidence_f1:.6,
    unanswerable_refusal_rate:1,answerable_false_refusal_rate:0,answer_token_f1:.4,citation_validity_rate:1,
    evidence_support_rate:1,latency_p50_ms:10,latency_p95_ms:20,estimated_cost_usd:0,
  },answer_quality_by_type:{},degraded_reasons:[],failure_case_ids:['case-1']}],
}
describe('Evaluation', () => {
  afterEach(() => vi.unstubAllGlobals())
  it('shows quality, latency, scope and fixture disclosure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ok:true,json:async()=>fixture}))
    render(<Evaluation />)
    expect(await screen.findByText('QASPER · validation')).toBeInTheDocument()
    expect(screen.getByText('Candidate R@20')).toBeInTheDocument()
    expect(screen.getByText('p95 latency')).toBeInTheDocument()
    expect(screen.getByText(/not a real public benchmark claim/i)).toBeInTheDocument()
    expect(screen.getByText('PDF parsing')).toBeInTheDocument()
  })

  it('shows three real gates, retrieval-only authorization and separate recommendations', async () => {
    const real = {...fixture,
      schema_version:'public-paper-benchmark-v2',result_status:'public_real_model_run',run_level:'pilot',
      dataset_paper_count:840,evaluated_paper_count:26,
      quality_gate:{passed:false,failures:['reranker precision uplift >= 0.05']},
      quality_gates:{
        retrieval:{name:'retrieval',passed:true,failures:[]},
        reranker:{name:'reranker',passed:false,failures:['reranker precision uplift >= 0.05']},
        refusal:{name:'refusal',passed:false,failures:['unanswerable refusal >= 0.70']},
      },
      validation_authorized:true,validation_scope:'retrieval_only',
      production_recommendation:{embedding:'candidate_for_validation',reranker:'disabled'},
      request_counts:{embedding_batches:100,rerank_requests:100},
      scenarios:fixture.scenarios.map(item=>({...item,scenario:'bm25-offline-baseline'})),
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ok:true,json:async()=>real}))
    render(<Evaluation />)
    expect(await screen.findByText('Real retrieval pilot')).toBeInTheDocument()
    expect(screen.getByText(/840 dataset papers · 26 evaluated papers/i)).toBeInTheDocument()
    expect(screen.getByText(/overall pilot quality gate:/i)).toHaveTextContent('failed')
    expect(screen.getByText('retrieval gate')).toBeInTheDocument()
    expect(screen.getByText('reranker gate')).toBeInTheDocument()
    expect(screen.getByText('refusal gate')).toBeInTheDocument()
    expect(screen.getByText('candidate for validation')).toBeInTheDocument()
    expect(screen.getByText('disabled')).toBeInTheDocument()
    expect(screen.getByText('retrieval only authorized')).toBeInTheDocument()
    expect(screen.getByText(/100 embedding batches · 100 reranks/i)).toBeInTheDocument()
    expect(screen.queryByText(/not a real public benchmark claim/i)).not.toBeInTheDocument()
  })
})
