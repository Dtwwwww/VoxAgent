# JR-04 Hybrid RAG 与评测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有纯向量知识检索升级为可解释、可降级、可量化的本地 Hybrid RAG，并用固定中文数据集证明召回、引用和延迟。

**Architecture:** 文档入库后同时进入现有 BGE 向量列和 SQLite FTS5 trigram 索引。查询并行取得 vector top-20 与 BM25 top-20，用 RRF 合并为 8 个候选，再由 CPU ONNX INT8 多语言 Cross Encoder 重排为 top-5。Reranker 不可用或超时时保留 RRF 结果；回答层只允许引用最终候选中的稳定 chunk ID。

**Tech Stack:** SQLite FTS5、NumPy、现有 BGE-small-zh、ONNX Runtime、Hugging Face tokenizers、mMARCO multilingual MiniLM INT8、Typer、pytest

## Global Constraints

- 数据库 Schema 从 JR-03 的 version 5 升级到 version 6。
- 使用 SQLite 自带 FTS5，不引入 Elasticsearch、Milvus 或另一个常驻数据库。
- RRF 常数固定为 `60`，候选数固定为 `8`，最终 top-k 最大为 `5`。
- Reranker 固定使用 CPUExecutionProvider，单批最多 8 对，应用内最多一个重排批次并发。
- ONNX 权重固定为 `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` 的 `onnx/model_quint8_avx2.onnx`，校验 SHA-256 `6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b`。
- 模型下载到 `D:\VoxAgentData\models\rerankers`，不进入 Git。
- 任何检索失败都不应破坏原始文档、向量或 FTS 索引；Reranker 失败必须显式标记降级。
- 每个任务独立提交。

---

### Task 1: 校验 FTS5 并增加 trigram 索引迁移

**Files:**
- Modify: `backend/src/voxagent/db/migrations.py`
- Create: `backend/src/voxagent/knowledge/fts.py`
- Modify: `backend/tests/db/test_migrations.py`
- Test: `backend/tests/knowledge/test_fts.py`

**Interfaces:**
- Schema version: `6`
- `ensure_fts5_available(connection) -> None`
- `FtsKnowledgeIndex.search(query: str, limit: int) -> tuple[LexicalHit, ...]`

- [ ] **Step 1: 写运行时能力测试**

```python
def test_python_sqlite_has_fts5_and_trigram(connection) -> None:
    connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text, tokenize='trigram')")
    connection.execute("DROP TABLE probe")
```

如果目标 Python 的 SQLite 不支持该语句，启动 preflight 必须报告 blocking issue `sqlite_fts5_trigram_unavailable`，不得静默改用不可用的中文 `unicode61` 分词。

- [ ] **Step 2: 写迁移测试**

测试从版本 0、3、4、5 升级至 6；迁移后已有 chunk 可检索，新增、更新和删除 chunk 时索引同步。迁移失败必须回滚到原版本。

- [ ] **Step 3: 创建外部内容 FTS 表和触发器**

```sql
CREATE VIRTUAL TABLE document_chunks_fts USING fts5(
    content,
    content='document_chunks',
    content_rowid='id',
    tokenize='trigram'
);
CREATE TRIGGER document_chunks_ai AFTER INSERT ON document_chunks BEGIN
    INSERT INTO document_chunks_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER document_chunks_ad AFTER DELETE ON document_chunks BEGIN
    INSERT INTO document_chunks_fts(document_chunks_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER document_chunks_au AFTER UPDATE OF content ON document_chunks BEGIN
    INSERT INTO document_chunks_fts(document_chunks_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
    INSERT INTO document_chunks_fts(rowid, content) VALUES (new.id, new.content);
END;
INSERT INTO document_chunks_fts(document_chunks_fts) VALUES ('rebuild');
```

- [ ] **Step 4: 实现 BM25 查询边界**

