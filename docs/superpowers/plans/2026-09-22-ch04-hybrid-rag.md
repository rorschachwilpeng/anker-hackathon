# ch04 混合检索、重排与评估 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保留现有 `query_faq` 契约和聊天 SSE，在 Milvus 2.6.16 上上线中文 BM25 + dense RRF + BGE rerank、可核验引用/拒答和四策略评估。

**Architecture:** 并行建 `knowledge_hybrid_v1`，旧 `knowledge` 在线服务至全量核验和原子切换完成；MySQL 始终是原文权威源。单轮检索改写 → 双路各 Top-50 → RRF → MySQL 回查 → 云端 rerank Top-10 → 有用性自评 → 受约束的 SSE 回答。离线评估记录召回、忠实度和编造台账。

**Tech Stack:** Python 3.12+、FastAPI 0.141、SQLAlchemy 2.0/aiomysql/MySQL 8.4、LangChain Core/OpenAI、PyMilvus/Milvus Standalone 2.6.16、硅基流动 BGE-M3 与 `BAAI/bge-reranker-v2-m3`、原生 JS/CSS。

**Spec:** `docs/superpowers/specs/2026-09-22-ch04-hybrid-rag-design.md`

**Status:** 计划待用户审阅；所有以下代码和测试是实施目标，尚未执行。

## Global Constraints

- 用户提供的 `low_confidence_questions`、`faith_cases` DDL 精确落地；不更改旧表数据，初始化仅建缺失表并严格校验；`SET NAMES utf8mb4`。
- 现有 `query_faq(keyword)` 1–64 字符、返回 `keyword/found/items`，每项仅 `id/question/answer/category`；不做 Agent Loop、指代消解或多轮改写。
- Milvus 原生 `chinese` analyzer + BM25 Function、双路各 Top-50、`hybrid_search` + `RRFRanker`；不换外部全文引擎。云端 rerank `BAAI/bge-reranker-v2-m3` 输出 Top-10，本机不下载模型。
- 密钥仅 `.env`，不输出、提交或写进报告；查询用户真实对话或离线历史挖掘不作为合成评估输入。
- 低置信度池只写 `useful=false` 的 `source=self_check`；UI 反馈只在浏览器会话，`faith_cases` 仅由离线 Faithfulness 裁判写。
- 后端每项 TDD RED→GREEN；纯 Prompt/合成数据用标注集验证；前端直接按 Vibe Coding 做，手动浏览器验收。
- 每项任务结束立即追记 `dev-notes/ch04.md` 四项（用户原话、产出/路径、拒绝/纠偏、翻车/返工）并提交该任务相关文件；代码评审结论和 finish 单独即时追记。
- 上线新集合前必须保留旧集合与现有可用数据；不执行 `down -v`、drop 旧表/集合；如果 SDK/固定选型不兼容则停下问用户。

## Review Focus

1. 老知识来源修改或删除与迁移交错：任务 3 锁内快照、断点恢复和切换前 hash/主键校验。
2. 型号中数字、大小写和否定词被改写丢失：任务 4 的保留约束及 BM25 原话通路。
3. RRF 分数被误当作概率，或空证据仍生成政策承诺：任务 5/6 对阈值和 `useful` 的边界测试。
4. SSE 已发出回答后出现越界引用或断线：任务 6 验证编号、`answer_reset`、不保存无效回答与唯一入池。
5. 来源 ID/路径可被猜测并读取任意本机文件：任务 6 的权威库校验、路径白名单、无文件来源展示。

## 文件与共享接口

工作目录默认 `ecommerce-support/`，文档/记录在仓库根。沿用用户此前明确选择的 Native 执行方式：计划获审后由当前会话按 `superpowers:executing-plans` 逐项实现，最终做独立整枝 review；执行前读取 spec、确认分支/状态、按 `superpowers:using-git-worktrees` 选择隔离。当前已有工作区的 `.env` 和 Docker 卷保持原状。

