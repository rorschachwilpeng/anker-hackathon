# Anker 售后单张图片辅助咨询实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **项目特殊规则：只由现有 Technical Builder 任务执行代码，不新建或 fork Builder，也不另派子代理。**

**Goal:** 在已验收的 S1 Pro 文字售后切片上，加入单张图片的上传、可见线索提取与追问，并保持现有文字知识检索和工单门禁。

**Architecture:** 图片只进入本轮视觉理解，不进入 MySQL/Milvus 知识索引。视觉模型返回结构化观察，现有 `S1ProFlow` 用程序规则决定是否追问；产品经用户确认后沿用原文字 RAG。新增图文入口，保留 `/s1pro/chat` 的 JSON 契约与原电商路径。

**Tech Stack:** Python 3.12+、FastAPI、Pydantic、httpx、SQLAlchemy/MySQL、现有文字 RAG、原生 JavaScript、pytest。视觉服务选择 DeepSeek `deepseek-flash`，通过其 OpenAI-compatible chat-completions 接口调用；真实服务必须先通过单图探针。现有文字模型配置保持不变。

## Global Constraints

- 需求依据：[单张图片 PRD](../specs/2026-09-23-anker-single-image-after-sales-prd.md)；界面依据：[可交互上传原型](../../../04-方案设计/原型/2026-09-23-单图上传交互原型.html)；上层边界：[初赛增强设计](../specs/2026-09-23-anker-initial-round-enhancements-design.md)。本计划仅实施多模态第一阶段，不包含情绪识别、图向量、图片知识库或客服后台重做。
- 代码只在项目的正式开发仓库与实施分支中修改；先核实仓库路径、分支、工作树状态。`codex/customer-support-import` 是只读基线，不能在知识仓库或历史副本中冒充代码交付。
- 前置条件：在唯一 Technical Builder 的实施工作树确认已验收文字切片与本计划均已合入，复现 187 项测试、原底座 9/9、S1 Pro 10/10，再开始图片功能；保留其他工作树所有未提交内容。完成后再按分支规则将代码交回正式开发线。
- 图片限单张 JPEG/PNG，最大 5 MB；原图不入知识库、数据库、工单或 Git。会话只保存文字化观察及其“图片线索/未核实”标记。测试图应自有或有使用权。
- 产品对象为 eufy Robot Vacuum Omni S1 Pro（T2080）；同名其他品类、模糊图和图文冲突不能触发 T2080 专属排障。建单仍须用户明确同意且同会话幂等。
- `.env`、真实密钥、用户照片及包含 base64 图片的数据日志不提交；本地无鉴权工单页不得公开部署。

---

## 先看这里：人如何验收

```text
用户选择一张图片，可配文字 → 页面预览 → 图片服务只提取可见线索
→ 不确定则追问 → 用户确认 T2080 → 原文字知识库返回官方步骤与来源
→ 用户反馈；自助无效时仍需明确同意才建单
```

验收时先试一张清晰标签图、一张同名不同品类图和一张看不清的图：第一张应引出确认问题，后两张不得得到 T2080 专属步骤。再拔掉视觉服务，确认纯文字链路仍可运行。界面应能预览、移除图片，显示分析中与失败状态。它**不**需要按图片搜索知识库。

## 代码边界

以下代码路径均相对于 `09-workspace/customer-support/ecommerce-support/`；`06-评估验证/` 是仓库根目录下的文档路径。不改 `app/knowledge/`、Milvus collection、旧 `/chat` 或旧电商工具。

| 单元 | 文件 | 责任 |
| --- | --- | --- |
| 视觉配置与客户端 | `app/config.py`、`.env.example`、`app/s1pro/vision.py`、`tests/test_s1pro_vision.py` | 独立视觉服务配置；图片转为结构化可见线索；失败显式返回 |
| 图文输入 | `app/main.py`、`pyproject.toml`、`requirements.lock`、`tests/test_s1pro_image_route.py` | 新增 multipart 图文入口；服务端校验真实 MIME/文件大小；原 JSON 路由不变 |
| 任务状态与安全门禁 | `app/s1pro/state.py`、`app/s1pro/flow.py`、`tests/test_s1pro_flow.py` | 区分视觉候选与已确认产品；图文冲突先追问；图片失败不损坏文字路径 |
| 消费者界面 | `app/static/index.html`、`app/static/chat.js`、`app/static/chat.css` | S1 Pro 模式单图选择、预览、移除、发送和状态反馈 |
| 评估证据 | `tests/test_s1pro_image_route.py`、`tests/test_s1pro_flow.py`、`06-评估验证/` 下新的验收记录 | 八类图文场景、原文字回归和真实视觉探针结果 |

