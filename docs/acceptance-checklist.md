# XhsMovieAssistant 受控真实环境验收清单

> **本清单是未来受控窗口的执行记录，不是执行授权。当前轮次状态：全部 `PENDING / NOT RUN`。**
>
> 不要在本轮或未经用户再次明确授权的窗口访问真实 XHS/RedNote、MoviePilot 或企业微信；不要启动 Chromium、生成或扫描二维码、创建订阅、发布评论，或故意触发风控。外部副作用检查项只能由已获授权的执行者逐项执行。

## 记录规则与安全基线

### 执行信息

| 字段 | 执行时填写（仅脱敏值） |
| --- | --- |
| 执行窗口编号 | `PENDING` |
| 执行者 | `PENDING` |
| 开始/结束时间（含时区） | `PENDING` |
| MoviePilot 版本与插件版本/commit | `PENDING` |
| 站点（Xiaohongshu 或 RedNote） | `PENDING` |
| 总结状态 | `PENDING / NOT RUN` |

### 严格禁止记录

不得在本清单、插件日志摘录、截图、工单或提交中记录 Cookie、`xsec_token`、MoviePilot API token、LLM API key、原始通知内容/JSON、二维码内容或稳定 user ID 全值。仅可记录：时间戳、脱敏 request ID、状态 code、必要时的脱敏标题、授权账号代号，以及稳定 user ID 的不可逆散列短前缀或末尾 4 位。

截图必须先遮盖地址栏查询参数、二维码、Cookie、token、完整用户 ID、完整评论与通知正文。任何请求 ID、标题或回复 ID 均应只保留足以关联本次记录的脱敏片段。

### 默认安全基线（每个非副作用项开始前复核）

| 配置 | 要求 |
| --- | --- |
| `enable_subscription` | `false` |
| `reply_enabled` | `false` |
| 轮询 | 仅在对应检查项内执行最少次数；其余时间关闭 |
| 授权用户 | 仅配置本次检查所需主号的脱敏对应值；昵称不得作为授权依据 |
| 站点风险 | 不主动制造 captcha、HTTP `403`、`429` 或错误码 `300012`；出现任何一项即停止并按页面恢复指引处理 |
| 回滚 | 每项结束恢复安全基线；删除临时测试数据或将其标记为已处理，但不得删除用户既有订阅/媒体数据 |

### 外部副作用授权闸门

下列项目开始前，执行者必须取得用户针对该单项、该时间窗口的再次明确授权，并在本行填写授权时间与范围；没有授权即保持 `PENDING / NOT RUN`：

| 项目 | 需要的再次授权 | 授权记录 |
| --- | --- | --- |
| C-01 真实订阅 | 允许为指定的唯一测试影视创建恰好一条订阅 | `PENDING` |
| R-01 公开回复 | 允许向指定授权评论发布恰好一条固定模板回复 | `PENDING` |
| L-01/L-02 登录与轮询 | 允许使用专用账号扫码、打开通知页并进行最少轮询 | `PENDING` |

### 通用单项留证模板

每个项目填入：`状态（PENDING / PASS / FAIL / BLOCKED / NOT RUN）`、`时间戳`、`脱敏 request ID`、`状态 code`、`必要时脱敏标题`、`证据位置`、`清理确认`。禁止复制原始通知、完整评论、完整账号标识或任何凭据。

## 0. 环境、版本与回滚前置

### E-01 环境与回滚点

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：插件未启用轮询；`enable_subscription=false`；`reply_enabled=false`；确认使用 MoviePilot `>= 2.15.6` 与受控专用账号。
- 步骤：记录插件版本/commit、MoviePilot 版本、部署时间与站点选择；确认可在插件页面关闭轮询、关闭真实订阅、关闭公开回复，并记录现有订阅数量的非敏感汇总值。若需要检查 SQLite，仅以只读方式查看 `app.db` 及相应 WAL/SHM 的一致快照，不能修改用户数据库。
- 期望结果：版本兼容；安全基线已保存；存在无需接触真实平台即可恢复的插件配置回滚路径。
- 可记录证据：版本号、commit 短 SHA、时间戳、设置页脱敏截图、订阅数量汇总、数据库快照校验摘要。
- 清理/恢复：退出设置页前再次确认两个开关均为 `false`，轮询关闭；不保留含敏感值的导出文件。

## 1. 浏览器、登录与 Profile