| 文件 | 单一责任 |
| --- | --- |
| `sql/ch04-schema.sql`, `app/knowledge/quality_models.py`, `scripts/init_ch04.py` | 两表严格 DDL/ORM 元数据和安全初始化 |
| `app/knowledge/hybrid_vectors.py`, `app/knowledge/migration.py`, `scripts/migrate_ch04.py` | 混合集合、读写和旧集合幂等迁移/切换 |
| `app/knowledge/query.py`, `app/knowledge/hybrid_retrieval.py`, `app/knowledge/reranker.py` | 单轮问法、双路检索、权威回查、云端精排 |
| `app/knowledge/evidence.py`, `app/knowledge/quality.py`, `app/knowledge/quality_repository.py` | 证据编排、自评、引用/拒答和两表读写 |
| `app/chat_service.py`, `app/main.py`, `app/tools/business.py`, `app/prompts.py`, `app/config.py` | 注入在线服务、单轮 SSE、详情接口、提示词和配置 |
| `evals/ch04/*.jsonl`, `scripts/evaluate_ch04.py`, `app/knowledge/faithfulness.py` | 合成标注、四策略和忠实度判定/台账 |
| `app/static/chat.js`, `app/static/chat.css`, `app/static/index.html` | 引用查看与一次性本地满意度反馈 |

共享内部类型及约定（`query_faq` 外部结构不变）：

```python
@dataclass(frozen=True)
class Candidate:
    id: int
    score: float
    content_hash: str
    rank: int

@dataclass(frozen=True)
class Evidence:
    n: int
    chunk_id: int
    section_path: str | None
    question: str
    answer: str

# QueryUnderstanding.normalize(raw: str) -> QueryPlan(canonical, synonyms, category)
# HybridVectors.retrieve(canonical, lexical, vector, category, strategy, limit=50) -> list[Candidate]
# Reranker.rank(query, rows, top_n=10) -> list[RankedChunk]
# EvidenceGate.check(raw_question, evidence) -> Useful(useful: bool, reason: str)
# KnowledgeSearch.find(keyword) -> list[dict]  # 旧四字段仍唯一公开字段
```

## Task 1：建表、严格校验与最小存储接口

**Files:** Create `sql/ch04-schema.sql`, `app/knowledge/quality_models.py`, `app/knowledge/quality_repository.py`, `scripts/init_ch04.py`, `tests/knowledge/test_ch04_schema.py`, `tests/knowledge/test_quality_repository.py`.

**Interfaces:** `initialize_ch04(db) -> None`; `QualityRepository.record_low(message_id, conversation_id, raw_question, reason) -> int` 幂等；`record_faith_case(case) -> None`; `resolve(eval_id,status,resolution) -> None`。

- [ ] Context7 再核对 SQLAlchemy `on_duplicate_key_update`、异步事务/Inspector；RED：`test_ch04_schema` 比对每列类型、默认值、ENUM、索引/外键/utf8mb4、`faith_cases.citations` JSON，可重复建表且保留已有行；`test_record_faith_case_reopens_resolved` 与 `test_record_low_once` 先失败。Run: `.venv/bin/python -m pytest tests/knowledge/test_ch04_schema.py tests/knowledge/test_quality_repository.py -q`，预期因模块不存在而 FAIL。
- [ ] 将用户 SQL 的两段 `CREATE TABLE` 与 `SET NAMES utf8mb4` 原文保存为 `sql/ch04-schema.sql`；以 SQLAlchemy Core 声明完整对应元数据，不通过 `create_all()` 更改已有表。新增初始化脚本沿用 ch03 的安全校验风格，逐表只 CREATE 缺失者，再核对所有字段、键、ENUM、默认值、engine/charset；错误中止，不自动修复旧表。

```python
from sqlalchemy.dialects.mysql import insert
stmt = insert(faith_cases).values(**values)
await session.execute(stmt.on_duplicate_key_update(
    answer=stmt.inserted.answer, reason=stmt.inserted.reason,
    citations=stmt.inserted.citations, judge_model=stmt.inserted.judge_model,
    status='未解决', resolution=None, seen_count=faith_cases.c.seen_count + 1,
    last_seen_at=func.current_timestamp(),
))
```

- [ ] 低置信写入把 `message_id` 作为现有 `knowledge_ingestion_state` 中 `quality:low:<id>` 键；同一 MySQL 事务先插入唯一状态键（并发重复键回滚后重新读），锁住该行，再插池并更新状态 payload 中的池行 id；重复只返回原 id。`faith_cases` 复发保留 `resolved_at`、清空 `resolution`，处置时强制非空说明。Run: 上述定向测试 GREEN，然后 `.venv/bin/python -m pytest tests/test_schema_validation.py -q`；即时追记任务 1 并精确提交文件。

