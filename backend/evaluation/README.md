# Ask Paper 离线质量评估

## 公开 QASPER Benchmark

公开基准入口是 `backend.evaluation.qasper_benchmark`。导入命令要求调用者提供官方文件的 SHA-256；原始数据与适配后的段落文本只进入 Git 忽略缓存。适配器保留官方论文级 train/validation/test 划分，只接受标注者可回答性一致、文本 Evidence 可完全且无歧义映射的样本，并排除 `FLOAT SELECTED` figure/table 样本。

```bash
uv run python -m backend.evaluation.qasper_benchmark import \
  --source /data/qasper-dev-v0.3.json \
  --cache backend/data/public_evaluation/qasper-v0.3 \
  --split validation \
  --sha256 <official-file-sha256>

uv run python -m backend.evaluation.qasper_benchmark run \
  --cache backend/data/public_evaluation/qasper-v0.3 \
  --split validation \
  --official-source \
  --output backend/evaluation/results/qasper-validation-v0.3.json
```

test split 还必须传 `--final-config`，同一导入缓存只允许运行一次。报告采用 `public-paper-benchmark-v1`，包含 BM25、离线 Vector、BM25+Vector+RRF、RRF+Reranker、Embedding 降级和 Reranker 降级；失败明细只保存 case ID，不保存完整问题或正文。QASPER 没有可靠 PDF 页码，因此报告明确不覆盖 PDF 解析和页码准确性。仓库自带的 `results/qasper-format-fixture-v1.*` 只验证格式、CI 与 Evaluation 页面，状态为 `unverified_local_run`，不是公开真实质量结论。第三方归属见 [`THIRD_PARTY_QASPER.md`](THIRD_PARTY_QASPER.md)。

### 真实 Embedding / Reranker 分阶段校准

真实校准前必须分别导入官方 train 与 validation，并保留各自 SHA；不要导入 test。导入器同时生成 Git 忽略、内容寻址的轻量 state，chunk ID、section 和正文保持稳定，但不会伪造页码。train/validation 论文 ID 有交集、state 或 adapted SHA 变化都会使后续 artifact 失效。

```bash
uv run python -m backend.evaluation.qasper_benchmark import \
  --source /data/qasper-train-v0.3.json \
  --cache backend/data/public_evaluation/qasper-v0.3 \
  --split train --sha256 <official-train-sha256>

uv run python -m backend.evaluation.qasper_benchmark import \
  --source /data/qasper-dev-v0.3.json \
  --cache backend/data/public_evaluation/qasper-v0.3 \
  --split validation --sha256 <official-validation-sha256>

uv run python -m backend.evaluation.qasper_benchmark real-pilot \
  --cache backend/data/public_evaluation/qasper-v0.3 \
  --pilot-version qasper-real-pilot-tev3-v3 \
  --output backend/outputs/evaluation/qasper-real-pilot-tev3-v3.json \
  --checkpoint backend/outputs/evaluation/qasper-real-pilot-tev3-v2.checkpoint.json
```

`real-pilot` 会先执行各一个脱敏 Embedding/Reranker 合成 preflight，然后从 train 固定选择 100 题（45 extractive、20 free-form、10 yes/no、25 unanswerable，每篇最多 4 题）。每篇向量索引只构建或加载一次，每题只发起一次 query embedding 和 rerank；20/30/40 候选、4/6/8 Evidence 和阈值网格都从这次最大深度采集离线重放。Pilot 最多允许 400 个 embedding 批次和 120 次 rerank，评测 reranker timeout 为 10 秒。

checkpoint schema 为 `qasper-real-checkpoint-v2`，保存原始 wall latency、本次请求实际发生的 build/load、查询延迟、持久/内存缓存状态、数值分数、请求数和失败类别，不含问题、正文、密钥或端点。`real-pilot` 和 `real-validation` 都可用 `--checkpoint` 显式恢复完全匹配的数据、模型、样本选择与配置；v1 checkpoint 只供审计，不得生成新的权威延迟结论。

Pilot 有三条独立质量门：

