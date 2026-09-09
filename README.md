# 小红书影视助手

<p align="center">
  <img src="icons/xhsmovieassistant.png" width="128" height="128" alt="小红书影视助手图标">
</p>

[![MoviePilot](https://img.shields.io/badge/MoviePilot-%3E%3D%202.15.6-2f6fed)](https://github.com/jxxghp/MoviePilot)
[![Version](https://img.shields.io/badge/version-0.2.6-2d8a56)](https://github.com/s450586793/MoviePilot-XhsMovieAssistant/releases)
[![License](https://img.shields.io/badge/license-MIT-555555)](LICENSE)

小红书影视助手是一个非官方 MoviePilot V2 社区插件。你在小红书或
RedNote 笔记评论中 `@` 专用助手账号后，插件会读取授权账号发出的请求，复用
MoviePilot 已配置的 AI 识别电影或电视剧，再通过 MoviePilot 原生链路完成匹配、
查重和订阅。

插件只负责“小红书发现影视 → 交给 MoviePilot”这一段。下载、115、刮削和
Emby 入库继续沿用你已有的 MoviePilot 配置。

> `v0.2.6` 是公开测试版本。请先长期使用 dry-run 校准识别结果，再打开真实
> 订阅。真实订阅和小红书公开回复默认均为关闭状态。

## 功能

- 低频读取专用小号收到的“评论和 @”通知，不抓推荐流。
- 只允许配置的稳定用户 ID 触发，昵称不会作为授权依据。
- 获取触发评论、笔记标题、正文、作者和少量相关评论。
- 复用 MoviePilot 当前 AI 配置，不单独保存模型地址、Key 或 Token。
- 一篇笔记明确推荐多部作品时，在企业微信列出 MoviePilot 候选供编号选择，不自动
  订阅第一部。
- 待确认列表过滤仅媒体类型相同的低相关搜索结果，避免无关候选淹没真实片名。
- 按标题、原名、类型、年份和季数进行确定性匹配；结果歧义时要求人工确认。
- 查询媒体库和已有订阅，避免重复创建。
- 使用 SQLite 保存请求状态，支持崩溃恢复与幂等处理。
- 通过 MoviePilot 已配置的消息渠道发送处理结果。
- RedNote 直接在对应“评论和 @”通知卡片内回复，并在发送前复核账号与正文。
- 无法确定时，可在企业微信发送通知内的明确命令；插件页人工确认继续作为备用入口。
- 可选使用固定模板回复原始小红书评论；不会让 AI 自由生成公开回复。
- 提供 Vue 管理页，可导入 Cookie/Storage State，并查看状态、人工确认、重处理、补发回复、忽略和运行诊断。

## 系统要求

- `MoviePilot >= 2.15.6`（V2）。
- NAS 或主机能够访问所选的小红书/RedNote 站点和 GitHub 插件仓库。
- MoviePilot 已配置可用的 AI 服务；插件直接复用该配置。
- 如需在企业微信直接确认，MoviePilot 的企业微信通知渠道需要配置 `WECHAT_ADMINS`。
- MoviePilot 容器需要具备运行 Playwright Chromium 的系统依赖。

这是纯 MoviePilot 插件，插件本身**无需额外 Docker 容器**，也不需要配置 MP 地址
或 MP Token。插件只使用自身安装的 Chromium 和私有 Storage State，不连接外部
浏览器服务。`src/xhs_probe/` 只保留离线协议解析和数据导入代码，不是插件安装入口。

## 登录方案

插件支持粘贴完整 Cookie 请求头，或导入 Playwright `storageState.json`。凭据会先按
当前选择的小红书/RedNote 站点过滤，再以 `0600` 权限保存到 MoviePilot 插件私有数据
目录。插件设置、状态接口和日志均不会回显 Cookie 值。

### 从 Chrome / Edge 获取 Cookie

1. 先在插件设置中确认选择的是“小红书”还是“RedNote”。
2. 在电脑浏览器打开对应网站，登录“小红书影视助手”小号：国内站使用
   `https://www.xiaohongshu.com/explore`，RedNote 使用 `https://www.rednote.com/explore`。
3. 按 `F12` 打开开发者工具，切换到 `Network`（网络），然后刷新页面。
4. 点开任意发往当前站点的请求，在 `Headers`（标头）→ `Request Headers`（请求标头）
   中找到 `Cookie`。
5. 复制 `Cookie:` 后面的完整值，粘贴到插件管理页并点击“保存并验证 Cookie”。只粘贴值
   即可；如果连同 `Cookie:` 一起复制，插件也能识别。
6. 导入后插件会立即验证；页面应显示登录凭据 `PRESENT`、登录状态 `LOGGED_IN`。

插件登录区的 Cookie 状态标签会显示“未保存”“待验证”“有效”或“已失效”。只有显示
“Cookie：有效”时才表示站点已完成真实登录验证。

不要在 Console（控制台）运行 `document.cookie` 获取凭据，它通常拿不到 HttpOnly
Cookie，会造成登录验证失败。Cookie 等同于账号登录凭据，不要发给他人，也不要上传到
GitHub、Issue、聊天、截图或日志。

### 获取 Storage State

Storage State 可以同时携带 Cookie 和站点 Local Storage。在电脑上安装 Node.js 后，
可使用 Playwright 打开本地浏览器并在其中人工登录：

```bash
npx playwright codegen --save-storage=storageState.json https://www.xiaohongshu.com
```

RedNote 账号将地址改为 `https://www.rednote.com`。登录完成后关闭 Playwright，再在插件
管理页选择该 JSON 文件。

Cookie 和 `storageState.json` 都等同于账号登录凭据，只应在自己的设备与 MoviePilot
之间传递。不要上传到 GitHub、Issue、聊天、截图或日志。导入后插件会立即访问所选站点
验证登录状态，验证成功才显示 `LOGGED_IN`；验证失败不会自动循环重试。

## 安装

1. 打开 MoviePilot 的“插件”页面，进入“插件市场设置”（自定义插件市场）。
2. 添加下面的插件仓库地址并保存：

   ```text
   https://github.com/s450586793/MoviePilot-XhsMovieAssistant/
   ```

3. 刷新插件市场，搜索“小红书影视助手”，点击安装。
4. 打开插件详情页并点击“安装 Chromium”。安装是后台任务，完成前不要导入登录凭据
   或启用轮询；通常不需要重启 DSM。

MoviePilot V2 读取该仓库 `main` 分支的 `package.v2.json`，插件更新发布后只需
刷新插件市场并执行更新。若 NAS 无法访问 GitHub，请先配置 MoviePilot 的 GitHub
网络代理，不要把 GitHub 凭据写入插件设置。

## 首次授权

1. 在插件设置中保持“启用轮询”“允许真实订阅”和“小红书公开回复”全部关闭。
2. 填写允许发起请求的稳定“授权用户 ID”。可以从主账号网页版个人主页 URL，或
   一次已确认的 `@` 通知请求中核对该 ID；不要使用昵称、展示名或可变短 ID。
   多个 ID 可用英文逗号或换行分隔。
3. 选择助手小号实际使用的站点并保存设置，然后在管理页粘贴完整 Cookie，或选择
   Playwright 生成的 `storageState.json`。导入会自动验证，站点选择错误时不会通过。
4. 导入完成后刷新插件页：Chromium 应显示 `AVAILABLE`，登录凭据应显示 `PRESENT`，
   登录状态应显示 `LOGGED_IN`。
5. 先点击“测试 AI”“测试 MoviePilot”和“测试通知”，分别确认现有 MP 能力正常。需要微信确认时，还要确保接收人的企业微信用户 ID 已列入该渠道的 `WECHAT_ADMINS`。

## 先验证，再订阅

1. 保持“允许真实订阅”关闭，打开“启用轮询”。这是 dry-run：插件会识别、匹配和去重，但不会创建真实订阅。
2. 用已授权账号发送一条可明确识别的影视请求，观察插件页面的请求列表和状态。
3. 确认 `DRY_RUN_MATCHED`、匹配标题和季信息都正确后，才在设置中打开“允许真实订阅”。后续成功请求会显示 `SUBSCRIBED`。

不要将“MoviePilot 通知”当作订阅开关；它只控制 MoviePilot 内的通知。小红书“公开回复”为可选功能，风险更高：它会把处理结果公开写回站点。总开关和四个分类开关默认全部关闭；只有“小红书公开回复”与对应的“订阅成功”“已存在”“需人工确认”或“处理失败”分类同时打开，才会发送该类结果。建议在长期 dry-run 稳定后、且已审核回复模板时按需逐类打开。

## 工作流程

```text
授权主账号 @ 助手小号
        ↓
低频读取“评论和 @”
        ↓
校验稳定用户 ID
        ↓
读取并清洗笔记上下文
        ↓
MoviePilot AI 结构化识别
        ↓
MoviePilot 搜索、唯一匹配与查重
        ↓
dry-run 或创建订阅
        ↓
MoviePilot 消息通知 / 可选固定模板评论回复
```

无法确定具体作品、同一笔记推荐多部作品、同名年份歧义、类型冲突或季数不明确时，
插件不会选择搜索结果中的第一项，而是进入 `NEED_CONFIRMATION`。多作品笔记只会列出
明确推荐的作品，不会加入正文中明确排除或仅顺带提及的片名。企业微信通知会为每个
候选给出完整的 `/xhs_pick 请求号 编号` 命令；需要重新说明片名时，发送
`/xhs_confirm 请求号 片名 年份 电影/剧集`。插件管理页的人工确认仍保留为备用入口。

插件不会创建企业微信普通文本输入会话，也不会接管你与 MoviePilot AI 的聊天。
单独发送 `1`、片名或其他普通文本会继续由 MoviePilot 原有消息链处理，不会触发本插件。
只有企业微信配置中的 `WECHAT_ADMINS` 可以执行上述命令，其他用户的命令会被忽略。

## 状态说明

浏览器状态：`READY` 表示可继续；`PAUSED` 表示风险控制、会话或浏览器问题已暂停轮询；`ERROR` 表示最近一次浏览器操作失败，先检查页面显示的错误码和对应恢复项，再恢复轮询。登录凭据 `PRESENT` 表示插件私有 Storage State 已存在，`MISSING` 表示需要导入；登录状态 `LOGGED_IN` 表示验证成功，`LOGGED_OUT` 表示凭据无效或已清除。活动状态 `IDLE` 表示空闲，`POLL` 表示正在轮询，`START_FAILED` 或 `FAILED` 表示需要查看下方恢复步骤。

请求状态：`NEW` 为待处理，`FETCHED` 为已获取，`RESOLVING` 为 AI 正在识别；`NEED_CONFIRMATION` 表示信息不足或有歧义，需要人工确认；`NOT_MEDIA` 表示不是影视请求；`MATCHED` 表示已找到候选；`DRY_RUN_MATCHED` 表示 dry-run 匹配成功；`ALREADY_IN_LIBRARY` 和 `ALREADY_SUBSCRIBED` 表示无需再次订阅；`SUBSCRIBED` 表示已创建订阅；`FAILED` 表示本次失败；`IGNORED` 表示人工忽略。

公开回复状态只在明确启用该可选高风险功能后出现：`PENDING` 表示等待发送；`SENT` 表示公开回复已成功发出；`FAILED` 表示发送失败。RedNote 未返回回复 ID、但页面明确关闭回复编辑器时，也会记录为 `SENT`，不会伪造平台 ID。插件不会自动重试公开回复；`PENDING` 的支持结果可在管理页手动补发。补发前会重新读取通知并严格核对 mention、发起人、评论和笔记 ID，瞬时访问凭据不会写入 SQLite。`SENT` 或 `FAILED` 不会再次补发，避免重复留言。重新处理会开启一次新的处理尝试并重置回复状态，但不会重复创建已存在的订阅。

日常优先在企业微信发送待确认通知中列出的完整命令。也可通过请求列表的“人工确认”“重新处理”或“忽略”操作处理 `NEED_CONFIRMATION`、`FAILED` 和未处理条目。人工确认页会载入缓存状态和最近的持久化请求；可先编辑影视标题、原始标题、电影/电视剧类型、年份和季数，再提交确认。标题为空或类型不属于电影/电视剧时，页面不会提交；提交后会刷新列表。该操作仍受服务端的授权、严格校验、确定性匹配、去重和 dry-run/真实订阅开关约束。恢复后再轮询。

## 恢复

| 现象 | 处理 |
| --- | --- |
| 出现 `300012`、验证码或站点风险控制 | 停止高频重试，在电脑浏览器中人工完成验证并重新导出 Storage State；导入成功后插件会恢复登录暂停。 |
| 导入后出现“安全限制” | 不要反复导入或立即轮询；确认 Cookie/Storage State 来自所选站点和助手小号，并等待风控解除。 |
| 登录过期、`LOGGED_OUT` 或 `LOGIN_REQUIRED` | 在电脑浏览器中重新登录，导出并导入新的 Cookie/Storage State。 |
| `INVALID_CREDENTIALS` | 确认粘贴的是完整 Cookie 请求头，或文件是合法的 Playwright `storageState.json`，并与插件选择的站点一致。 |
| Chromium 安装失败或缺少系统库 | 按 MoviePilot 部署镜像/宿主机的 Chromium 依赖说明补齐库后，重新点击“安装 Chromium”。不要把浏览器依赖写入插件配置。 |
| `TEMPORARY_FAILURE` | 通知接口或页面结构连续失败 3 次后监听会暂停；检查 MoviePilot 日志及站点可用性，稍后人工恢复轮询，不要高频重试。 |
| AI 测试失败或 AI 未启用 | 先在 MoviePilot 系统设置中配置并启用 AI，再使用插件的“测试 AI”确认；插件不保存模型凭据。 |
| `NEED_CONFIRMATION` 或歧义结果 | 发送企业微信通知中的 `/xhs_pick` 或 `/xhs_confirm` 完整命令；插件页“人工确认”作为备用。不要仅凭相近标题开启真实订阅。 |
| 浏览器显示 `ERROR` | 查看刚执行操作的错误码；修复 Chromium、登录或站点验证问题后点击“恢复轮询”。 |
| 公开回复为 `PENDING` | 修复浏览器或登录问题后，在管理页点击“补发小红书回复”。 |
| 公开回复为 `FAILED` | 为防止重复公开留言，该次回复已终止，需人工处理；必要时重新处理整个请求。 |
| `PAUSED`、`FAILED` 或 `START_FAILED` | 先处理页面显示的浏览器/登录原因，再点击“恢复轮询”；恢复前保持真实订阅关闭。 |

## 安全与隐私

- 小红书账号密码不会写入代码或插件配置；导入的 Cookie/Storage State 只保存在
  MoviePilot 插件私有数据目录中，文件权限为 `0600`。
- Storage State、SQLite 数据库、日志、`.env` 和各类会话凭据均被排除在
  Git 仓库之外。
- 瞬时笔记访问凭据只用于当前浏览器会话，不进入数据库、AI 输入或日志。
- 完整稳定用户 ID 不会显示在管理页或日志中。
- 遇到登录失效、验证码、`300012`、限流或风险控制时，插件会暂停，不会无限重试。
- 不会自动点赞、关注、抓取推荐流或批量发送消息。
- 小红书并未提供面向此场景的稳定机器人接口，页面结构和风控策略可能变化。请使用
  专用小号、低频轮询，并遵守账号所在地区的平台规则。

## 已知限制

- V1 不做 OCR、视频字幕、关键帧识别或推荐流抓取。
- 只有模糊文案、无片名画面或多个作品混剪的笔记通常需要人工确认。
- 国内站与 RedNote 的登录跳转、可见 DOM 和通知接口可能因账号或出口网络不同。
- 离线测试和 MoviePilot `v2.15.6` import/route smoke 已通过；不同 NAS 镜像中的
  Chromium 系统库，以及真实站点链路仍需每位用户先完成 dry-run 验证。

## 开发验证

```bash
python -m pytest -o addopts='' -q
python -m pytest -o addopts='' -q \
  --cov=xhs_probe --cov=xhsmovieassistant --cov-fail-under=80
npm ci --prefix plugins.v2/xhsmovieassistant
npm test --prefix plugins.v2/xhsmovieassistant
npm run build --prefix plugins.v2/xhsmovieassistant
python -m pytest -o addopts='' -q tests/plugin/test_package.py
python -m compileall -q src plugins.v2 tests
git diff --check
```

当前 `v0.2.6` 发布验证：Python `486 passed`，总覆盖率 `87.73%`；Vue
`26 passed`，生产构建通过。MoviePilot `v2.15.6` 隔离 import/route smoke 不调用
真实 MoviePilot Chain，也不代表真实账号或部署环境已经验收。

## 实现证据

仓库中保留了 Phase 1–3 的离线验证证据：`src/xhs_probe/` 包含早期捕获与导入工具，`tests/test_capture.py`、`tests/test_ingest.py` 和 `tests/test_phase3_cli.py` 覆盖其契约。它们用于追溯和开发验证；日常安装、凭据导入、授权、状态查看与恢复都应在 MoviePilot 插件界面完成。

## 许可与反馈

本项目使用 [MIT License](LICENSE)。问题反馈请提交到
[GitHub Issues](https://github.com/s450586793/MoviePilot-XhsMovieAssistant/issues)，
并附上 MoviePilot 版本、站点类型、脱敏后的错误码和复现步骤；不要上传浏览器
会话凭据、完整用户 ID、Token 或未经脱敏的站点响应。