## Task 2：版本化 Milvus 原生 BM25 集合

**Files:** Create `app/knowledge/hybrid_vectors.py`, `tests/knowledge/test_hybrid_vectors.py`; Modify `tests/knowledge/test_milvus_integration.py`。

**Interfaces:** `HybridVectors.ensure_collection()`, `upsert(rows)`, `retrieve(canonical, lexical, vector, category, strategy, limit=50)`, `delete(ids)`；`strategy ∈ {'dense','bm25','hybrid'}`。

- [ ] Context7 核对当前版本 `DataType`, `Function/FunctionType.BM25`, `AnnSearchRequest`, `RRFRanker`, `AsyncMilvusClient.hybrid_search`。RED：schema 必含 `text` 中文 analyzer、`sparse` 及 BM25 Function；两路请求各 limit=50 且同 filter；schema 不符拒绝启动；断言 BM25 data 为原话文本，dense data 为 1024 维向量。Run: `.venv/bin/python -m pytest tests/knowledge/test_hybrid_vectors.py -q` → FAIL。

```python
schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
schema.add_field('id', DataType.INT64, is_primary=True)
schema.add_field('vector', DataType.FLOAT_VECTOR, dim=1024)
schema.add_field('text', DataType.VARCHAR, max_length=6000,
                 enable_analyzer=True, analyzer_params={'type': 'chinese'})
schema.add_field('sparse', DataType.SPARSE_FLOAT_VECTOR)
schema.add_field('category', DataType.VARCHAR, max_length=1024)  # MySQL 255 字符，Milvus 按 UTF-8 字节计
schema.add_field('content_hash', DataType.VARCHAR, max_length=64)
schema.add_function(Function(name='text_bm25', function_type=FunctionType.BM25,
                             input_field_names=['text'], output_field_names=['sparse']))
indexes = client.prepare_index_params()
indexes.add_index(field_name='vector', index_type='FLAT', metric_type='COSINE')
indexes.add_index(field_name='sparse', index_type='SPARSE_INVERTED_INDEX', metric_type='BM25')
```

- [ ] `upsert` 只提交 `id/vector/text/category/content_hash`，稀疏字段由 Milvus 派生；`retrieve` 构造两个 `AnnSearchRequest`，每个带同一参数化/白名单生成的 `expr`，`hybrid_search(..., ranker=RRFRanker(), limit=50)`；纯路用 `search`，统一转 Candidate。输出空、重复、NaN 和不合法 ID 直接拒绝；超时按现有 `VectorError` 契约映射。Run: 定向测试 GREEN；在 `test_milvus_integration.py` 的隔离集合实测中文型号可由 BM25 查到，测试结束仅删自己的随机集合；记录精确 PyMilvus/Milvus 版本和探针结果，立即追记并提交。

## Task 3：无损重建与活动集合切换

**Files:** Create `app/knowledge/migration.py`, `scripts/migrate_ch04.py`, `tests/knowledge/test_hybrid_migration.py`; Modify `app/knowledge/ingestion.py`, `scripts/knowledge.py`, `app/main.py`。

**Interfaces:** `migrate_hybrid(repo, old_vectors, new_vectors, state, failpoint=None) -> MigrationReport`; `repo.all_done_after(last_id,limit) -> list[ChunkSnapshot]`；`active_collection(state) -> str`；CLI `prepare|verify|activate|status`。任务 2 的新集合已可用。

- [ ] RED：旧集合服务保持可读、迁移 `after-upsert` 中断并重跑只留同一 id、更新来源和删除 tombstone、缺失旧向量重编码、校验失败不得 activate、激活后 writer 写新集合。Run: `.venv/bin/python -m pytest tests/knowledge/test_hybrid_migration.py -q` → FAIL。
- [ ] 锁内 `repo.pending()` 先完成旧集合修复；分批 `repo.load_done(ids)`，旧集合 `client.query(collection_name='knowledge', ids=ids, output_fields=['id','vector','content_hash'])` 后逐条比对 hash，缺失的由 BGE-M3 重新编码；新集合用同一 id 幂等 upsert，并用 `StateStore.put('migration:ch04', {'last_id': last_id, 'target': 'knowledge_hybrid_v1'})` 断点。严格校验全量 id/hash/条数及两条中文检索样例后，以 writer 锁内一次事务更新 `active_collection` 状态键；失败保持 `knowledge`。所有普通 writer 从同一活动集合状态读，避免 stale 进程写旧集合。

