# 小红书影视助手

当前仓库已实现 Phase 1 至 Phase 3：

- 复用 DSM 上已有的 CloakBrowser Manager，不再打包 Chromium。
- 由人工完成小红书小号登录和验证码。
- 捕获一次“评论和 @”接口响应并输出脱敏字段摘要。
- 只接受配置的稳定主号 `user_id`，其他用户请求不入库。
- 使用 SQLite 保存请求，并按 mention 和规范化请求双重去重。
- 不调用 AI，不连接 MoviePilot，不创建订阅，不回复小红书。

## 准备 CloakBrowser Profile

DSM 上的 CloakBrowser Manager 当前入口是：

```text
http://192.168.0.153:9050
```

在管理页面新建一个只供本项目使用的 Profile：

- 名称建议使用 `xhs-movie-assistant`。
- 保持 `Headless` 关闭，以便人工扫码和处理验证码。
- 开启 `Auto launch`，使 DSM 或 CB 重启后自动恢复浏览器。
- 不要复用其他业务 Profile，避免 Cookie 和标签页互相影响。

启动该 Profile，在网页远程桌面中打开通知页并登录小号。Profile 数据已由 CB 持久化到 DSM 的 `/volume4/docker/docker/makerhub/cloakbrowser`。

如果扫码时提示账号属于 RedNote 并跳转国际站，登录实际会保存在 `rednote.com`。此时将 `XHS_NOTIFICATION_URL` 设置为：

```text
https://www.rednote.com/notification
```

国内站账号继续使用默认的 `https://www.xiaohongshu.com/notification`。探针会按该配置选择浏览器标签页，两个站点使用相同的 mentions API path。

## 启动 Bridge

根据 `.env.example` 创建 Git 忽略的 `.env`。从 CB Profile 页面复制 CDP endpoint，将 Profile ID 写入 `XHS_CDP_URL`；`XHS_CDP_TOKEN` 使用 CB 的 Access Token；`XHS_NOTIFICATION_URL` 根据账号所属站点配置。不要提交 `.env`。

启动轻量 bridge 容器：

```bash
sudo docker compose up -d --build
```

该 Compose 不再启动第二个浏览器，只通过带 Bearer Token 的 CDP 连接现有 CB Profile。

## 捕获一次通知响应

确认小号已登录后，用主号在一篇小红书笔记中 @ 小号。然后执行：

```bash
sudo docker compose exec xhs-mp-bridge xhs-phase1-probe
```

命令只在标准输出显示通知数量、通知类型和必需字段计数。原始响应写入 `data/app/probe/`，目录权限为 `0700`，文件权限为 `0600`，并被 Git 忽略。

若结果为 `timeout`，先检查远程 Chromium 是否仍保持登录，再执行一次探针。不要连续高频重试。

## 导入 SQLite 并验证去重

在 `.env` 中配置主号的稳定 ID：

```text
AUTHORIZED_XHS_USER_ID=replace-with-main-account-user-id
XHS_DATABASE_PATH=/data/app.db
```

将一次 Phase 1 捕获结果离线导入 SQLite：

```bash
sudo docker compose exec xhs-mp-bridge \
  xhs-phase3-import /data/probe/mentions-YYYYMMDDTHHMMSSZ.json
```

命令只输出创建、重复、忽略和无效记录的数量，不输出用户 ID、评论或 token。对同一捕获文件再次执行时，记录应计入 `duplicate_count`，数据库仍只保留一条请求。

SQLite 表为 `xhs_requests`，数据库文件和原始捕获文件都位于已持久化的 `data/app/` 目录。数据库不会保存 Cookie、CDP Token 或 `xsec_token`。

## 验证与停止

```bash
pytest -q
sudo docker compose config -q
sudo docker compose ps
sudo docker compose stop
```

Phase 1 通过标准：

1. CB Profile 中能正常登录小红书小号。
2. 重启 CB Profile 后登录状态仍保留。
3. 小号网页能看到主号发出的新 @。
4. 探针能捕获 mentions API 响应，且没有验证码、HTTP 403/429 或异常退出。

Phase 3 通过标准：

1. 只有 `AUTHORIZED_XHS_USER_ID` 对应账号的通知会生成请求。
2. 同一 `mention_id` 重复导入不会新增记录。
3. 同一笔记、发送者和规范化评论即使使用新的 mention，也不会新增记录。
4. `app.db` 权限为 `0600`，且不包含 Cookie、CDP Token 或 `xsec_token`。