- Retrieval：Hybrid Candidate Recall@20 ≥85%、不低于 BM25、降级率 ≤1%、查询 p95 ≤3 秒；
- Reranker：相对最佳 Hybrid 的 Precision@6 提升 ≥5pp，Recall@6 下降 ≤2pp；
- Refusal：不可回答拒答率 ≥70%，可回答错误拒答率 ≤10%。

三条都通过时总门禁才通过；仅 Retrieval 通过即可设置 `validation_authorized=true`、`validation_scope=retrieval_only`。Retrieval-only validation 固定复用 Pilot 的 Hybrid 配置，Reranker 保持 `disabled`，不会发起 706 次 rerank。其质量门为 Candidate Recall@20 ≥90%、Recall@6 ≥70%、Precision@6 ≥20%、MRR ≥0.55、Coverage ≥70%、Evidence F1 ≥30%、降级率 ≤1%、p95 ≤3 秒。

当前 Hybrid 选择先过滤通过正式 Retrieval 门的配置，再在固定 100 题内按论文划分 5 个确定性、答案类型平衡的折。只有所有折都满足上述八项 proxy 的配置才进入稳健候选集；若该集合为空，则按跨折失败规则数最少、最差折指标最好和全量指标的顺序安全回退。报告分别保留全量 proxy、全折 proxy、最差折指标、折分布及 assignment SHA。`validation_authorized` 仍只表达正式 Retrieval 门；新增的 `validation_recommended` 只有 Retrieval 和全折 proxy 都通过时才为真，新 Pilot 若为 false 会被 validation 入口拒绝。

### `qasper-real-pilot-tev3-v2` 真实结果（2026-07-15）

本次使用 `text-embedding-v3` 与 `qwen3-rerank`，对 train 的 840 篇适配论文中固定抽取 26 篇、100 题。preflight 通过；采集使用 100 个 embedding batch、100 次 rerank，均未超过上限。持久向量全部命中：26 次磁盘加载、74 次内存命中，索引 build 为 0，总 load 为 234.595ms；build/load 互斥。Hybrid 查询 p50/p95 为 167.5/299.4ms，含 Reranker 为 511.2/646.5ms，降级率为 0。

Retrieval 门通过：Hybrid Candidate Recall@20 为 98.95%，高于 BM25 的 85.65%；Recall@6 83.46%、Precision@6 17.33%、MRR 0.588、Coverage 78.67%、Evidence F1 27.49%。选定 Hybrid 配置为 candidate/evidence `20/6`、BM25 最低分 `6.1050431604`、向量最低相似度 `0.3569696024`、RRF k `60`。

Reranker 与 Refusal 门失败，3267 组离线配置中可行配置数为 0，因此没有 selected Reranker configuration：

- 召回保护方案 Recall@6 为 83.68%，但 Precision@6 20.22%，相对 Hybrid 只提升 2.89pp，未达到 5pp；
- 精度优先方案 Precision@6 为 31.67%，但 Recall@6 降至 51.24%，下降 32.22pp；其不可回答拒答率为 60%，错误拒答率为 28%；
- 报告将 answerability threshold 拒答与 Evidence 过滤后空结果分开统计；精度优先方案的拒答全部来自 answerability threshold，未发现 Evidence 过滤后空结果。

结论是 `quality_gate.passed=false`，但 Retrieval 单轨已授权 `retrieval_only` validation。Embedding 推荐为 `candidate_for_validation`，Reranker 固定为 `disabled`。旧 `qasper-real-pilot-tev3-v1` 继续作为失败审计记录保留，其延迟字段不作为权威结论。本次尚未运行 validation、test 或人工评估，也未把 Embedding 切换为默认配置。

### `qasper-real-pilot-tev3-v3` 修订选择结果（2026-07-15）

v3 显式复用 v2 checkpoint；数据、模型、100 题样本和最大深度采集配置均未变化，因此没有重复 100 次 embedding/rerank。artifact 保留 checkpoint 的 100/100 请求计数、26 次磁盘加载、74 次内存命中、234.595ms 总 load 和非零查询延迟。当前端点 preflight 各执行一次并通过，Embedding/Reranker 分别耗时 704.551/317.197ms。