```python
async with state.writer_lock():
    await state.assert_owned()
    report = await migrate_hybrid(repo, old_vectors, new_vectors, state)
    if not report.passed:
        raise KnowledgeError('hybrid_migration_verification_failed')
    await state.put('active_collection', {'name': 'knowledge_hybrid_v1'})
```

- [ ] Run: 定向测试 GREEN；`scripts/migrate_ch04.py status` 只读不改数据；用现有演示资料做 prepare→verify→activate 集成验证，验证旧集合仍存在、两集合主键一致和中断可恢复。测试后不 drop 旧集合；立即追记并提交。

## Task 4：单轮问法理解和双路权威召回

**Files:** Create `app/knowledge/query.py`, `app/knowledge/hybrid_retrieval.py`, `tests/knowledge/test_hybrid_retrieval.py`; Modify `app/knowledge/retrieval.py`, `app/tools/business.py`。

**Interfaces:** `QueryPlan(canonical, synonyms, category)`，`lexical` 由原话+canonical+synonyms 生成；`KnowledgeSearch.find(keyword)` 原有四字段不变；内部 `find_evidence(keyword, category=None, strategy='hybrid')` 返回有序权威 ChunkSnapshot 与 score。

- [ ] Context7 核对 LangChain `with_structured_output` 和 Milvus filter `expr_params`，RED：型号 `X100-Pro`、数值、否定词不能从 lexical 丢失；无明确 category 时不推断强过滤；恶意 category 不能拼表达式；只有 done/hash 一致的 MySQL 行进入结果。Run: `.venv/bin/python -m pytest tests/knowledge/test_hybrid_retrieval.py -q` → FAIL。

```python
class QueryPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    canonical: str = Field(min_length=1, max_length=128)
    synonyms: list[str] = Field(max_length=3)
    category: str | None = None

lexical = ' '.join(dict.fromkeys([raw, plan.canonical, *plan.synonyms]))
rows = await repo.load_done([hit.id for hit in hits])
valid = [rows[h.id] for h in hits if h.id in rows and rows[h.id].content_hash == h.content_hash]
```

- [ ] 对原话做单轮结构化改写，返回校验后保留型号/数字/否定词的 canonical 和最多 3 个同义词；非法或失败保留原话，不把提示注入当 category；category 只允许数据库已有分类精确值，参数化传 Milvus 两路 expr。`query_faq` 仍调用 `find` 和旧 `pack_faq_result`，不加新公开字段；评估可调用内部策略接口。Run: 定向测试 GREEN 和 `tests/knowledge/test_retrieval.py tests/test_tools.py`；即时追记并提交。

## Task 5：云端重排、Top-10 与证据顺序

**Files:** Create `app/knowledge/reranker.py`, `app/knowledge/evidence.py`, `tests/knowledge/test_reranker.py`, `tests/knowledge/test_evidence.py`; Modify `app/config.py`, `.env.example`, `app/knowledge/hybrid_retrieval.py`。

**Interfaces:** `Reranker.rank(query, rows, top_n=10) -> list[RankedChunk]`；`pack_evidence(rows, byte_budget) -> list[Evidence]`；配置 `RERANK_BASE_URL/RERANK_MODEL/RERANK_API_KEY`，默认同 SiliconFlow 端点、固定模型。

- [ ] Context7 确认硅基流动 `/v1/rerank` 请求/响应；RED：乱序 index 正确对应原 chunk、重复/越界/NaN score 拒绝，429/5xx 总超时内有界重试，预算丢弃只能整块，精排顺序首尾交错后重编号，不能把 RRF 分数套旧 COSINE 阈值。Run: `.venv/bin/python -m pytest tests/knowledge/test_reranker.py tests/knowledge/test_evidence.py -q` → FAIL。

