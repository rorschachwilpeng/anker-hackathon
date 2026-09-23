# 已验收客服底座迁入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将队友已验收的运行项目迁入正式开发分支，并证明迁入后的版本仍能完成现有知识查询和工单创建。

**Architecture:** 原导入分支保持不变。Technical Builder 从 `origin/codex/product-agent-development` 建立短期实施分支 `codex/anker-s1pro-implementation`，只复制 `09-workspace/customer-support/ecommerce-support/` 目录。在迁入目录使用已有本地依赖与私有配置，启动独立端口，运行现有回归；用户审查该分支的验证结果后再并入开发集成线。安克知识和 Agent 改造放在后续计划，不混入本次基线提交。

**Tech Stack:** Python 3.12+、FastAPI、LangChain、MySQL、Milvus、Docker Compose、pytest。

**执行分工：** 用户是本版唯一人类开发负责人，负责确认范围、理解关键实现并验收；现有 Technical Builder（线程 ID `01a0b4e1-f4df-7133-8075-03f34c242983`）负责执行本计划中的代码迁入、运行验证和后续技术实现。不向知识库队友或新建 Builder 派单。

## Global Constraints

- `codex/product-agent-development` 是正式集成线；`codex/customer-support-import` 保留为原始验收基线。Technical Builder 在自己的工作树使用短期分支 `codex/anker-s1pro-implementation` 实施，不在用户的 `5648` 工作树中改代码；通过验证后由用户决定何时并入集成线。
- 只迁入 `09-workspace/customer-support/ecommerce-support/`；不合并整个导入分支，不删除其他分支。
- 不覆盖当前工作树中的 `.DS_Store` 或 `spec/002-Anker分支职责与代码流转.md`，不清理现有 MySQL/Milvus 数据卷。
- `.env` 包含本地密钥，只能留在被 `.gitignore` 忽略的本机目录，绝不提交或打印密钥值。
- 原验收实例占用 `127.0.0.1:8000`；迁入版本使用 `127.0.0.1:8001`，便于区分两套代码。
- 本计划只验收通用电商底座。S1 Pro 资料、知识检索契约、任务状态及工单交接另行实施；不能把原有电商回复宣称为安克切片通过。

---

### Task 1: 迁入运行项目并重跑基线

**Files:**
- Create: `09-workspace/customer-support/ecommerce-support/`（内容来自 `origin/codex/customer-support-import` 的同一路径）
- Preserve: `spec/002-Anker分支职责与代码流转.md`、当前工作树的其他未提交文件
- Read: `09-workspace/customer-support/ecommerce-support/README.md`

**Interfaces:**
- Consumes: `origin/codex/customer-support-import` 中已验收的运行项目。
- Produces: 正式开发分支中目录结构、文件内容与导入分支对应路径一致的可启动底座；现有 `/chat` 与工单工具行为不变。

- [ ] **Step 1: 核对分支、源目录和未提交内容**

  Run from Technical Builder 的工作树根目录：

  ```bash
  git status --short --branch
  git fetch origin codex/product-agent-development codex/customer-support-import
  git ls-tree -d --name-only origin/codex/customer-support-import 09-workspace/customer-support/ecommerce-support
  git switch -c codex/anker-s1pro-implementation origin/codex/product-agent-development
  ```

  Expected: 原工作树未提交文件得到记录并保留；源目录存在；新分支基于最新设计提交。若同名实施分支已存在，先核对它是否属于本任务，不覆盖或重建。

- [ ] **Step 2: 只复制运行项目**

  ```bash
  git restore --source=origin/codex/customer-support-import --worktree -- 09-workspace/customer-support/ecommerce-support
  git status --short
  ```

  Expected: 新增的仅为 `09-workspace/customer-support/ecommerce-support/` 下文件；任一工作树的原未提交文件均未被修改或清除。