查询先 Unicode NFKC、去控制字符、trim，并限制 200 字符。少于 3 个 code point 时返回空 lexical 集，由向量路径处理。用户文本始终作为绑定参数传给 `MATCH`，双引号转义后组成单个 phrase；结果按 `bm25(document_chunks_fts)` 升序、rowid 升序确定性排序。

- [ ] **Step 5: 运行并提交**

Run: `cd backend; uv run pytest tests/db/test_migrations.py tests/knowledge/test_fts.py -v`

Expected: PASS，中文无空格查询可命中且触发器同步。

```powershell
git add backend/src/voxagent/db/migrations.py backend/src/voxagent/knowledge/fts.py backend/tests/db/test_migrations.py backend/tests/knowledge/test_fts.py
git commit -m "feat: index knowledge chunks with SQLite FTS5"
```

### Task 2: 实现确定性 RRF 融合

**Files:**
- Create: `backend/src/voxagent/rag/__init__.py`
- Create: `backend/src/voxagent/rag/models.py`
- Create: `backend/src/voxagent/rag/fusion.py`
- Test: `backend/tests/rag/test_fusion.py`

**Interfaces:**
- `RankedCandidate(chunk_id, vector_rank, lexical_rank, rrf_score)`
- `reciprocal_rank_fusion(vector_ids, lexical_ids, *, k=60, limit=8) -> tuple[RankedCandidate, ...]`

- [ ] **Step 1: 写融合公式测试**

```python
def test_rrf_merges_duplicate_candidates_and_uses_one_based_ranks() -> None:
    result = reciprocal_rank_fusion((10, 20), (20, 30), k=60, limit=8)
    assert [item.chunk_id for item in result] == [20, 10, 30]
    assert result[0].rrf_score == pytest.approx((1 / 62) + (1 / 61))
```

补充空列表、单通道、重复 ID、相同分数按 chunk ID、非法 `k` 和 `limit` 的测试。

- [ ] **Step 2: 实现纯函数**

rank 从 1 开始，每个通道内同一 ID 只取第一次出现；分数为所有通道 `1 / (k + rank)` 之和。排序键固定为 `(-rrf_score, best_rank, chunk_id)`，输出不超过 8。

- [ ] **Step 3: 运行并提交**

Run: `cd backend; uv run pytest tests/rag/test_fusion.py -v`

Expected: PASS。

```powershell
git add backend/src/voxagent/rag backend/tests/rag/test_fusion.py
git commit -m "feat: fuse lexical and vector retrieval with RRF"
```

### Task 3: 固定并下载轻量 Reranker

**Files:**
- Create: `backend/src/voxagent/rag/reranker_models.json`
- Create: `backend/src/voxagent/rag/reranker_manifest.py`
- Create: `scripts/download-reranker.ps1`
- Test: `backend/tests/rag/test_reranker_manifest.py`