9801 组 Hybrid 配置中有 8136 组通过 Retrieval 门，其中 57 组同时通过 `pilot_validation_proxy`。最终配置为 candidate/evidence `20/4`、BM25 最低分 `6.1050431604`、向量最低相似度 `0.0`、RRF k `60`。其 Candidate Recall@20 为 98.95%、Recall@6 75.24%、Precision@6 22.33%、MRR 0.570、Coverage 70.67%、Evidence F1 32.98%、降级率 0，查询 p50/p95 为 167.5/299.4ms；八项 proxy 全部通过。

正式 Retrieval 门通过并继续授权 `retrieval_only` validation。Reranker 与 Refusal 门仍失败，3267 组诊断配置中可行数仍为 0，`quality_gate.passed=false`，Reranker 推荐保持 `disabled`。v2 作为有效历史记录保留，但其 Evidence=6 配置未通过 Precision/F1 proxy；后续 validation 只复用了 v3。生成 v3 时尚未运行 validation、test 或人工评估，也未把 Embedding 切换为默认配置。

实际执行的 retrieval-only validation 命令如下；同一版本和输出不得重跑：

```bash
uv run python -m backend.evaluation.qasper_benchmark real-validation \
  --cache backend/data/public_evaluation/qasper-v0.3 \
  --pilot backend/outputs/evaluation/qasper-real-pilot-tev3-v3.json \
  --calibration-version qasper-real-retrieval-cal-tev3-v3 \
  --output backend/outputs/evaluation/qasper-real-validation-tev3-v3.json
```

validation 会验证数据 manifest、模型和 Hybrid 配置 SHA，原样重放 Pilot 配置，不重新调参。通过后 Embedding 才推荐 `candidate_default`；Reranker 仍为 `disabled`。当前 CLI 没有真实 test 子命令，避免在配置冻结前访问 test。

### `qasper-real-retrieval-cal-tev3-v3` validation 失败结果（2026-07-15）

validation 对 261 篇、706 题完整运行，Pilot artifact SHA 和配置 SHA 均匹配，阈值未经调优原样重放。Embedding preflight 单请求通过；实际记录 2341 个 embedding batch，包括 261 篇的首次索引构建与 706 次查询，445 次同论文内存索引复用。Reranker 请求为 0，降级率为 0，查询 p50/p95 为 213.9/353.3ms；运行本身有效，不是端点或延迟故障。

Hybrid Precision@6 为 21.44%、降级率和延迟通过，但总门失败：Candidate Recall@20 89.77%（门槛 90%）、Recall@6 54.96%（70%）、MRR 0.478（0.55）、Coverage 55.57%（70%）、Evidence F1 28.72%（30%）。因此 `quality_gate.passed=false`、`calibration_version=null`，没有写入 calibration registry，Embedding 保持 `keep_current`，Reranker 保持 `disabled`。artifact 与 checkpoint 作为失败审计记录保留；不得降低门槛、用这 706 题反向选择阈值或重复运行同版本。test 与人工评估仍未访问。

### Train-only 5 折稳健性诊断

为防止再次由单个 100 题聚合指标产生乐观选择，后续 Pilot 固定使用 paper-disjoint 5 折。现有 v3 checkpoint 仅在 train 上离线重放；显式锁定其 `text-embedding-v3` 模型签名，不执行 preflight 或真实请求，也不读取 validation/test。5 折分别含 20/20/20/21/19 题、5/5/5/6/5 篇论文，折间无论文重叠。

诊断中仍有 8136 组配置通过 Retrieval、57 组通过全量 proxy，但全折 proxy 合格数为 0。确定性回退仍选中 v3 的 `20/4` 配置；最差折 Candidate Recall@20 为 97.06%、Recall@6 67.35%、Precision@6 19.64%、MRR 0.488、Coverage 52.94%、Evidence F1 30.48%。因此新报告会保留 `validation_authorized=true` 的正式 Retrieval 语义，但设置 `validation_recommended=false`、Embedding `keep_current`；本次不生成正式 v4 artifact，也不启动新的 validation 或 test。