### Task 1：接入可验证的视觉服务

**Files:** 修改 `app/config.py`、`.env.example`；创建 `app/s1pro/vision.py`、`tests/test_s1pro_vision.py`。

**Interfaces:** `VisualObservation(candidate_model: Literal['T2080','other','unknown'], visible_text: list[str], visible_clues: list[str], uncertainties: list[str])`；`VisionClient.analyze(image_bytes: bytes, mime_type: str) -> VisualObservation`（异步）；`VisionError` 表示超时、协议或解析失败。视觉配置使用独立的 `VISION_BASE_URL=https://api.deepseek.com`、`VISION_MODEL=deepseek-flash`、`VISION_API_KEY`、`VISION_TIMEOUT_SECONDS`。本机 `VISION_API_KEY` 可以使用已有的 DeepSeek API Key，但不得把密钥写入提交；缺失配置不能影响文字服务启动。不得改动现有 `OPENAI_*` 文字咨询配置。

- [ ] 先写客户端失败测试：用 `httpx.MockTransport` 回传合法 `choices[0].message.content` JSON，断言能解析 `candidate_model='T2080'` 与可见文字；再回传错误结构、超时及 `candidate_model='other'`，断言错误被转换为 `VisionError`，不把错误猜成 `T2080`。测试中只使用短小的自造 JPEG 字节，不打印请求正文。

```python
import pytest

def test_observation_rejects_unexpected_candidate():
    from app.s1pro.vision import VisualObservation
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        VisualObservation(candidate_model="guessed-T2080", visible_text=[], visible_clues=[], uncertainties=[])
```

- [ ] 运行 `.venv/bin/python -m pytest tests/test_s1pro_vision.py -q`，确认新模块尚不存在时失败。
- [ ] 最小实现：配置字段保持可空；只有走图片路径时检查三项视觉配置。`VisionClient` 用注入的 `httpx.AsyncClient` 向 `{VISION_BASE_URL}/chat/completions` 发送一条包含简短观察指令与 `data:<mime>;base64,...` 的 user 消息，请求 JSON 对象。仅解析 `choices[0].message.content`，再用 Pydantic 验证枚举与列表；超时、HTTP 非 2xx、空响应或非法 JSON 统一抛 `VisionError`。指令要求只填图片可见内容，不能推断内部故障或把产品候选当确认事实。

```python
from typing import Literal

from pydantic import BaseModel

class VisualObservation(BaseModel):
    candidate_model: Literal["T2080", "other", "unknown"]
    visible_text: list[str]
    visible_clues: list[str]
    uncertainties: list[str]

class VisionError(Exception):
    pass
```

- [ ] 用一张拥有使用权的非私密测试图，从本地 `.env` 调用 `deepseek-flash` 验证图片输入和 JSON 返回；探针最多 3 次，记录模型名、是否返回有效结构、耗时、API 返回的 token 用量与可见线索，不记录图片或密钥。如果服务不支持图片输入或结构化返回，停在此检查点并报告 Product Lead，不以 mock 通过冒充多模态可用。
- [ ] 运行视觉单测及原文字测试；只提交该任务文件，并报告真实探针结果。`git add` 只列上述文件，提交信息 `feat: add bounded S1 Pro vision observation`。

### Task 2：新增不破坏旧路径的图文接口

**Files:** 修改 `app/main.py`、`pyproject.toml`、`requirements.lock`；创建 `tests/test_s1pro_image_route.py`。

**Interfaces:** `POST /s1pro/chat/image` 接收 `multipart/form-data` 的 `message: str`（可空）、`conversation_id: int | None`、必填 `image`；成功时返回与现有 `/s1pro/chat` 同结构的 JSON。内部调用 `S1ProFlow.handle(message, conversation_id, observation=..., image_failed=...)`。原 `/s1pro/chat` 的 JSON 请求不变。

- [ ] 先写路由测试：有效 PNG/JPEG 可传入 fake 视觉客户端与 fake flow；空文本＋图片合法；仅改文件扩展名但魔数不符返回 415；超过 5 MB 返回 413；视觉失败时仍把 `image_failed=True` 交给 flow；原 JSON 路由继续可用。`TestClient(create_app(service=object(), s1pro_flow=fake_flow, vision_client=fake_vision))` 注入假服务，不请求外网。

