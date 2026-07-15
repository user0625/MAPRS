# Public benchmark report

- Benchmark: QASPER (validation)
- Papers / cases: 1 / 3
- Result status: `unverified_local_run`
- Schema: `public-paper-benchmark-v1`

This report evaluates full-text retrieval and Evidence behavior only; it does not evaluate PDF parsing or page-number accuracy.
The answer score is a transparent top-paragraph heuristic. No LLM-as-Judge score is used.

| Scenario | Cand. R@20 | R@6 | P@6 | MRR | Evidence F1 | Refusal | False refusal | p95 ms | Cost USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bm25 | 0.500 | 0.500 | 0.250 | 0.500 | 0.333 | 1.000 | 0.500 | 0.19 | 0.0000 |
| vector | 0.500 | 0.500 | 0.250 | 0.500 | 0.333 | 1.000 | 0.500 | 0.13 | 0.0000 |
| rrf | 0.500 | 0.500 | 0.250 | 0.500 | 0.333 | 1.000 | 0.500 | 0.14 | 0.0000 |
| rrf_reranker | 0.500 | 0.500 | 0.250 | 0.500 | 0.333 | 1.000 | 0.500 | 0.14 | 0.0000 |
| embedding_degraded | 0.500 | 0.500 | 0.250 | 0.500 | 0.333 | 1.000 | 0.500 | 0.12 | 0.0000 |
| reranker_degraded | 0.500 | 0.500 | 0.250 | 0.500 | 0.333 | 1.000 | 0.500 | 0.13 | 0.0000 |

Failure details are represented by case IDs only; questions and paper text are intentionally omitted.
