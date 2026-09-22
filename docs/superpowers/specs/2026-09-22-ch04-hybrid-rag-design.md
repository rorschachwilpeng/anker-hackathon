# ch04 混合检索、重排与评估设计

状态：用户以“执行”确认书面设计，进入实施计划编写；产品代码尚未开始。

## 目标与边界

现有客服聊天保留单轮工具调用、SSE 逐 token 回复和 `query_faq(keyword: str)` 的参数名及外层 `keyword/found/items` 契约。把知识检索从 BGE-M3 dense 单路升级为 Milvus 原生 BM25 + dense 双路各 Top-50、`hybrid_search` + RRF、云端 `BAAI/bge-reranker-v2-m3` 精排 Top-10。商品型号等精确词要能由 BM25 命中；不同说法由检索侧改写与同义词扩展覆盖，知识原文仍只存一份。

本章不做指代消解、多轮改写、Agent Loop、用户反馈入库或自动修复知识。订单/商品/物流工具仍是 ch02 的 mock。BGE-M3 继续使用硅基流动云端嵌入；rerank 使用同服务商的 `/v1/rerank`，密钥放 `.env`，不在本机部署模型。MySQL 和 Milvus Standalone 继续沿用现有本地 Docker 部署。

用户提供的两张 MySQL 表 `low_confidence_questions` 与 `faith_cases` 的列、ENUM、默认值、索引、外键、注释和 InnoDB/utf8mb4 均按原样实现；现有 ch02/ch03 表与数据保留。`source` 虽有 `retrieval_low_conf/self_check/user_feedback` 三种合法值，本章只在生成前证据自评 `useful=false` 时写 `self_check`；检索为空、低分或证据不足是触发自评的理由，不绕过 `useful` 判定直接入池。前端反馈只保存在当前浏览器端，不写 `user_feedback`。

## 索引与无损迁移

现有 `knowledge` 集合只有 `id/vector/content_hash`，无法凭一次查询添加建集合时才定义的 BM25 函数。新建版本化集合 `knowledge_hybrid_v1`：`id` 为显式 INT64 主键，`vector` 为 1024 维 FLOAT_VECTOR（COSINE），`text` 为带内置 `chinese` analyzer 的 VARCHAR，`sparse` 为 SPARSE_FLOAT_VECTOR，另存 `category` 与 `content_hash` 供预过滤和 MySQL 原文校验。Milvus VARCHAR 上限按 UTF-8 字节留足空间，不把 MySQL 的字符数误作字节数。`text` 精确等于已有固定的 `category + questions + answer` 嵌入文本；只在该单份文本挂 Milvus BM25 函数，由服务端派生稀疏向量。章节路径、内容类型、关键条款和邻块指针只在 MySQL，不进 dense 文本以外的向量，也不作为重复记录存储。

通过独立迁移命令在已有知识 writer 锁下读取 MySQL 有效 chunk，先补齐 pending，再分批从旧集合按主键读取已验证的 BGE-M3 向量（缺失时重新编码），将同一主键、vector、text/category/hash 幂等写入新集合；重启后可继续，断点保存在现有 `knowledge_ingestion_state` 命名空间。保留旧集合及旧服务读取路径，直至新集合的条数、主键/hash、一条精确型号和中文问法的 BM25 检索及双路结果检查通过。切换在数据库状态记录里原子标记 active collection；新服务只在状态和新集合结构、索引均匹配时读取它，切换失败仍用旧集合。清理旧集合须另行显式操作，本章不自动删除。在线改写导入和定时 worker 与迁移共用锁，并在切换后向活动集合写入，避免旧 writer 写入一个在线不可见的集合。`knowledge_chunks.vectorize_status/vector_id` 的语义仍是当前活动集合对应主键，迁移中不提前标 done；旧集合继续提供服务。

