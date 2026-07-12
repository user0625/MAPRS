# Ask Paper 离线质量评估

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

当前状态是“过滤方向得到验证，但检索降噪阶段尚未完成”。下一轮应：

1. 让默认阈值 `0.0` 与调优阈值作为不同实验组同时报告；
2. 拆分验证集和测试集，避免对固定合成夹具调参；
3. 增加 Precision@6、每题返回数量，以及可回答/拒答样本的分组指标；
4. 为噪声率设置绝对目标，而不只要求优于 BM25；
5. 分析过滤后仍存在的正分排序失败样本，再决定是否增加可选 Cross-encoder reranker。

建议 CI 同时执行：

```bash
uv run pytest backend/tests -q
uv run python -m backend.evaluation.ask_paper \
  --mode all \
  --gate \
  --output backend/outputs/logs/ask-paper-eval.json
```

将 JSON 作为 CI artifact 保存，可用于比较不同提交的质量、延迟与失败样本。任何修改数据集版本、检索参数或离线向量规则的提交，都应在评审说明中记录原因，避免新旧基线被直接混比。