### `qasper-real-pilot-tev4-v1` 稳健性结果（2026-07-15）

本次使用 `text-embedding-v4` 与 `qwen3-rerank` 重新采集同一 train Pilot，Embedding/Reranker preflight 各一次通过。采集记录 188 个 embedding batch、100 次 rerank；索引 build 为 28.54s，查询 p50/p95 为 187.5/262.1ms，降级率为 0。未读取 validation 或 test。

7464 组配置通过 Retrieval，39 组通过 100 题全量 proxy，但仍无配置通过全部 5 折 proxy。因此选择器按安全回退规则选中 candidate/evidence `20/4`、BM25 最低分 `0.6177483461`、向量最低相似度 `0.3767446518`、RRF k `60`。全量 Candidate Recall@20 为 97.84%、Recall@6 70.32%、Precision@6 24.33%、MRR 0.530、Coverage 65.33%、Evidence F1 33.86%；MRR 和 Coverage 未通过 proxy。最差折 Candidate Recall@20 为 95.10%、Recall@6 50.51%、Precision@6 21.43%、MRR 0.435、Coverage 42.86%、Evidence F1 31.15%。

正式 Retrieval 门通过，但跨折稳健性门失败，所以 artifact 明确记录 `validation_authorized=true`、`validation_recommended=false`、Embedding `keep_current`。Reranker 与 Refusal 门仍失败，Reranker 保持 `disabled`。tev4 不启动 validation/test，也不切换为默认 Embedding；该 artifact 仅作为失败审计记录。模型轮换实验在此收口，后续优先完成人工复核、Trace/并行对比与端到端演示。

## 10 篇/80 题双语人工复核集

现有 `generate_candidates` 仍只生成 `candidate`，不能自动升级为 gold。10 篇论文每篇 8 题完成单人逐项复核后，使用 reviewed-demo profile 校验：

```bash
uv run python -m backend.evaluation.validate_dataset \
  --dataset backend/data/private_evaluation/reviewed-demo-v1 \
  --profile reviewed-demo
```

该 profile 固定检查 10 篇/80 条、每篇 8 条且逐篇中英文各 4 条、25% 不可回答、至少 20% 多 Evidence、论文级 60/20/20 划分，并要求覆盖同义改写、跨章节、相邻段落、缩写、数字和近似不可回答干扰类型。manifest 还必须明示：`dataset_label: human-reviewed demonstration set`、`reviewer_count: 1` 以及不含 “expert” 的 `review_claim`。仓库不伪造尚未完成的人工复核内容。

## Trace 与并行 Reader

`ANALYSIS_TRACE_ENABLED=true` 默认记录 content-free 单任务 Trace，包括阶段耗时、模型、Prompt 版本、token、结构化输出重试、降级、估算成本和 Evidence 数量；不记录正文、完整问题、路径、请求头或密钥。价格通过 `LLM_INPUT_COST_PER_MILLION_USD` 与 `LLM_OUTPUT_COST_PER_MILLION_USD` 配置。

并行 Reader 默认关闭。设置 `PARALLEL_READER_ENABLED=true` 后，只对 Planner 分配给 Reader 的独立任务并行，`READER_PARALLELISM` 控制并发上限，`READER_BRANCH_RETRIES` 控制每分支重试。分支状态按 Planner 顺序稳定聚合；部分失败会在 `reader_execution.coverage_gaps` 明示，全部失败才终止流程。Critic、Writer、Verifier 仍在汇总后串行运行。

冻结集串并行验收使用：

```bash
uv run python -m backend.evaluation.parallel_comparison \
  --serial serial-run.json --parallel parallel-run.json \
  --output backend/outputs/evaluation/parallel-comparison.json \
  --frozen-reviewed-run
```

两个输入必须具有相同 dataset freeze SHA 与配置 SHA。只有真实冻结集运行才传 `--frozen-reviewed-run`；仓库的格式夹具不会触发前端默认切换建议。只有 Evidence Recall、报告质量均不下降且 coverage gap 为零，输出才会建议将 parallel 设为默认。

