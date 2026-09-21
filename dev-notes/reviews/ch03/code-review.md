# ch03 独立代码评审

## 范围

- 基线：`f7ec87d8218733cef7b58e6e37792bc7417a3251`
- 初审 HEAD：`cd4fa569afde852a42ad73e370bed14880fe9af9`
- 方式：独立只读审查代理；未读取 `.env`，未修改代码。

## 结论

最终通过。初审及复审共发现 8 项 Important，无 Critical；全部用失败测试复现并整改，最终复审未发现新的 Critical/Important。

| 问题 | 整改结果 |
|---|---|
| LLM 可省略同轮工具消息，绕过 mock/失败来源校验 | 按完整轮次校验问题与最终回答，核对 tool_call 配对并解析 `ok/error`；拒绝 mock、失败、损坏和跨轮证据 |
| 外部模型调用期间写锁丢失后仍可提交 | 每个批次返回后及所有写事务入口重新 `assert_owned` |
| 文档重导入抹掉已接受问法 | 重放有效 merge 映射；政策事实变化或目标删除时，将暂存与映射转 review 并移除旧问法 |
| 不同事实只因短答案相同被自动合并 | 仅完整问答重复走确定性 discard；其余进入分页 dense Top-5 + LLM 判断 |
| 超长抽取/合并写成无法向量化的 pending | 去重编码前和仓储写入前双重限制，超限保持 extracted 并标 review |
| 单轮超限仍发给模型，且会丢弃同来源短轮次 | 从模型窗口扣除输出、margin、Prompt/Schema；只跳过超限轮次并保留前后完整短轮次 |
| 后续批次失败导致前批全部重做 | 每批事务提交稳定 checkpoint；同 run_id 恢复复用首次扫描快照、批次和来源指纹 |
| 旧 discarded 行阻止后来有效证据入库 | 无 dedup 决策的旧 rejected 行可原子升级为 extracted 并刷新证据；再次重跑不重复 |

## 验证

- 最终代码回归：`171 passed`。
- 抽取标注集：12/12，正例与安全负例准确率均 1.0。
- 去重标注集：8/8，错误合并 0。
- 合成历史挖掘：首次云端模型未抽取，第二次通过；重复运行未新增暂存或知识。
- fresh recovery：after-mysql / after-milvus 均通过，报告 `dev-notes/evals/ch03/recovery-task10.json`。
- SSE：`query_faq` succeeded，逐 token 输出完整运费政策并收到 done；第一次上游返回 unfinished，重试通过。