```python
payload = {'model': 'BAAI/bge-reranker-v2-m3', 'query': query,
           'documents': [embedding_text(row.draft) for row in rows],
           'top_n': min(10, len(rows)), 'return_documents': False}
ranked = [(rows[r['index']], float(r['relevance_score'])) for r in body['results']]
for row in ranked[::2]: front.append(row)
for row in ranked[1::2]: back.insert(0, row)
evidence = [Evidence(n=i, chunk_id=row.id, section_path=row.draft.section_path,
                     question=row.draft.questions, answer=row.draft.answer)
            for i, row in enumerate([*front, *back], 1)]
```

- [ ] 从 `.env` 加载私密 key，失败不回退并报工具不可用；设独立经校准的 rerank 证据阈值，接受单个可配置正负评估校准输出；后端预留知识工具总 timeout 覆盖改写+嵌入+hybrid+rerank，避免默认 20s 被误算成上游 15s 即可靠。Run: 定向 GREEN、现有 retrieval/tool 相关回归；即时追记并提交。

## Task 6：有用性自评、拒答、引用和原文详情

**Files:** Create `app/knowledge/quality.py`, `tests/knowledge/test_quality_gate.py`, `tests/knowledge/test_citation_route.py`; Modify `app/chat_service.py`, `app/prompts.py`, `app/main.py`, `app/routes/chat.py`, `app/knowledge/repository.py`。

**Interfaces:** `EvidenceGate.check(question,evidence) -> Useful`；SSE `citations: {items:[{n,chunk_id,section_path}]}` 及 `done.message_id`（已保存助手消息的主键）；`GET /knowledge/chunks/{chunk_id}` 返回 ID、章节路径、完整 answer、来源定位（若安全可用）。旧 `query_faq` JSON 不变。

- [ ] Context7 核对 FastAPI `StreamingResponse` 和 LangChain 结构化输出。RED：无证据/低置信→`useful=false`→明确拒答并只入池一次；自评超时不可擅自通过；生成答案的 `[11]` 越界/缺角标/引用错误不能持久化；已流出无效文本用 SSE `answer_reset` 再输出拒答；详情不存在/非 done/目录外路径返回 404，合法原文可查。Run: `.venv/bin/python -m pytest tests/knowledge/test_quality_gate.py tests/knowledge/test_citation_route.py tests/test_chat_tools.py -q` → FAIL。

```python
class Useful(BaseModel):
    model_config = ConfigDict(extra='forbid')
    useful: bool
    reason: str = Field(min_length=1)

judgment = await model.with_structured_output(
    Useful, method=settings.structured_output_method,
).ainvoke([SystemMessage(content=QUALITY_PROMPT), HumanMessage(content=evidence_text)])
if not judgment.useful:
    await quality_repo.record_low(user_message_id, conversation_id, raw_question, judgment.reason)
    yield StreamEvent('token', {'text': '目前的知识资料不足以确认这个问题，建议联系人工客服核实。'})
```

- [ ] 只在 query_faq 成功回灌后调用自评；生成 prompt 带完整 `Evidence[n]` 和禁止退款到账时间、到货时间、赔付金额、人工已处理等承诺；引用编号映射由服务内部保存并发 `citations` 帧。流式引用 token 缓冲到闭合 `]` 时核验，结束时核验至少一个有效引用和 `finish_reason=stop`；不合格用 `answer_reset` + 明确拒答，不将候选保存为 assistant 消息。保存最终回答后将 `append_message` 返回主键放进 `done.message_id`，未保存的 `length` 回复不发该字段。只读详情基于 `repo.load_done` 和受控来源映射，不向浏览器暴露任意本地绝对路径。Run: 定向 GREEN + `tests/test_chat_routes.py tests/test_api.py`；即时追记并提交。

## Task 7：合成标注、四策略数值、Faithfulness 与台账

**Files:** Create `evals/ch04/calibration.jsonl`, `evals/ch04/holdout.jsonl`, `app/knowledge/faithfulness.py`, `scripts/evaluate_ch04.py`, `tests/knowledge/test_evaluate_ch04.py`, `tests/knowledge/test_faith_cases.py`; Modify `knowledge/demo/products.md`, `README.md`。

**Interfaces:** CLI `evaluate_ch04.py --dataset evals/ch04/holdout.jsonl --output ../dev-notes/evals/ch04/report.json`；`recall_at_k`, `mrr`, `faithfulness` 纯函数；`FaithJudge.score(query,answer,evidence)` 逐事实结构化判断。