> 项目范围说明：本评估框架保留了生产化所需的严格边界。正式 gold、冻结 test 和生产 reranker 不作为文档检索增强 MVP 的前置条件；离线合成回归、显式降级和端到端演示完整性是当前优先级。

## Pilot-only 工程准入门

专家裁决的 Pilot 视图与正式 gold 完全隔离：它只在内存中选择
`accept_for_pilot_only` 的 analysis/validation 样本，不重写 `cases.jsonl`，
不把源候选提升为 `reviewed`，并拒绝任何 test split 请求。报告不包含问题或
论文正文，始终标记 `evidence_grade: pilot_only`，生产启用建议固定为空，
reranker 固定保持 `disabled`。

真实 Pilot 必须先运行脱敏 preflight；它只发送固定合成文本，失败输出仅包含异常类别、耗时、请求数和需要修复的配置项名称：

```bash
uv run python -m backend.evaluation.upstream_preflight

uv run python -m backend.evaluation.pilot \
  backend/data/private_evaluation/target-v1 \
  backend/data/private_evaluation/target-v1/expert_adjudication.jsonl \
  backend/outputs/evaluation/pilot-only-v3.json pilot-only-v3 \
  --candidate-counts 20,30,40 \
  --bm25-k1 1.2,1.5,1.8 --bm25-b 0.5,0.75,1.0 --rrf-k 40,60,80 \
  --vector-thresholds 0,0.2,0.4
```

候选配置以 Candidate Recall@20 优先选择；只有选中的配置运行一个 reranker shadow，Evidence/answerability 阈值再从固定边界、观测分数及相邻分数中点离线重放。报告单列索引 build/load、缓存命中率和冷构建失败率，索引构建时间不计入查询 p95。

只有 validation 硬门全部通过且 embedding/reranker 两条显式降级路径完整运行，
`exit_ready_for_comparison_mvp` 才会为 true。Pilot 通过也只代表工程准入，
不构成正式质量或生产启用结论。

这套评估用于在不访问网络、不调用真实模型的条件下，重复测量 Ask Paper 的检索、章节约束、拒答和引用清理质量。它复用线上 `AskPaperRetrievalService` 与 `sanitize_citations`，但不会创建任务、会话或修改 REST/SSE 行为。

## 快速运行

从仓库根目录执行：

```bash
uv run python -m backend.evaluation.ask_paper \
  --mode all \
  --gate \
  --output backend/outputs/logs/ask-paper-eval.json
```

命令会向终端输出完整 JSON 和每种模式的一行摘要。指定 `--output` 时还会保存格式化 JSON；目标目录会自动创建。

可用参数：

| 参数 | 说明 |
| --- | --- |
| `--mode bm25` | 只运行 BM25，不启用向量召回。 |
| `--mode hybrid` | 运行未过滤的 BM25 + 确定性离线向量 + RRF 原始对照。 |
| `--mode filtered-hybrid` | 在 RRF 前过滤无效/低相似度向量候选，运行当前实验基线。 |
| `--mode degraded` | 模拟 embedding 异常，验证显式降级报告。 |
| `--mode all` | 依次运行以上四种模式，默认值。 |
| `--dataset PATH` | 使用另一个同格式 JSON 数据集。 |
| `--output PATH` | 保存完整机器可读报告。 |
| `--gate` | 任一合格基线未达到硬门槛时返回非零退出码。 |

## 数据集

默认数据集是 [`fixtures/ask_paper_v1.json`](fixtures/ask_paper_v1.json)，当前内容版本为 `ask-paper-v2`。它只包含仓库内合成文本，不包含受版权限制的论文全文。

真实标注集应放在仓库外，通过 `--dataset` 传入；不要提交论文正文。阈值选择只运行按论文划分的 validation 数据，冻结 test 只在候选配置确定后运行。每次报告必须保存 dataset version、embedding model、reranker model、阈值和 calibration version；更换任一模型或分块策略后不得复用旧校准版本。

## 真实论文私有评估集