新集合原生 BM25、中文 analyzer、FLAT/COSINE dense 索引和 SPARSE_INVERTED_INDEX/BM25 索引的版本及 SDK 行为在实现前用 Context7 和本地 2.6.16 集成探针校验；如固定选型与当前版本确实冲突，停止并向用户说明，不偷换词库或外部搜索引擎。数据写入仍遵守 MySQL 原文权威、状态 pending → Milvus 同主键 upsert → MySQL 回填 done 的恢复序列；中断后能补齐 text、dense 和 BM25 派生数据，删除 tombstone 也同步作用于活动集合。

## 在线检索与重排

`query_faq` 接收原有 `keyword`，为当前单条问题产生标准问法和少量同义词；改写仅用于本轮检索，不写回 MySQL 或 Milvus，不读取历史对话做指代消解。改写必须保留订单号、商品型号、数量和否定词；结构化结果异常时保留原问法。密钥、模型名、超时都由配置读取。dense 对标准问法编码；BM25 文本查询含原话及受控同义词，分别用 `AnnSearchRequest(limit=50)`，同一个经过枚举/参数化校验的品类 filter 施加到两路请求，使过滤发生在召回之前。`hybrid_search` 用 `RRFRanker` 融合，候选最多 100 个去重主键；纯 dense、纯 BM25 与 hybrid 实现共享同一索引和 filter 语义，以便评估可比。

在进入 rerank 前批量回查 MySQL，只接受 `done` 且 `content_hash` 一致的权威原文；过期向量不传给 reranker。将候选原文送硅基流动 `BAAI/bge-reranker-v2-m3`，按返回 index 对应候选，检验唯一、范围和有限分数，截取相关性 Top-10。失败不静默退回旧策略并宣称重排成功；工具暴露可诊断错误，在线答复不编造知识。纯策略与混合加重排共用同一标注集，在评估报告中记录模型、集合、配置、用时和失败。

配置一个经校准的 rerank 证据阈值（不同于旧 dense COSINE 阈值），并把无命中、低分、证据不充分交给统一自评；RRF 分数不能被当作概率或直接沿用旧阈值。Top-10 是重排输出上限，进入模型上下文前按 token/字节预算取完整块，不截断条款；预算不足则拒答。为抑制“lost in the middle”，按相关性名次交替置于证据列表首尾，编号 `[1]` 到 `[n]` 由最终送入 prompt 的位置重编号；原分数名次单独保留供评估。不得将仅检索到但未送入生成的块列为引用。

## 证据、自评、拒答和引用

商家知识问题仍由 `query_faq` 路由。工具结果**严格保留**原有外层 `keyword/found/items` 以及 item 的 `id/question/answer/category`，不额外添加公开字段；章节路径和引用信息通过服务内部的本轮证据对象及单独 SSE 元数据帧传递。生成前对本轮最终证据做一次结构化自评 `{useful: bool, reason: str}`，只判断是否足够回答用户原话；prompt 明确不得用模型常识代替商家政策。自评失败/格式异常属于服务错误，不能当作 `useful=true`；确定 `useful=false` 时返回统一明确拒答，按原话、来源会话、`source=self_check`、理由写池，且自评结果不另建 useful 字段。必须避免网络重试或客户端中止造成同一轮重复入池；为此在线轮次使用消息/会话的唯一键在现有状态记录中去重，表本身不增加列。自评完成后再开始最终 SSE token 流，保留工具状态帧。

System Prompt 列明不可承诺退款或到账时间、物流到达时间、赔付金额、人工处理完成等负面知识。答案中的 `[n]` 只允许指向本轮实际送入 prompt 的完整知识块；服务端记录编号到 chunk 主键的映射，在生成完校验引用存在且不越界，不能验证的引用视为不完整回答并拒答，不回显虚构引文。引用映射通过 SSE 的受控元数据帧供聊天页消费；原文详情由只读接口按 chunk ID 回 MySQL 的 done 记录，返回章节路径、原文、来源标识。文档来源可由现有 ingestion state 的来源映射定位到项目知识文档，再展示章节锚点/原文件路径；FAQ 或挖掘所得知识没有可跳转文件时，详情中的权威 chunk 即“原文”，不虚构 URL。接口对不在允许知识目录下的路径不提供文件内容，避免暴露本机任意文件。