- [ ] **Step 3: 复用已配置的本地密钥，不将其写入 Git**

  以下仅适用于两个路径确实位于同一主机的情况。先检查源文件存在、目标文件不存在，再复制私有配置；若路径不共享，要求用户在 Builder 工作树本地配置，不从聊天中索取密钥。不得输出文件内容：

  ```bash
  test -f /Users/pengyoutian/.codex/worktrees/5648/customer-support-validation/09-workspace/customer-support/ecommerce-support/.env
  test ! -e 09-workspace/customer-support/ecommerce-support/.env
  cp -p /Users/pengyoutian/.codex/worktrees/5648/customer-support-validation/09-workspace/customer-support/ecommerce-support/.env 09-workspace/customer-support/ecommerce-support/.env
  git -C 09-workspace/customer-support/ecommerce-support check-ignore .env
  ```

  Expected: 输出 `.env`，证明 Git 忽略该文件。若验证工作区没有 `.env`，停止配置步骤并请用户在新目录填写，不猜测或索取密钥。

- [ ] **Step 4: 运行迁入后的现有测试**

  在 `09-workspace/customer-support/ecommerce-support/` 中使用已验收工作区的虚拟环境 Python 运行。若该路径在 Builder 主机不可访问，则在迁入目录建立本地虚拟环境并安装 `requirements.lock` 中的依赖，再使用该目录的 Python；不得把虚拟环境提交到 Git。

  ```bash
  /Users/pengyoutian/.codex/worktrees/5648/customer-support-validation/09-workspace/customer-support/ecommerce-support/.venv/bin/python -m pytest -q
  ```

  Expected: exit code 0；如有失败，记录具体测试与迁入差异，不修改导入分支。数据库相关测试若显示跳过，不能记作数据库验收通过。

- [ ] **Step 5: 启动迁入实例并检查健康状态**

  在同一目录中启动独立服务，保留进程供下一步验收：

  ```bash
  /Users/pengyoutian/.codex/worktrees/5648/customer-support-validation/09-workspace/customer-support/ecommerce-support/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
  ```

  在另一个终端运行：

  ```bash
  curl -fsS http://127.0.0.1:8001/health
  ```

  Expected: `{"status":"ok"}`；原 `8000` 实例保持不变。若启动失败，先记录错误与环境差异，不删除数据库卷。

- [ ] **Step 6: 在新端口运行现有端到端回归**

  在迁入项目目录运行：

  ```bash
  /Users/pengyoutian/.codex/worktrees/5648/customer-support-validation/09-workspace/customer-support/ecommerce-support/.venv/bin/python scripts/evaluate.py --base-url http://127.0.0.1:8001 --output /tmp/anker-import-baseline-results.json
  ```

  Expected: `failed=0`，报告位于 `/tmp/anker-import-baseline-results.json`；回归覆盖原有聊天、知识查询、会话续接和工具/工单行为。若 `failed>0`，先查工具调用和环境配置，不直接开始安克功能改造。

- [ ] **Step 7: 检查差异并提交迁入底座**

  ```bash
  git add 09-workspace/customer-support/ecommerce-support
  git diff --cached --check
  git diff --cached --stat
  git commit -m 'chore: import validated customer support baseline'
  git status --short --branch
  ```

  Expected: 提交只包含运行项目；`.env`、原有未提交文件不在提交内。

- [ ] **Step 8: 推送实施分支并交付验证证据**

  ```bash
  git push -u origin codex/anker-s1pro-implementation
  git rev-parse HEAD
  ```

  Expected: 远端实施分支指向刚才的迁入提交。Technical Builder 向用户展示提交哈希、测试与回归结果，以及一条真实请求的“消息 → 模型工具选择 → 工具结果 → 工单记录”调用链。用户验收后再决定并入 `codex/product-agent-development`，不自动合并。

## 下一阶段接口

底座迁入验收通过后，Technical Builder 继续承担 S1 Pro 官方知识和 Agent 编排的技术实现；下一份实施计划针对产品型号与来源元数据、旧电商知识隔离、`search_support_knowledge(query, confirmed_model)` 契约及 10 条冒烟测试。随后接入追问、短期状态、排障反馈和确认后建单。用户负责逐阶段检查实现、运行链路并决定范围取舍；两阶段都不修改本计划中的原始基线提交。