仓库提供三个内部 CLI；它们不修改 REST、SSE、Evidence、前端或数据库。私有目录建议使用 `backend/data/private_evaluation/<version>`，该路径已被 Git 忽略。仓库只提交 [`private_dataset.schema.json`](private_dataset.schema.json) 和 [`fixtures/private_eval_sample`](fixtures/private_eval_sample) 脱敏夹具。

### 1. 生成候选与人工复核模板

先为每篇 PDF 生成包含 chunks 的 analysis state，再重复传入 `--state`：

```bash
uv run python -m backend.evaluation.generate_candidates \
  --state /private/paper-01-state.json \
  --state /private/paper-02-state.json \
  --output backend/data/private_evaluation/target-v1 \
  --dataset-version target-v1 \
  --split-seed private-stable-seed \
  --questions-per-paper 13
```

输出目录包含：

- `manifest.json`：数据版本、分块配置、划分 seed 与冻结状态；
- `papers.jsonl`：论文 ID、state 路径/摘要、chunk ID 与论文级 split，不含正文；
- `cases.jsonl`：问题、语言、章节、可回答性、相关 chunks、最低充分 Evidence 集、干扰类型与复核状态，不含正文。

模型输出一律标为 `candidate`。人工必须核对问题、可回答性、章节、所有相关 chunks 和 `minimum_evidence_sets`，然后改为 `reviewed`；模型生成的 Evidence 不视为金标准。建议先复核约 40 条并统一标注口径，再扩展到约 200 条。

### 2. 校验与冻结 test

```bash
uv run python -m backend.evaluation.validate_dataset \
  --dataset backend/data/private_evaluation/target-v1

uv run python -m backend.evaluation.validate_dataset \
  --dataset backend/data/private_evaluation/target-v1 \
  --freeze-test
```

production profile 检查至少 15 篇、至少 180 条已复核样本、中英文各至少 30 条、无答案 25%–30%、多 Evidence 至少 20%、论文级 60/20/20 划分、唯一 ID、state 摘要及所有 chunk/Evidence 引用。冻结会把 test 行设为只读语义状态，并生成 `frozen-test.json` 内容摘要；之后任何 test 标注或 state 变化都会导致校验失败。`--profile fixture` 仅供脱敏小夹具测试，不得用于真实质量结论。

### 3. validation 校准与冻结质量门

```bash
uv run python -m backend.evaluation.calibrate calibrate \
  --dataset backend/data/private_evaluation/target-v1 \
  --calibration-version target-v1-cal1 \
  --vector-thresholds 0,0.1,0.2,0.3 \
  --evidence-thresholds 0.2,0.4,0.6,0.8 \
  --answerability-thresholds 0.2,0.4,0.6,0.8 \
  --output backend/outputs/evaluation/target-v1-cal1.json

uv run python -m backend.evaluation.calibrate frozen-test \
  --dataset backend/data/private_evaluation/target-v1 \
  --calibration backend/outputs/evaluation/target-v1-cal1.json \
  --output backend/outputs/evaluation/target-v1-test-cal1.json
```

校准代码只允许读取 `validation`。每个向量阈值只运行一次 reranker shadow，Evidence/answerability 组合从同一批分数离线重放；报告保留 BM25/hybrid 候选、调优方案与 embedding/reranker 降级场景。冻结 test 必须已冻结，且数据版本、embedding/reranker 模型、分块配置和候选配置必须与 calibration artifact 完全一致。成功或失败都写入 `frozen-test-run.json`，同一数据集不能换输出路径重复试跑。

冻结硬门槛为 Candidate Recall@20 ≥98%、Recall@6 ≥95%、macro Precision@6 ≥70%、MRR ≥0.85、Evidence coverage ≥90%、无答案正确拒答率 ≥90%、可回答错误拒答率 ≤5%、章节跨界率/非法引用保留率为 0、p95 ≤1.5 秒。reranker 相对最佳无 rerank 基线 Precision 不足 10 个百分点时，报告强制建议 `disabled`。

上线仍按 `disabled → shadow → enabled`。离线通过后，shadow 至少收集 500 次检索或运行一周；只记录 reranker 延迟、降级率、top score、平均返回数和排序变化，不记录论文正文或完整问题。