- [ ] 数据先做标注评估：政策、相近型号、口语、跨两条证据、无答案/对抗、category filter 各至少 3 条，校准与留出不重复；固定 gold chunk 源身份/文本 hash，由脚本解析运行时 id，而不是依赖本机自增 id。RED：两相关块 Recall@K、首相关 MRR、无答案不计虚假正例、不同策略同样候选上限、证据快照含全部 Top-K 而非仅引用；台账同题复发 count/status/resolution/时间正确。Run: `.venv/bin/python -m pytest tests/knowledge/test_evaluate_ch04.py tests/knowledge/test_faith_cases.py -q` → FAIL。

```python
def recall_at_k(gold: set[int], ranked: list[int], k: int) -> float:
    return len(gold.intersection(ranked[:k])) / len(gold) if gold else 1.0 if not ranked[:k] else 0.0

def mrr(gold: set[int], ranked: list[int]) -> float:
    return next((1.0 / i for i, ident in enumerate(ranked, 1) if ident in gold), 0.0)

for strategy in ('dense', 'bm25', 'hybrid', 'hybrid_rerank'):
    result[strategy] = await evaluate_retrieval(cases, strategy=strategy, leg_limit=50)
```

- [ ] 输出每题原始 query/gold IDs、四策略排名、Top-K、引用、judge 原始结构化判词、失败和延迟；按桶及总计 Recall@1/5/10、MRR、无答案误命中率。Faithfulness 只对 hybrid_rerank 最终生成逐事实裁判；有编造则 `record_faith_case`，包含送入生成的所有证据快照；无法解析裁判结果单列 error，不以通过记。先对 Prompt/数据用标注例跑一遍，然后定向测试 GREEN；即时追记并提交。

## Task 8：聊天页引用查看与本地一次性反馈（Vibe Coding）

**Files:** Modify `app/static/chat.js`, `app/static/chat.css`, `app/static/index.html`。

**Interfaces:** 处理任务 6 的 `citations` SSE；点击 `[n]` 调 `GET /knowledge/chunks/{chunk_id}`；反馈只写 `sessionStorage`/页面状态，不提交服务端。

- [ ] 按用户要求直接改界面：流中保持纯文本节点；收到有效映射与 `done` 后只把 `[n]` 转换成安全 `button` 节点，弹层展示章节路径、完整原文和安全的项目内定位；不存在来源显示“暂无原文件路径”。每段助手回答左下角 👍/👎，点击一次记录当前浏览器会话 key、点亮所选、显示“已反馈”、禁用两按钮；不发网络请求。

```javascript
const button = document.createElement('button');
button.type = 'button';
button.textContent = `[${citation.n}]`;
button.addEventListener('click', () => showCitation(citation.chunk_id));
sessionStorage.setItem(`feedback:${conversationId}:${messageId}`, choice);
```

- [ ] 浏览器手动验证来源弹层、键盘/关闭、未知编号仍是普通文本、跨轮映射不串、刷新后同一回答不被错误重复反馈、新对话独立；错误/停止生成不出现可点虚构引用或误锁反馈。无独立前端 TDD/code review 门禁；即时追记任务 8 和人工验收结果，精确提交 UI 文件。

## Task 9：真实集成、四策略报告、回归与评审

**Files:** Modify `README.md`, `dev-notes/ch04.md`；Write `dev-notes/evals/ch04/report.json`；若发现真实缺陷，只修改对应任务文件并写 RED/GREEN/返工记录。

- [ ] 用 `scripts/init_ch04.py` 验证两表与 existing rows；`scripts/migrate_ch04.py prepare/verify/activate`，插入 failpoint 真实中断再重跑；旧集合未删、总量/hash、中文 BM25 型号题均通过。只导入合成演示资料，不对历史真实聊天执行挖掘。Run: `.venv/bin/python -m pytest -q` 和标记集成测试，记录本轮真实计数。
- [ ] Run: `.venv/bin/python scripts/evaluate_ch04.py --dataset evals/ch04/holdout.jsonl --output ../dev-notes/evals/ch04/report.json`；报告必须含四策略逐桶 Recall@K/MRR、Faithfulness、拒答和引用率、型号 BM25 原始 hit、裁判版本与失败数。人工浏览器问精确型号、无知识问题，核对 SSE 逐 token/引用原文/池中同一原话一次；记录完整演示命令，失败项如实标明。
- [ ] 按 `superpowers:requesting-code-review` 做一次独立整枝评审，按 `receiving-code-review` 处理反馈；复测后立即追加 code review 结论记录。最后按 `verification-before-completion` 核实本轮测试、报告、Git 状态，再追加 finish 记录，提交，只交付真实跑出的数值、演示命令和 `dev-notes/ch04.md` 路径。