### B-01 Chromium 显式安装

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：E-01 已通过；已获 L-01 的登录与轮询窗口授权；轮询仍关闭，两个副作用开关仍为 `false`。
- 步骤：仅在 MoviePilot 插件详情页点击“安装 Chromium”，等待安装任务结束；不启动手工浏览器会话，不开始登录或轮询。
- 期望结果：详情页 Chromium 状态为可用/已安装，失败时显示可执行恢复原因；安装不阻塞页面且不改变订阅或回复开关。
- 可记录证据：时间戳、Chromium 状态 code、插件版本、脱敏安装结果截图或日志摘要。
- 清理/恢复：保持轮询关闭；安装失败只记录错误类别，按部署镜像依赖说明恢复，不把浏览器依赖或任何凭据写入插件配置。

### L-01 QR 登录与 RedNote 跳转

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：B-01 已通过；获得 L-01 授权；仅使用专用账号；`enable_subscription=false`、`reply_enabled=false`、轮询关闭。
- 步骤：在插件详情页生成登录二维码并由用户扫码。若登录页自动跳转国际站，在站点下拉框选 `RedNote` 后重新生成二维码并完成登录；不得保存、截图或传播二维码内容。
- 期望结果：浏览器状态为 `READY`，登录状态不再为 `WAITING_FOR_SCAN`；若发生站点跳转，已明确记录为 `RedNote` 且可继续到通知页。
- 可记录证据：时间戳、站点选择、`READY`/登录状态 code、二维码已遮盖的页面截图；不得记录二维码、Cookie 或 URL token。
- 清理/恢复：关闭轮询；若未成功，执行插件页面的退出/重新生成流程，不尝试规避验证码、`403`、`429` 或 `300012`。

### B-02 插件 reload 后 Profile 持久化

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：L-01 已成功，且专用账号已登录；两个副作用开关为 `false`，无需轮询。
- 步骤：在 MoviePilot 受控维护窗口 reload 插件；reload 后只通过插件页面检查登录状态，不重新扫码，不执行轮询。
- 期望结果：同一持久化 Profile 仍使浏览器状态回到 `READY`，登录态仍有效；没有创建订阅、公开回复或新媒体请求。
- 可记录证据：reload 前后时间戳、浏览器/登录状态 code、Profile 路径是否存在的非敏感布尔结果。
- 清理/恢复：确认 `enable_subscription=false`、`reply_enabled=false`、轮询关闭；如登录失效转入 P-01 的 mocked/admin 注入检查，不进行风险触发。

## 2. 授权、dry-run、幂等与去重

### A-01 已授权主号 dry-run

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：L-01 和 B-02 已通过；配置唯一授权主号，仅记录其代号及哈希短前缀或末尾 4 位；`enable_subscription=false`、`reply_enabled=false`；MoviePilot 通知按 N-01 设置。
- 步骤：由授权主号提交一条内容明确的测试 mention；在受控窗口执行一次轮询，等待处理结束。
- 期望结果：该请求到达 `DRY_RUN_MATCHED` 或 `NEED_CONFIRMATION`；若为匹配结果，不创建真实订阅或公开回复。
- 可记录证据：时间戳、脱敏 request ID、最终状态 code、必要时脱敏标题/年份/类型、SQLite 行数增量、通知结果摘要。
- 清理/恢复：关闭轮询；保持两个副作用开关为 `false`；将测试请求按页面流程忽略或保留为审计记录，不能复制原始评论。

### A-02 未授权号隔离

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：A-01 已完成；第二账号不在授权列表，只以代号及哈希短前缀或末尾 4 位留证；`enable_subscription=false`、`reply_enabled=false`。
- 步骤：由未授权号提交一条 mention，并执行一次最小轮询；仅查看插件请求列表、resolver 调用观测和 MP 调用观测的计数/状态摘要。
- 期望结果：未授权 mention 不进入 LLM/resolver 或 MoviePilot，不写入媒体请求；不创建订阅、不发送公开回复。
- 可记录证据：时间戳、未授权账号代号、请求列表增量为 `0`、resolver/MP 调用计数增量为 `0`、状态 code（如有）。
- 清理/恢复：关闭轮询；从临时授权配置中确认未授权号从未加入；两个副作用开关保持关闭。