`faith_cases` 是**离线评估**发现编造后的持久台账，生成时不调用忠实度裁判。按 `eval_id` 一题一行 upsert：再次判编造更新最近答案/理由/完整送入证据快照、`seen_count+1`、`last_seen_at`；原来已解决则退回未解决、清空处置说明但保留 `resolved_at` 供复发标识。处置操作要求填写非空 `resolution`；评估跑出忠实答案时不自动删除历史个案。引用快照 `[ {n,chunk_id,section_path,question,answer} ]` 包含**送入模型的全部证据**，并不只记录答案实际引用的两三条。

## 评估与前端

从演示政策、售后手册和含相近型号的商品 FAQ 扩充**合成**标注集，分政策精确问法、型号字面词、口语同义词、多证据组合、无答案/对抗问法和元数据过滤等难度桶；每题固定 query、可接受 chunk ID 集、答案证据要点与是否应拒答，评估集不混入校准集。四策略在同一索引快照和相同每路 Top-50 / 输出 K 上运行：dense、BM25、hybrid RRF、hybrid + rerank。输出每桶和总体 Recall@K、MRR、无答案误命中率、延迟/失败数；生成段只以 hybrid + rerank 的最终证据生成，逐事实给忠实度裁判评 Faithfulness，同时记录拒答正确率、引用有效率及 `faith_cases` 新增/复发情况。报告保存原始逐题结果、评委模型/提示词、时间与知识版本，不只给汇总百分比。含明确型号的题须能看到 BM25 路的原始命中证据。

聊天页按用户指定的 Vibe Coding 方式直接迭代，不套独立的 brainstorm/TDD/code review 门禁。回答 `[n]` 呈现为可点击引用，点开可看 MySQL 权威原文、章节路径和可用的来源定位；流式输出期间先显示文本，拿到完整引用映射后再变成安全 DOM 链接，不把模型输出当 HTML。完成帧附上已保存助手消息的 id 供前端本地反馈去重，不改变 `query_faq` 契约。每段回答左下角加 👍/👎，本地一次性点亮、显示“已反馈”并锁定；仅在浏览器当前会话采集，不提交后端，也不写低置信度池。UI 验收用实际浏览器手动演示点击、刷新与停止生成行为。

## 验收、回归和阶段记录

实现前逐项通过 Context7 核对 PyMilvus 2.6 的 schema/function/analyzer/hybrid_search、SQLAlchemy 异步事务与 upsert、FastAPI SSE/路由、LangChain 结构化自评，以及硅基流动 rerank API；目前已查 Milvus/PyMilvus 的 BM25、`AnnSearchRequest`/`RRFRanker`、SiliconFlow rerank 的基础契约，实施时按所用接口继续查。官方参考：[Milvus BM25](https://milvus.io/docs/full-text-search.md)、[Milvus Hybrid Search](https://milvus.io/docs/multi-vector-search.md)、[SiliconFlow Rerank](https://docs.siliconflow.cn/docs/api/rerank-post)。

后端 TDD 覆盖 schema、迁移中断重跑/不丢旧服务、中文型号 BM25、双路 Top-50/RRF/过滤、rerank 索引与分数校验、旧工具契约、证据完整性、空证据和低分自评、拒答入池幂等、引用映射/原文访问安全、台账复发及解决状态。纯 Prompt 和合成数据用标注评估集验证；代码测试通过后，再用本机 MySQL/Milvus、真实云端嵌入/rerank、聊天 SSE 和浏览器端到端验证四策略数字与四项验收标准。受限网络或服务配额造成的未实测项必须如实报告，不假称通过。

每完成 brainstorm 定稿、计划评审、各任务、最终 code review 和 finish，立即追记 `dev-notes/ch04.md`，四项均写：用户关键原话、产出/路径/结论、拒绝或纠偏、翻车与返工。不在收尾补写阶段记录。最后交付演示命令、测试与评估数值和该记录路径。
