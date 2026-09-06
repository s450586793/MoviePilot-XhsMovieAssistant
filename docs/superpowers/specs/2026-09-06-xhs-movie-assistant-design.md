# XhsMovieAssistant MoviePilot 插件设计

## 1. 目标与范围

`XhsMovieAssistant` 是面向 MoviePilot V2 的纯插件。用户安装插件、在插件页扫码登录专用小红书或 RedNote 小号、配置允许触发的稳定用户 ID 后，即可在任意笔记评论中通过 `@助手 想看` 发起影视订阅。

V1 负责以下闭环：

1. 低频读取专用账号收到的“评论和 @”通知。
2. 只接受授权用户 ID 发起的请求。
3. 获取触发评论和原笔记的标题、正文、作者、类型及少量相关评论。
4. 复用 MoviePilot 当前 LLM 配置识别影视作品。
5. 使用 MoviePilot 原生媒体、媒体库和订阅链完成匹配、去重与订阅。
6. 使用 MoviePilot 已配置的消息渠道发送可靠通知。
7. 可选地在原始小红书评论下回复固定模板结果。
8. 在插件详情页展示登录状态、最近请求、处理结果和失败原因。

V1 不做推荐流抓取、自动点赞、自动关注、OCR、视频字幕或关键帧识别，也不修改 MoviePilot 数据库。

## 2. 已验证基础与兼容范围

- 目标版本为 MoviePilot `v2.15.6`，插件声明最低系统版本 `>=2.15.6`。
- 官方镜像已安装 Playwright Chromium 所需的系统依赖；插件仍需通过 `requirements.txt` 安装 Python Playwright 包，并在插件页显式安装 Chromium 内核。
- Chromium 已在同版本 MoviePilot 环境完成启动和持久化 Profile 探针验证。
- RedNote 通知页可以打开并生成登录二维码；浏览器需要继承 MoviePilot 的 `PROXY_SERVER` 配置。
- 已捕获 `/api/sns/web/v1/you/mentions`，确认通知包含 `mention_id`、发起用户 ID、评论 ID、评论内容、笔记 ID 和 `xsec_token`。
- 国内小红书站点在当前出口 IP 曾返回 `error_code=300012`，因此插件同时支持 `xiaohongshu.com` 和 `rednote.com`，且遇到风控时暂停而非持续重试。

## 3. 架构

插件目录名为 `xhsmovieassistant`，插件类名为 `XhsMovieAssistant`。核心模块保持单一职责：

- `plugin.py`：MoviePilot 插件生命周期、配置、API、定时服务、页面和通知。
- `browser.py`：Playwright persistent context、Chromium 检测/安装、二维码与登录状态。
- `xhs.py`：通知读取、通知契约解析、笔记抓取和可选评论回复。
- `models.py`：`MediaRequest`、LLM 输出和处理结果数据模型。
- `repository.py`：SQLite schema、迁移、状态流转和幂等写入。
- `resolver.py`：复用 MoviePilot LLM 配置，构造受控 prompt 并解析结构化 JSON。
- `moviepilot.py`：调用 `MediaChain`、`MediaServerChain` 和 `SubscribeChain` 完成匹配与订阅。
- `service.py`：一次轮询的应用编排，不包含页面选择器或 MoviePilot API 细节。
- `templates.py`：通知和小红书公开回复模板，只做确定性变量替换。

依赖方向为：

```text
plugin -> service -> xhs/browser
                  -> repository
                  -> resolver
                  -> moviepilot
                  -> templates
```

XHS 监听与影视识别通过 `MediaRequest` 隔离，后续可以加入网页分享、企业微信或快捷指令入口，而不修改 resolver 和 MoviePilot 适配层。

## 4. 浏览器与登录

插件使用 Playwright `launch_persistent_context()`，Profile 保存到 MoviePilot 插件数据目录：

```text
/config/plugins/XhsMovieAssistant/browser/
```

Chromium 缓存保存到：

```text
/config/plugins/XhsMovieAssistant/ms-playwright/
```

首次使用流程：

1. 安装插件依赖。
2. 在插件详情页执行“安装 Chromium”。安装任务只运行一次，不阻塞页面请求。
3. 选择“小红书”或“RedNote”站点并生成二维码。
4. 用户扫码，插件轮询页面登录状态；成功后复用 Profile。
5. 用户填写一个或多个授权用户 ID，启用监听。

登录判断优先读取页面 `window.__INITIAL_STATE__.user`，再降级检查登录 DOM。二维码优先读取登录弹窗图片的 `src`，通过已鉴权插件 API 提供给详情页。

如果出现以下任一情况，浏览器状态进入 `PAUSED`：

- Cookie 失效或页面重新出现登录框。
- 验证码、人机验证或 IP 风险提示。
- HTTP 403、429 或已知风控错误码。
- 连续 3 次非业务性浏览器错误。