```python
def test_rejects_forged_png(client):
    response = client.post("/s1pro/chat/image", data={"message": "看下图片"},
                           files={"image": ("label.png", b"not-a-png", "image/png")})
    assert response.status_code == 415
```

- [ ] 运行 `.venv/bin/python -m pytest tests/test_s1pro_image_route.py tests/test_s1pro_routes.py -q`，确认新路由测试先失败。
- [ ] 在依赖中加入 `python-multipart` 并把安装后确认的版本锁定到 `requirements.lock`；不要顺手重写锁文件里现有的 editable 路径或其他版本。只读 `5*1024*1024+1` 字节校验上限，检查请求 MIME 与 JPEG/PNG 文件魔数一致，不依赖文件名。对非法格式/超限在调用视觉模型或写入会话前拒绝。视觉异常或配置缺失转为 `image_failed=True`：有文字则继续原文字处理，仅图片则请求用户改用文字描述；不把异常堆栈或 base64 回给浏览器。

```python
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
```

- [ ] 跑新旧路由测试与 `.venv/bin/python -m pytest -q`。提交仅包含本任务文件，信息 `feat: accept one image in S1 Pro chat`。

### Task 3：把图片线索安全接入现有任务流程

**Files:** 修改 `app/s1pro/state.py`、`app/s1pro/flow.py`、`tests/test_s1pro_flow.py`。现有 `conversations.task_state` JSON 可容纳新增可空键，不新增数据库表或清理历史会话。

**Interfaces:** `S1ProState` 新增 `pending_model_candidate: str | None = None`、`visual_observation: dict | None = None`，`awaiting` 增加 `symptom` 分支；`S1ProFlow.handle(message, conversation_id=None, *, observation: VisualObservation | None = None, image_failed: bool = False)`。`visual_observation` 只存 `visible_text`、`visible_clues`、`uncertainties` 和候选标记，绝不存图片字节或 data URL。

- [ ] 先写流程失败测试：图片候选 T2080＋“S1 Pro 不吸了”先追问；同名另一品类图＋同一句文字不检索；图文冲突时本轮不得因 `_explicit_product()` 直接确认 T2080；图片单独发送先问故障现象；图片失败＋文字仍走纯文字追问；用户回复“是的”只有在上轮确实提出 T2080 确认问题时才确认。每例断言 `search.calls == []`，直到获得明确产品确认。

```python
from app.s1pro.vision import VisualObservation

other = VisualObservation(candidate_model="other", visible_text=["S1 Pro"],
                          visible_clues=["非扫拖机器人外观"], uncertainties=[])
first = await flow.handle("S1 Pro 不吸了", observation=other)
assert first["action"] == "clarify_product"
assert search.calls == []
second = await flow.handle("是的", first["conversation_id"])
assert search.calls == []  # 未曾提出 T2080 确认问题，不能把“是的”当确认
```

- [ ] 运行 `.venv/bin/python -m pytest tests/test_s1pro_flow.py -q`，确认新增场景先失败。
- [ ] 在 `handle()` 中先保存用户真实文字（仅图片时用明确的“[用户上传图片]”会话标记，不伪作用户原话），再处理视觉候选。非 T2080 候选或图文冲突时清空待确认型号并追问；T2080 候选只设置 `pending_model_candidate='T2080'`，不设置 `confirmed_model`。原文字模糊案例在询问 T2080 时也设置同一待确认字段。只有用户明确完整说出产品，或回答上轮 T2080 确认问题，才设置 `confirmed_model='T2080'` 并检索。视觉可见线索与已确认事实在状态和回复中分开表述。
- [ ] 仅图片且无症状时先问故障现象；视觉失败时保留原文字和先前状态，回复说明图片未读出，继续原文字路径。若后续建单，将文字化视觉线索标为“图片提示、未核实”加入现有描述，不承诺原图可供人工查看；不改现有建单确认及幂等机制。
- [ ] 跑 `tests/test_s1pro_flow.py`、`tests/test_s1pro_state_repository.py` 和全量测试；提交本任务文件，信息 `feat: use visual clues without bypassing S1 Pro gates`。

### Task 4：增加 S1 Pro 前端上传控件与状态

**Files:** 修改 `app/static/index.html`、`app/static/chat.js`、`app/static/chat.css`；测试沿用 Task 2 的路由测试，并做浏览器手工验收。不重设计侧边栏或旧电商模式。