### 当前 pilot 摘要

专家裁决结果中有 122 条 `accept_for_pilot_only` 和 86 条 `reject_or_reannotate`；这些决定不会回写 `cases.jsonl`，也不等于正式人工 gold。Pilot 入口只使用其中 20 条 validation 可接受样本，明确跳过 analysis 调参之外的数据和全部 test；test 尚未冻结，也未运行 frozen-test。

最后一次已保存结论仍是 `pilot-only-v2`：真实端点不可用且有 7 项硬门失败。`pilot-only-v3` 实现已加入 preflight、持久索引和单次 shadow 校准，但只有在区域、workspace 与 API Key 匹配并实际生成新报告后才能更新质量结论。无论 v3 是否通过，生产继续保持 `ASK_RERANKER_MODE=disabled`；全部 validation 硬门通过前不得冻结或运行 test。

顶层字段：

| 字段 | 含义 |
| --- | --- |
| `version` | 数据集版本；修改既有样本语义或标注时必须更新。 |
| `chunks` | 写入临时 state 的合成论文分块。 |
| `cases` | 检索与拒答评估样本。 |

每个 case 包含：

| 字段 | 含义 |
| --- | --- |
| `id` | 稳定且唯一的样本 ID。 |
| `query` | 用户检索问题。 |
| `section` | 可选章节约束；检索前应用。 |
| `relevant_chunk_ids` | 可正确回答查询的 chunk ID 集合。 |
| `should_refuse` | 论文范围内无答案时设为 `true`。 |
| `allowed_evidence` | 允许作为回答证据的 chunk ID 集合。 |

新增样本时应同时覆盖英文和中文，并优先增加真实失败类型，例如同义表达、多正确 chunk、相邻章节干扰和领域外无答案。无答案查询不能仅依赖停用词或标点造成偶然零命中。

## 运行模式与降级

`hybrid` 与 `filtered-hybrid` 使用小型确定性概念向量，只为 CI 稳定地覆盖向量召回、过滤和 RRF 路径。前者是故意保留无效向量结果的原始对照，后者是当前质量门检查的实验基线。它们不是生产 embedding 的质量替代品，也不能用来比较真实 embedding 模型。

当前实验夹具为 filtered-hybrid 使用 `1.0` 的最低余弦相似度，而生产配置默认值是保守的 `0.0`。因此，实验结果只能说明“过滤无效候选”这一方向在固定夹具上有效，不能代表生产默认配置已经达到同等质量。阈值需要在独立验证集上按实际 embedding 模型校准，再用未参与调参的测试集报告最终结果。

报告同时记录每个 case 和全局的 BM25 候选数、原始向量候选数、过滤后向量候选数、移除数和 RRF 唯一候选数。`comparison` 给出 filtered-hybrid 相对原始 hybrid 与 BM25 的 Recall、MRR、覆盖率、噪声率、拒答率和 p50/p95 延迟差值。

`degraded` 会让 embedding 建索引失败，并确认线上检索降级为 BM25。报告会保留：

- `requested_mode`: 请求运行的模式；
- `effective_mode`: 实际生效的模式；
- `degraded_reasons`: 不含上游敏感详情的降级原因；
- `baseline_eligible`: 是否可计入对应基线。

降级报告的 `baseline_eligible` 为 `false`，因此 `--gate` 不会把它误当成 hybrid 基线。它仍会输出指标，便于观察降级后的质量。

## 指标口径

所有检索指标只统计前 6 个结果：