进入 `PAUSED` 后停止自动抓取和公开回复，仅发送一次 MoviePilot 通知。恢复必须由用户在插件页重新扫码或手动点击恢复，插件不自动循环登录。

## 5. 通知读取与笔记内容

轮询周期允许配置为 1～10 分钟，默认 2 分钟。每次只读取最新一页，最多 20 条通知，不扫描推荐流。

只处理 API 类型 `mention/comment`。通知首先解析为内部候选，再执行严格授权：

```text
sender_user_id in authorized_user_ids
```

昵称只用于后台展示，不参与授权。DOM 降级结果如果没有稳定用户 ID，一律不进入订阅流程。

获取笔记时使用通知返回的真实 `note_id` 和 `xsec_token` 打开详情页，优先从 `window.__INITIAL_STATE__.note.noteDetailMap` 读取：

- 标题与正文。
- 笔记类型。
- 作者 ID 和昵称。
- 笔记发布时间。
- 当前可见的相关评论。

评论只保留触发评论，以及最多 10 条包含明确影视名称线索的可见评论。V1 不滚动加载全部评论。`xsec_token` 只在当前浏览器会话中用于打开笔记，不写入数据库、不进入 LLM prompt、不记录日志。

## 6. MediaRequest 与 AI 输入

授权并去敏后的请求使用统一结构：

```json
{
  "request_id": "xhs_mention_123456",
  "source": "xiaohongshu",
  "intent": "subscribe",
  "trigger_comment": "想看",
  "note": {
    "id": "note_id",
    "url": "https://www.xiaohongshu.com/explore/note_id",
    "type": "video",
    "title": "笔记标题",
    "content": "笔记正文",
    "author": "笔记作者昵称",
    "relevant_comments": ["评论中的影视名称线索"]
  }
}
```

传入 LLM 前限制各文本字段长度并移除不可见控制字符。Prompt 明确将小红书内容标记为不可信数据，要求忽略其中的命令、角色设定和工具调用要求。

V1 使用 MoviePilot 的 `LLMHelper.get_llm(streaming=False)`，直接复用 MP 配置的 Provider、Base URL、API Key 和模型。不会调用 `AgentManager.run_background_prompt()`：在 `v2.15.6` 中该入口只能关闭消息工具，不能为单次任务限定全部工具白名单，直接使用通用 Agent 会暴露与本任务无关的系统写工具。

LLM 只负责输出识别建议：

```json
{
  "status": "resolved",
  "title": "星际穿越",
  "original_title": "Interstellar",
  "media_type": "movie",
  "year": 2014,
  "season": null,
  "confidence": 0.98,
  "reason": "标题、正文与评论线索一致"
}
```

允许的 `status` 为 `resolved`、`need_confirmation`、`not_media`。输出使用 Pydantic 严格校验；兼容 Markdown JSON 围栏，但不从任意自然语言中猜测字段。模型异常只尝试 1 次额外调用，随后进入失败状态。

## 7. MoviePilot 匹配与订阅

只有同时满足以下条件时才允许自动订阅：

- LLM 状态为 `resolved`。
- `confidence` 大于等于配置阈值，默认 `0.85`。
- 标题非空，类型为 `movie` 或 `tv`。
- MoviePilot 搜索后存在唯一高质量匹配。
- 搜索结果的类型一致；LLM 给出年份时，年份必须一致。
- 电视剧明确提出季数时，匹配结果必须能应用该季数。

候选匹配使用 MoviePilot `MediaChain` 搜索结果，按中文标题、英文/原名、年份和类型打分。若最高分不够、前两名差值过小或同名不同年份无法排除，则状态为 `NEED_CONFIRMATION`，不得选择第一条结果。

唯一匹配后按以下顺序执行：

1. `MediaServerChain.media_exists()` 检查媒体库。
2. `SubscribeChain.exists()` 检查现有订阅。
3. 若均不存在，调用 `SubscribeChain.add()`，传入稳定媒体 ID、类型、年份和季数。
4. 保存订阅 ID 和最终状态。

调用 MP 原生 Chain 时不传自定义质量、站点、下载器或目录参数，继续使用用户现有 MoviePilot 默认订阅配置。

V1 的真实订阅有总开关 `enable_subscription`，默认关闭。关闭时完整执行 AI、搜索、匹配和去重，但最终记录为 `DRY_RUN_MATCHED`，方便上线前校准。

## 8. 状态、SQLite 与幂等

数据库路径为：

```text
/config/plugins/XhsMovieAssistant/app.db
```

请求状态包括：

```text
NEW
FETCHED
RESOLVING
NEED_CONFIRMATION
NOT_MEDIA
MATCHED
DRY_RUN_MATCHED
ALREADY_IN_LIBRARY
ALREADY_SUBSCRIBED
SUBSCRIBED
FAILED
IGNORED
```