**Interfaces:**
- Model ID: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`
- Revision: `173760f`
- Files: `config.json`, `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json`, `sentencepiece.bpe.model`, `onnx/model_quint8_avx2.onnx`

- [ ] **Step 1: 写 manifest 校验测试**

测试 model ID、revision、必需文件集合、权重 SHA-256、最大 150 MiB 权重体积和 Apache-2.0 license 字段。`validate_reranker_directory()` 缺文件、哈希错误或符号链接越出模型目录时必须失败。

- [ ] **Step 2: 增加固定 manifest**

manifest 把 revision 固定为 `173760f`，权重最大允许值固定为 `157286400` 字节，权重 SHA 使用 Global Constraints 中的完整值。其余小文件由不可变 revision 固定，下载完成后记录实际 byte size 和 SHA 到 `download-record.json`；该 record 位于数据根且不提交。

- [ ] **Step 3: 实现幂等下载脚本**

脚本逐个下载以下固定形式的 URL：

```text
https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1/resolve/173760f/{file}
```

先写入数据根内 `.partial` 文件，验证 HTTP 状态、文件非空和 ONNX SHA 后用 `Move-Item -LiteralPath` 原子替换。已有合法文件不重复下载；失败只删除对应 `.partial`，不删除合法模型。

- [ ] **Step 4: 运行离线测试**

Run: `cd backend; uv run pytest tests/rag/test_reranker_manifest.py -v`

Expected: PASS；单元测试使用临时文件，不访问网络。

- [ ] **Step 5: 在目标机器下载并校验**

Run: `powershell -ExecutionPolicy Bypass -File scripts/download-reranker.ps1 -DataRoot D:\VoxAgentData`

Expected: 六个文件存在、ONNX SHA 匹配，脚本打印 `Reranker ready`。

- [ ] **Step 6: 提交**

```powershell
git add backend/src/voxagent/rag/reranker_models.json backend/src/voxagent/rag/reranker_manifest.py backend/tests/rag/test_reranker_manifest.py scripts/download-reranker.ps1
git commit -m "build: pin multilingual ONNX reranker"
```

### Task 4: 实现 CPU ONNX 重排器

**Files:**
- Create: `backend/src/voxagent/rag/reranker.py`
- Test: `backend/tests/rag/test_reranker.py`
- Test: `backend/tests/rag/test_reranker_model.py`

**Interfaces:**
- `OnnxCrossEncoder.from_directory(path) -> OnnxCrossEncoder`
- `OnnxCrossEncoder.rerank(query, candidates, limit=5) -> tuple[RerankedCandidate, ...]`
- `UnavailableReranker.rerank(query, candidates, limit=5)` raises `RerankerUnavailable`

- [ ] **Step 1: 写 fake session 单元测试**

用可注入 fake tokenizer/session 验证 query-passage 成对编码、384 token 截断、padding mask、batch 最大 8、logit 降序和相同 logit 按 RRF/chunk ID 破同分。空候选不调用 session。

- [ ] **Step 2: 实现资源边界**

加载 `tokenizer.json`，启用 pair truncation 到 384；只创建 `onnxruntime.InferenceSession` 的 `CPUExecutionProvider`，`intra_op_num_threads=4`、`inter_op_num_threads=1`。用应用级 `threading.Semaphore(1)` 包裹推理，等待超过 350ms 即抛 `RerankerBusy` 供上层降级。

- [ ] **Step 3: 解析固定输入输出**

只接受模型输入 `input_ids` 和 `attention_mask`，若模型声明 `token_type_ids` 则补全零矩阵。输出必须为 `(batch, 1)` 或 `(batch,)` 的有限 float；NaN、Infinity、错误 shape 和缺失输入都标记模型不可用。

- [ ] **Step 4: 增加真实模型 smoke test**

测试标记 `model`，从 `VOXAGENT_DATA_ROOT` 加载真实模型；查询“声灵支持什么文档格式”，相关 passage 必须排在无关天气 passage 前。普通 CI 不运行此标记。

- [ ] **Step 5: 运行并提交**

Run: `cd backend; uv run pytest tests/rag/test_reranker.py -v`

Expected: PASS。

Run: `cd backend; uv run pytest tests/rag/test_reranker_model.py -v -m model`

Expected on target machine: PASS，单批峰值 RSS 增量写入测试日志且低于 600 MiB。

```powershell
git add backend/src/voxagent/rag/reranker.py backend/tests/rag/test_reranker.py backend/tests/rag/test_reranker_model.py
git commit -m "feat: rerank hybrid results with CPU ONNX"
```

### Task 5: 组装 Hybrid Knowledge Retriever

**Files:**
- Create: `backend/src/voxagent/rag/retriever.py`
- Modify: `backend/src/voxagent/conversation/context.py`
- Modify: `backend/src/voxagent/cli.py`
- Test: `backend/tests/rag/test_retriever.py`
- Modify: `backend/tests/conversation/test_context.py`

**Interfaces:**
- `HybridKnowledgeRetriever.search(query: str, limit: int = 5) -> HybridSearchResult`
- `HybridSearchResult(items, retrieval_mode, reranker_used, degraded_reason, duration_ms)`
- Retrieval modes: `hybrid_reranked`, `hybrid_rrf`, `vector_only`

- [ ] **Step 1: 写组装和降级测试**

覆盖双通道融合、lexical 空、vector 空、Reranker busy、Reranker 模型损坏、数据库异常和同分确定性。数据库异常返回明确失败，不把“数据库不可用”伪装成空结果；只有可选通道失败时允许降级。

- [ ] **Step 2: 并行检索**

在调用方 worker thread 中使用两个只读 SQLite connection，并发执行现有 `SqliteVectorRetriever.search_document_chunks(top_k=20)` 与 `FtsKnowledgeIndex.search(limit=20)`。每个 connection 设置 1 秒 busy timeout；候选 ID 交给 RRF 后一次查询补齐 chunk/document 元数据。

- [ ] **Step 3: 接入上下文**

将 `SqliteContextSource.search_knowledge()` 的内部实现替换为 `HybridKnowledgeRetriever`，保留原有 `KnowledgeContext` 和 token/字符预算，避免改动上层 Prompt 合同。来源增加内部 `retrieval_mode` 供日志和评测使用，但不把分数或系统字段展示给模型。

- [ ] **Step 4: 启动降级策略**

`serve` 启动时校验 Reranker。缺失或损坏时允许启动为 `hybrid_rrf` 并输出单条脱敏 warning；FTS5 不可用属于 blocking preflight；BGE 不可用仍按现有生产规则阻止完整模式启动。

- [ ] **Step 5: 运行并提交**

Run: `cd backend; uv run pytest tests/rag/test_retriever.py tests/conversation/test_context.py -v`

Expected: PASS；现有 context 注入和引用测试不回归。

```powershell
git add backend/src/voxagent/rag/retriever.py backend/src/voxagent/conversation/context.py backend/src/voxagent/cli.py backend/tests/rag/test_retriever.py backend/tests/conversation/test_context.py
git commit -m "feat: assemble degradable hybrid knowledge retrieval"
```

### Task 6: 建立 50 条中文 RAG 金标集

**Files:**
- Create: `qa/fixtures/rag-source/voxagent-user-guide.md`
- Create: `qa/fixtures/rag-source/voice-troubleshooting.md`
- Create: `qa/fixtures/rag-source/security-boundaries.md`
- Create: `qa/scenarios/rag-questions.json`
- Create: `backend/src/voxagent/rag/evaluation.py`
- Test: `backend/tests/rag/test_evaluation.py`

**Interfaces:**
- Dataset fields: `id`, `question`, `gold_document`, `gold_chunk_ordinal`, `answerable`, `expected_answer_terms`
- Metrics: `recall_at_5`, `mrr`, `citation_accuracy`, `refusal_accuracy`, `latency_p50_ms`, `latency_p95_ms`

- [ ] **Step 1: 编写公开、可提交语料**

三份文档只描述本项目的运行、语音排错和安全边界，不含个人数据、密钥或机器绝对路径。固定 chunking 配置后，测试断言每份文档的 SHA-256 和 gold ordinal 不漂移。

- [ ] **Step 2: 编写问题集**

数量固定为：30 条直接事实、10 条同义/口语化查询、5 条跨段干扰、5 条不可回答。每条可回答问题只有一个主 gold chunk；不可回答项 `gold_document` 和 `gold_chunk_ordinal` 为 null。

- [ ] **Step 3: 实现指标纯函数**

Recall@5 只统计可回答问题；MRR 使用第一个 gold chunk 的倒数排名；citation accuracy 要求回答引用集合非空且全部属于最终 top-5；不可回答问题必须没有 citation 且输出拒答标记。所有分母和四舍五入规则写入报告 Schema 测试。

- [ ] **Step 4: 增加 deterministic answer adapter**

CI 模式从 gold chunk 提取 `expected_answer_terms` 并生成固定 `[知识片段 chunk_id]` 引用，用于验证评测管道本身；报告必须标记 `answer_mode="deterministic"`，不能作为真实模型成绩。

- [ ] **Step 5: 运行并提交**

Run: `cd backend; uv run pytest tests/rag/test_evaluation.py -v`

Expected: PASS；fixture 管道得到 50/50 deterministic success。

```powershell
git add qa/fixtures/rag-source qa/scenarios/rag-questions.json backend/src/voxagent/rag/evaluation.py backend/tests/rag/test_evaluation.py
git commit -m "test: add fixed Chinese RAG evaluation set"
```

### Task 7: 增加评测 CLI 和对比报告

**Files:**
- Modify: `backend/src/voxagent/cli.py`
- Create: `qa/reports/rag-report.schema.json`
- Create: `docs/portfolio/rag-evaluation.md`
- Test: `backend/tests/rag/test_evaluation_cli.py`

**Interfaces:**
- CLI: `voxagent evaluate-rag --dataset PATH --source-dir PATH --output PATH --mode deterministic|local`
- Report modes: `vector`, `hybrid_rrf`, `hybrid_reranked`

- [ ] **Step 1: 写 CLI 合同测试**

临时数据根中导入三份 fixture，分别运行三个 retrieval mode，验证报告包含 Git commit、UTC 时间、数据集 SHA、模型 ID、model revision、每题排名、降级原因和聚合指标。输出已存在时必须要求 `--overwrite`。

- [ ] **Step 2: 实现对比运行**

同一次命令按 vector、hybrid_rrf、hybrid_reranked 顺序运行，第一次作为冷启动不进入延迟分位数，随后每题重复 3 次并记录中位数。`local` 模式调用 Qwen3 4B 生成答案和引用；温度固定 0，context 8K，答案找不到时必须拒答。

- [ ] **Step 3: 加入硬门槛**

CLI 在 final mode 下若 `hybrid_reranked` 的 Recall@5 小于 0.85、MRR 小于 0.75、citation accuracy 小于 0.90、refusal accuracy 小于 0.80 或 warm p95 大于 500ms，则 exit 1。deterministic 模式只校验管道，不声明真实模型门槛。

- [ ] **Step 4: 生成本地结构报告**

Run: `cd backend; uv run voxagent evaluate-rag --dataset ../qa/scenarios/rag-questions.json --source-dir ../qa/fixtures/rag-source --output ../qa/reports/rag-deterministic.json --mode deterministic`

Expected: 50 cases，三种 retrieval mode 均有结果，报告通过 JSON Schema。

- [ ] **Step 5: 完整验证并提交**

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1 -Scope All`

Expected: `Verification passed.`，exit 0。

```powershell
git add backend/src/voxagent/cli.py backend/tests/rag/test_evaluation_cli.py qa/reports/rag-report.schema.json qa/reports/rag-deterministic.json docs/portfolio/rag-evaluation.md
git commit -m "feat: report hybrid RAG quality and latency"
```

## JR-04 Completion Gate

- FTS5 trigram preflight、迁移、触发器同步和中文命中测试通过。
- RRF 公式、tie-break 和 8 候选上限为确定性行为。
- Reranker 权重小于 150 MiB、SHA-256 匹配、仅 CPU、batch 不超过 8、并发不超过 1。
- Reranker 失败时结果显式降级到 `hybrid_rrf`，数据库错误不伪装为空结果。
- 50 条中文数据集、三种 retrieval mode 和 JSON Schema 报告可复现。
- 真机 `local` 指标留给 JR-06，但评测 CLI 已能执行全部硬门槛。
- `scripts/verify.ps1 -Scope All` 通过。