## 计划自审清单

- [ ] 对照 spec 每段逐项映射至任务：任务 1 表；2 原生 BM25；3 幂等迁移；4 改写/双路与过滤；5 重排/顺序；6 自评/拒答/引用；7 评估与台账；8 UI；9 真实验收。
- [ ] 扫描占位符、共享接口名称和五类 Review Focus 的测试所属任务；对照 `query_faq` 旧契约与两张用户 DDL，不添加公开字段或秘密日志。
- [ ] 用户审阅并认可计划后沿用 Native，逐任务按 RED/GREEN/阶段记录执行；当前文件不代表已跑过任何 ch04 测试。

## 附录：任务 1 的用户指定 DDL

保存到 `sql/ch04-schema.sql`；列名、类型、ENUM、索引和外键不得自行改造，注释沿用用户消息原文。初始化器按本文件逐条执行并额外校验 COMMENT。

```sql
SET NAMES utf8mb4;

CREATE TABLE low_confidence_questions (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  conversation_id BIGINT UNSIGNED NULL COMMENT '来源会话',
  raw_question    TEXT            NOT NULL COMMENT '用户原话,带情绪口语',
  source          ENUM('retrieval_low_conf','self_check','user_feedback') NOT NULL COMMENT '入池入口:检索证据低 / 生成自评不足 / 用户反馈未解决',
  reason          TEXT            NULL COMMENT '判不能的原因,留作复盘',
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入池时间',
  PRIMARY KEY (id),
  KEY idx_source (source),
  KEY idx_created_at (created_at),
  CONSTRAINT fk_lcq_conversation FOREIGN KEY (conversation_id) REFERENCES conversations (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='低置信度问题池';

CREATE TABLE faith_cases (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  eval_id       VARCHAR(16)     NOT NULL COMMENT '评估集题号,如 A43;一题一行',
  bucket        VARCHAR(24)     NOT NULL COMMENT '题目所属桶:A_policy / B_model / C_colloquial / E_multi',
  query         VARCHAR(512)    NOT NULL COMMENT '用户问题原文',
  strategy      VARCHAR(24)     NOT NULL DEFAULT 'hybrid_rerank' COMMENT '产出这条答案的检索策略',
  answer        TEXT            NOT NULL COMMENT '被判编造的那版生成答案原文',
  reason        TEXT            NOT NULL COMMENT '裁判给的理由:编在哪一句',
  citations     JSON            NULL COMMENT '这一轮喂给模型的 Top-K 证据全集快照:[{n,chunk_id,section_path,question,answer}];答案里的角标 [n] 就是这份列表的序号,答案通常只引用其中两三条;老数据没记为 NULL',
  judge_model   VARCHAR(64)     NULL COMMENT '判这条的裁判模型',
  status        ENUM('未解决','已解决','无需解决') NOT NULL DEFAULT '未解决' COMMENT '处置状态,人工点按钮改',
  seen_count    INT UNSIGNED    NOT NULL DEFAULT 1 COMMENT '被判编造的累计次数(跨轮)',
  first_seen_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '第一次被判编造的时间',
  last_seen_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近一次被判编造的时间',
  resolution    VARCHAR(300)    NULL COMMENT '处置说明:标已解决要写清怎么解决的,标无需解决要写清为什么不用改;退回未解决时清空。空着的处置在台账上等于没有交代',
  resolved_at   DATETIME        NULL COMMENT '最近一次被标为已解决/无需解决的时间;复发后仍保留,用来标「复发」',
  PRIMARY KEY (id),
  UNIQUE KEY uk_eval_id (eval_id),
  KEY idx_status (status),
  KEY idx_last_seen_at (last_seen_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ch04 忠实度编造个案台账';
```