### D-01 相同 mention 双轮询幂等

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：使用 A-01 的同一授权 mention；`enable_subscription=false`、`reply_enabled=false`；具备只读 SQLite 计数与 resolver 调用计数观测方式。
- 步骤：对同一 mention 连续轮询两次，不重新发布评论；比较两次后的请求行数和 resolver 调用数。
- 期望结果：同一 mention 仅一条 SQLite `xhs_requests` 记录，resolver 仅调用一次；最终状态不因第二次轮询产生新订阅或新回复。
- 可记录证据：两次时间戳、同一脱敏 request ID、SQLite 行数 `1`、resolver 调用数 `1`、最终状态 code。
- 清理/恢复：关闭轮询；保留最小审计摘要，不导出数据库中任何原始通知或账号信息。

### I-01 已在媒体库

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：选择已在用户媒体库中的无歧义影视；`enable_subscription=false`、`reply_enabled=false`；授权主号可用。
- 步骤：使用授权主号提交对应测试 mention，执行一次轮询。
- 期望结果：最终为 `ALREADY_IN_LIBRARY`；不创建订阅、不公开回复（除非 R-01 已单独获授权，但本项默认不启用）。
- 可记录证据：时间戳、脱敏 request ID、`ALREADY_IN_LIBRARY`、必要时脱敏标题、媒体库存在性的布尔摘要、通知结果摘要。
- 清理/恢复：关闭轮询；不删除或变更既有媒体库内容；保持安全基线。

### I-02 已订阅

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：选择已有 MoviePilot 订阅、且不在媒体库中的无歧义影视；`enable_subscription=false`、`reply_enabled=false`。
- 步骤：使用授权主号提交对应测试 mention，执行一次轮询。
- 期望结果：最终为 `ALREADY_SUBSCRIBED`；订阅总数不增加，且没有公开回复。
- 可记录证据：时间戳、脱敏 request ID、`ALREADY_SUBSCRIBED`、订阅数量前后相同的汇总值、必要时脱敏标题、通知结果摘要。
- 清理/恢复：关闭轮询；不修改既有订阅；恢复安全基线。

### M-01 模糊笔记 NEED_CONFIRMATION

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：选择或重新处理一条刻意模糊、不会唯一匹配作品的授权测试 mention；`enable_subscription=false`、`reply_enabled=false`；MP 通知已启用。
- 步骤：执行一次轮询或对该记录执行一次“重新处理”；不手动指定影片，不开启真实订阅。
- 期望结果：状态为 `NEED_CONFIRMATION`；发出一条 MoviePilot 通知；订阅数量不增加，公开回复数不增加。
- 可记录证据：时间戳、脱敏 request ID、`NEED_CONFIRMATION`、通知送达状态 code、订阅数量前后汇总。
- 清理/恢复：关闭轮询；可在插件页面将该请求标记为忽略；保持两个副作用开关关闭。

## 3. MoviePilot 通知与受控副作用

### N-01 MoviePilot/企业微信通知

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：用户已配置 MP 消息渠道（可为企业微信）；`enable_subscription=false`、`reply_enabled=false`；不得在记录中保留 raw notification、Webhook、token 或消息正文。
- 步骤：先使用插件“测试通知”或 A-01/M-01 的非副作用结果触发一条通知；仅在用户端确认送达，不转发或截图原始内容。
- 期望结果：MoviePilot 显示通知发送成功，企业微信（如配置）收到恰好一条对应通知；通知开关不改变订阅或公开回复开关。
- 可记录证据：时间戳、插件通知状态 code、渠道代号、送达/未送达布尔结果、关联的脱敏 request ID（如有）。
- 清理/恢复：不重复点击测试以免重复通知；保持两个副作用开关关闭。

### C-01 单条受控真实订阅（需要再次明确授权）

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：C-01 授权闸门已填写；A-01、D-01、M-01、I-01、I-02 均已通过；选择一部未入库、未订阅、标题/年份/类型唯一且可接受下载的作品；确认使用既有 MoviePilot 质量、站点、下载器默认值。
- 步骤：只在处理该一条请求前临时将 `enable_subscription=true`；`reply_enabled=false`。执行一次轮询，确认 `SUBSCRIBED` 后立即将 `enable_subscription=false`，不处理其他 mention。
- 期望结果：恰好新增一条 MoviePilot 订阅，状态为 `SUBSCRIBED`；没有重复订阅、没有公开回复，且未传入自定义质量/站点/下载器参数。
- 可记录证据：授权时间、开始/结束时间、脱敏 request ID、`SUBSCRIBED`、订阅总数前后增量 `+1`、脱敏稳定媒体标识/订阅标识、必要时脱敏标题。
- 清理/恢复：**立即**关闭真实订阅和轮询；核查新增订阅恰为目标一条。若用户授权撤销该测试订阅，按 MoviePilot 正常 UI 删除并记录删除结果；否则保留，不得触碰其他订阅。