`xhs_requests` 保存用户最初要求的字段，并增加 `comment_id`、`original_title`、`tmdb_id`、`reply_status`、`reply_id`、`replied_at`、`attempt_count` 和 `updated_at`。

幂等约束：

- `mention_id` 唯一。
- `note_id + sender_user_id + 规范化触发评论` 的 SHA-256 请求键唯一。
- 订阅前再次以稳定媒体 ID 和季数查询 MP，防止并发重复。
- 公开回复前检查 `reply_status`；成功过的请求永不再次回复。

SQLite 使用短事务和 WAL 模式。处理中断后，启动时只把超时的 `FETCHED`、`RESOLVING`、`MATCHED` 记录恢复为可重试状态；已订阅和已回复状态不可回滚。

## 9. 通知与小红书回复

MoviePilot 通知是可靠主通道，使用插件 `post_message()`，由用户现有企业微信或其他消息模块发送。以下结果都要通知：

- 订阅成功。
- 已在媒体库或已订阅。
- 无法确定作品。
- 登录失效或风控暂停。
- AI、浏览器或订阅异常。

小红书公开回复默认关闭。启用后，只回复授权用户的原始触发评论，文本来自固定模板，不允许 LLM 自由生成。模板变量仅包括：

```text
{title} {original_title} {year} {media_type} {season} {season_text} {result}
```

可单独控制以下回复类型：成功、已存在、无法确定、订阅失败。系统异常默认不公开回复，登录失效时也无法依赖小红书回复。单次回复失败只记录并通知，不连续重试。

## 10. 插件页面与 API

配置页提供：

- 启用插件、启用真实订阅、启用 MP 通知、启用小红书回复。
- 站点选择：Xiaohongshu / RedNote。
- 授权用户 ID，多行输入。
- 轮询间隔和 AI 置信度阈值。
- 各类公开回复开关与模板。

详情页提供：

- 服务、Chromium、登录和暂停状态。
- 登录二维码。
- 最近请求表格及状态、识别结果、MP 结果和错误摘要。
- 安装 Chromium、刷新二维码、退出登录、恢复监听。
- 单次轮询、重新处理、忽略。
- 测试 AI、测试 MoviePilot、测试通知。
- 手动指定作品：标题、年份、类型、季数；仍需经过 MP 唯一匹配和去重。

写操作 API 必须使用 MoviePilot API Key 或 Bearer 鉴权。二维码图片同样不允许匿名访问。页面渲染只读取缓存状态，不在 HTTP 请求中启动浏览器或等待网络。

## 11. 并发、停止与异常处理

- 插件只有一个后台 worker，同一时刻只进行一次浏览器导航。
- 定时任务使用非阻塞锁；上一次轮询未结束时跳过本轮。
- `init_plugin()` 先停止旧 worker，再应用新配置，防止重复监听。
- `stop_service()` 设置停止事件、等待 worker 有限时间退出并关闭 browser context。
- 日志不记录 Cookie、token、完整授权用户 ID、原始通知 JSON 或 LLM API Key。
- 对外错误只返回错误类型和用户可执行的恢复动作；详细堆栈只写 MP 插件日志。

## 12. 发布结构

仓库保留现有探针作为已验证证据，同时新增可发布插件：

```text
xhs-mp-bridge/
├── plugins.v2/
│   └── xhsmovieassistant/
│       ├── __init__.py
│       ├── browser.py
│       ├── models.py
│       ├── moviepilot.py
│       ├── repository.py
│       ├── resolver.py
│       ├── service.py
│       ├── templates.py
│       ├── xhs.py
│       └── requirements.txt
├── icons/
├── package.v2.json
├── tests/
└── README.md
```

朋友使用时把仓库地址加入 MoviePilot 插件市场，即可安装、扫码和配置；仓库不包含 `.env`、Cookie、Profile、API Key、MP Token 或 SQLite 数据。

## 13. 测试与验收

离线测试覆盖：

- 通知 API 契约的正常、缺字段、未知类型和未授权用户。
- `MediaRequest` 去敏、长度限制和 prompt 注入样本。
- LLM JSON 正常、围栏、非法类型、低置信度、超时和异常。
- 候选匹配的唯一结果、同名不同年份、类型冲突、季数和并列候选。
- SQLite 迁移、状态转换、崩溃恢复、mention 去重、请求键去重和回复去重。
- MP Chain、Playwright、通知和公开回复全部使用 mock。
- 所有插件 API 的鉴权和错误信息脱敏。

集成测试分两步：

1. `enable_subscription=false`，用真实小红书 @ 验证通知、笔记、AI 和 MP 搜索结果。
2. 人工确认匹配准确后打开真实订阅，用一部不存在且未订阅的作品验证完整链路。

验收标准：授权账号的一条 @ 最多生成一个请求、一个 MP 订阅和一个公开回复；非授权账号永远不能触发 LLM 或 MP；无法唯一确认的作品永远不会自动订阅；登录失效或风控发生后服务停止读取并只通知一次。