| 指标 | 计算口径 | 趋势 |
| --- | --- | --- |
| `recall_at_6` | 对每个可回答样本计算前 6 条命中的相关 chunk 比例，再取宏平均。 | 越高越好 |
| `candidate_recall_at_20` | 对每个可回答样本计算融合候选前 20 条的相关 chunk 宏平均召回。 | 越高越好 |
| `precision_at_6` | 对每个可回答样本计算前 6 条宏平均准确率。 | 越高越好 |
| `mrr` | 每个可回答样本首个相关 chunk 排名的倒数，再取宏平均。 | 越高越好 |
| `evidence_coverage` | 被召回的允许 Evidence 数量除以全部允许 Evidence 数量。 | 越高越好 |
| `noise_rate` | 前 6 条中非相关 chunk 数量除以全部返回 chunk 数量。 | 越低越好 |
| `section_boundary_rate` | 返回结果中章节不等于请求章节的比例。 | 越低越好 |
| `no_answer_refusal_rate` | `should_refuse=true` 的样本中，检索零命中并进入现有拒答路径的比例。 | 越高越好 |
| `answerable_false_refusal_rate` | 可回答样本被错误拒答的比例。 | 越低越好 |
| `average_returned_count` | 每题平均返回 Evidence 数量。 | 用于监控 |

报告还按语言、可回答性、章节约束和干扰类型输出 `group_metrics`。生产冻结测试硬门槛为 candidate Recall@20 ≥98%、final Recall@6 ≥95%、macro Precision@6 ≥70%、MRR ≥0.85、Evidence coverage ≥90%、无答案拒答率 ≥90%、错误拒答率 ≤5%，章节跨界率和非法引用保留率均为 0，检索 p95 ≤1500ms。
| `illegal_citation_retention_rate` | 引用清理后仍保留的非法 Evidence ID 除以注入的非法 ID 数量。 | 越低越好 |
| `latency_p50_ms` / `latency_p95_ms` | 单个 case 完成检索的墙钟耗时分位数。 | 仅记录 |

噪声率会把返回的所有非标注 chunk 视作噪声，因此它依赖标注完备性。延迟是在本机合成小数据集上的进程内耗时，适合观察明显回归，不代表生产请求延迟。

当前 `ask-paper-v2` 的一次确定性质量结果为：BM25 噪声率 `55.56%`，原始 hybrid 为 `75.93%`，filtered-hybrid 为 `53.57%`；filtered-hybrid Recall@6 为 `100%`。虽然相对比较满足现有门槛，但超过一半的返回 Evidence 仍被标注为噪声，所以本阶段不能据此视为完成。原始 hybrid 在近似无答案样本上的拒答率也只有 `50%`，说明仅依赖正相似度不足以判断答案是否存在。

## CI 质量门

首版硬门槛如下：

- Recall@6 ≥ 90%；
- 章节跨界率 = 0%；
- 非法引用保留率 = 0%；
- 无答案拒答率 = 100%；
- filtered-hybrid Recall@6 不低于原始 hybrid；
- filtered-hybrid 噪声率不高于 BM25，且严格低于原始 hybrid。

MRR、证据覆盖率和延迟只记录；噪声率目前仅通过相对基线关系阻断 CI，尚未设置绝对上限。固定评估集也由 `backend/tests/test_ask_paper_evaluation.py` 执行，因此常规后端 pytest 已包含质量门。

## 当前结论与后续工作

合成夹具已承担 CI 回归职责，真实私有评估的 Schema、生成/校验/校准 CLI、论文级 split、test 冻结保护和硬质量门也已完成。真实 preflight 当前因 embedding 与 reranker 均超时而阻止 v3 Pilot，最后保存的 v2 结论仍有 7 项失败；因此不能启用 reranker，也不能访问 test。

项目主线先转向[文档检索增强 MVP](../../docs/document-retrieval-enhancement.md)：复用现有 BM25/向量/RRF 和持久索引，补齐任务内搜索 API、前端搜索面板、Evidence 联动、相邻 chunk 上下文与离线演示。恢复真实端点、扩充正式 gold 和继续 Pilot 作为后续加分项，不阻塞这一功能闭环。

建议 CI 同时执行：

```bash
uv run pytest backend/tests -q
uv run python -m backend.evaluation.ask_paper \
  --mode all \
  --gate \
  --output backend/outputs/logs/ask-paper-eval.json
```

将 JSON 作为 CI artifact 保存，可用于比较不同提交的质量、延迟与失败样本。任何修改数据集版本、检索参数或离线向量规则的提交，都应在评审说明中记录原因，避免新旧基线被直接混比。