### R-01 可选单次公开回复（需要再次明确授权）

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：R-01 授权闸门已填写；选择授权主号的安全测试评论；`enable_subscription=false`，仅启用“成功”固定回复模板和 `reply_enabled=true`；其余回复类别关闭。
- 步骤：处理一条可安全成功的测试请求，验证原触发评论下恰有一条固定模板回复；对相同 mention 再轮询一次。
- 期望结果：首次回复状态为 `SENT`，第二次轮询不发送第二条回复；没有真实订阅。
- 可记录证据：授权时间、时间戳、脱敏 request ID、`SENT`、回复计数 `1`、复轮询后计数仍为 `1`、必要时仅记录模板类别，不记录回复原文或 reply ID 全值。
- 清理/恢复：**立即**将 `reply_enabled=false` 并关闭“成功”模板；关闭轮询。已发布的单条回复只在用户明确授权时按平台 UI 删除，不能擅自删除。

## 4. 登录失效、暂停与恢复（不制造平台风险）

### P-01 mocked/admin 注入登录失效

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：使用 mocked/admin diagnostic endpoint 或等价受控诊断能力注入登录失效；不得通过真实站点触发。`enable_subscription=false`、`reply_enabled=false`、公开回复模板关闭，MP 通知启用。
- 步骤：注入 `LOGIN_REQUIRED`、`LOGGED_OUT` 或受控的会话过期状态；执行一次受控轮询/诊断读取，并再次请求一次以检验去重。
- 期望结果：浏览器状态变为 `PAUSED`；停止自动读取和公开回复；MoviePilot 只发送一次暂停通知；不创建订阅或新公开回复。
- 可记录证据：时间戳、注入的非敏感状态 code、`PAUSED`、`pause_notified=true` 的布尔结果、通知计数 `1`、第二次检查后的通知计数仍为 `1`。
- 清理/恢复：移除 mock/admin 注入状态；不要故意触发 captcha、`403`、`429` 或 `300012`，也不通过真实站点令 Cookie 失效。

### P-02 手动 resume 与 dry-run 恢复

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：P-01 已通过且受控注入已清除；会话已通过正常插件页面确认可用或已在授权窗口重新扫码；`enable_subscription=false`、`reply_enabled=false`。
- 步骤：在插件页面点击“恢复轮询”，仅处理一条已准备的安全 dry-run mention。
- 期望结果：暂停被清除，下一次轮询成功处理为 `DRY_RUN_MATCHED` 或 `NEED_CONFIRMATION`；不自动重试旧风险，不创建订阅或公开回复。
- 可记录证据：时间戳、resume 前后浏览器状态 code、脱敏 request ID、最终 dry-run 状态、订阅/回复计数均无增量。
- 清理/恢复：关闭轮询并再次核对安全基线；若恢复失败，记录错误类型和用户可执行恢复步骤，不循环重试。

## 5. 结束检查与签核

### F-01 安全回收与结果汇总

- [ ] **状态：`PENDING / NOT RUN`**
- 配置前提：所有获授权项目已逐项结束或显式标为未执行。
- 步骤：确认 `enable_subscription=false`、`reply_enabled=false`、轮询关闭；核对新增订阅/公开回复仅限已授权的单项；对未通过项保留最小脱敏证据并列出恢复动作。
- 期望结果：不存在未授权外部副作用；所有条目都有状态、时间戳和清理/恢复结果；敏感信息未进入记录。
- 可记录证据：安全基线最终截图（已脱敏）、项目状态汇总、订阅/回复计数变化汇总、签核时间。
- 清理/恢复：关闭受控窗口；撤销临时授权配置；不删除用户既有数据或凭据。

| 签核角色 | 姓名/代号 | 时间 | 结论 |
| --- | --- | --- | --- |
| 执行者 | `PENDING` | `PENDING` | `PENDING / NOT RUN` |
| 外部副作用授权人 | `PENDING` | `PENDING` | `PENDING / NOT RUN` |