**Interfaces:** 仅 `mode === 's1pro'` 在输入框左下角展示「＋」按钮，向上展开“添加 → 上传图片”菜单并打开原生文件选择器；整个消息输入框也接受单张图片拖放。选中后在文字区上方显示预览、文件名和移除入口。此处「文件夹」只是菜单图标，不代表支持上传文件夹。有图片时 `submit()` 发送 `FormData(message, conversation_id, image)` 到 `/s1pro/chat/image`，无图片时继续发送原 JSON 到 `/s1pro/chat`。选择图片可使空文字消息可发送；其他模式保持原输入规则。

- [ ] 先在浏览器记录失败基线：当前 S1 Pro 模式没有图片控件；仅输入文字可发送。保留一张截图或简短录屏，不改用户数据。
- [ ] HTML 按原型加入可访问的「＋」菜单、隐藏但可由菜单触发的 `input[type=file]`（`accept="image/png,image/jpeg"`）、拖入提示、缩略图、文件名、移除按钮和输入框内错误提示；菜单目前只有「上传图片」。CSS 沿用现有 composer 的字号、边框与焦点风格，不复制原型顶部仅用于评审的状态按钮。切换模式或新建会话时清空未提交的文件和预览 URL。
- [ ] JS 让文件选择和拖到整个 composer 进入同一校验：JPEG/PNG、5 MB、单图；已有图片时不静默替换第二张。用 `URL.createObjectURL()` 预览并在移除/重置时 `URL.revokeObjectURL()`；`syncInput()` 允许 S1 Pro 模式“只有图”。`busy()` 禁止重复提交与重复选图。用户气泡显示临时缩略图与“已附 1 张图片”，而不是 base64；回复区显示“正在识别图片”、真实追问、视觉失败或原有答复，不在响应前显示成功。

```javascript
const hasImage = mode === 's1pro' && Boolean(selectedImage);
$('send').disabled = Boolean(active) || (!$('message').value.trim() && !hasImage);
const form = new FormData();
form.append('message', message);
if (conversationId !== null) form.append('conversation_id', String(conversationId));
form.append('image', selectedImage);
```

- [ ] 在浏览器手工验证：点击「＋」打开/关闭菜单、菜单项调起本地选择器、拖到整个输入框、文字＋图、仅图、预览移除、第二张不暗中替换、格式和超限错误、上传中禁止重复点击、视觉失败改用文字继续、切换模式不残留图片。检查桌面与窄屏宽度；原文字入口和工单页仍可用。运行全量测试并提交上述三个前端文件，信息 `feat: add single-image S1 Pro composer`。

### Task 5：真实图文回归与交付证据

**Files:** 新增 `06-评估验证/2026-09-26-Anker单图售后验收.md`；根据发现的真实缺陷仅修改对应任务文件与测试。不要把私密图片、完整请求或密钥放进报告。

- [ ] 按 PRD 的八类场景逐条记录测试图片的来源/使用权、输入、预期下一步、实际下一步、型号是否在用户确认前进入检索、是否有官方来源。清晰 T2080 图、同名异品类图、模糊图、可见报错图、图文冲突、仅图、仅文字、视觉失败缺一不可。无使用权图片不得入 Git 或录屏。
- [ ] 在已启动的本地服务运行 `.venv/bin/python -m pytest -q`、`.venv/bin/python scripts/evaluate_s1pro.py --base-url http://127.0.0.1:8001`、`.venv/bin/python -m scripts.evaluate --base-url http://127.0.0.1:8001`；基线分别对照 186 项、10/10、9/9，新增测试会使测试总数增加，不能把总数写死为 186。
- [ ] 完成一条真实图文主链路：上传已授权图片 → 提取线索 → 用户确认 T2080 → 官方文字知识返回一步与来源 → 用户反馈。再断开视觉服务验证纯文字可继续。建单前后各核对一次用户确认门禁与同会话幂等。
- [ ] 交付测试命令、结果、失败和降级证据、UI 截图或录屏及“原图不留存”说明。只有真实视觉调用与八类安全判断均达标，才能在方案中称“已实现单张图片理解”；否则明确写为计划或部分实现。提交验收记录与必要测试修复，信息 `test: verify S1 Pro single-image support`。

## 交接规则

Product Lead 先审阅此计划。正式交接前，先提交并推送本计划，再向**唯一** Technical Builder 任务 `01a0b4e1-f4df-7133-8075-03f34c242983` 发送“提交哈希＋PRD 与 Plan 路径＋需要执行的动作＋验收标准”。Builder 按任务独立提交并报告结果；Product Lead 验收后再决定是否开始下一轮情绪与紧迫性设计。
