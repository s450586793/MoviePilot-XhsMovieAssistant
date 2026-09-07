# Task 3 Report: 原子提交通知并优先安全事件

## 范围与基线

- Base：`0b356faf53c6fc48c5c6810716c927d4c5db5f76`
- 所属文件：`plugins.v2/xhsmovieassistant/{repository,service,notifications}.py`、
  `tests/plugin/{test_repository,test_service}.py`、本报告。
- 发现预先存在的未跟踪目录：`src/xhs_mp_bridge.egg-info/`；未读取、修改、暂存或提交。

## TDD 证据

### RED：原子性与饥饿

先新增 4 个用例：两个用真实 SQLite `BEFORE INSERT` trigger 使 outbox `INSERT`
返回 `RAISE(ABORT, ...)`，分别验证终态和 reply failure 的状态回滚；一个在真实
终态 transition 返回后设置 cancellation，验证 durable result event 不丢失；一个制造
21 条 disabled business event 后追加 `PAUSE`，验证 safety event 不会被 limit 饿死。

```bash
.venv/bin/pytest -o addopts='' -q tests/plugin/test_repository.py -k 'atomic_result_outbox or reply_failure_rolls_back'
```

```text
FF                                                                       [100%]
E TypeError: RequestRepository.transition() got an unexpected keyword argument 'business_notifications_enabled'
E TypeError: RequestRepository.mark_reply() got an unexpected keyword argument 'business_notifications_enabled'
2 failed, 28 deselected in 0.94s
```

失败原因：repository 没有一个能在请求/reply mutation 所在 SQLite transaction 中写入
business event 的 API。

```bash
.venv/bin/pytest -o addopts='' -q tests/plugin/test_service.py -k 'cancellation_after_terminal or pause_event_is_delivered_after_disabled'
```

```text
FF                                                                       [100%]
E AssertionError: assert [] == [('REQUEST_RESULT', 'DRY_RUN_MATCHED')]
E AssertionError: assert [] == [('小红书监听已暂停', '暂停原因：AUTH_REQUIRED')]
2 failed, 42 deselected in 0.93s
```

失败原因：service 在已提交终态后才 best-effort enqueue，cancellation 会跳过该路径；
`pending_notifications()` 先按 id 加 `LIMIT 20`，因此 disabled business backlog
占满窗口后 `PAUSE` 不会被送达。

### GREEN

```bash
.venv/bin/pytest -o addopts='' -q tests/plugin/test_repository.py -k 'atomic_result_outbox or reply_failure_rolls_back'
.venv/bin/pytest -o addopts='' -q tests/plugin/test_service.py -k 'cancellation_after_terminal or pause_event_is_delivered_after_disabled'
```

```text
2 passed, 28 deselected in 0.97s
2 passed, 42 deselected in 0.70s
```

## 设计

- `repository._enqueue_notification()` 接受活动 SQLite connection；它在持久化前用
  SHA-256 处理 dedupe material，并仅写 kind、request id、safe code、hashed key 和
  delivery metadata。
- `transition(..., business_notifications_enabled=True)` 对所有终态在同一 transaction
  写 `REQUEST_RESULT`；`mark_reply(..., status=FAILED, ...)` 同样写 `REPLY_FAILURE`。
  outbox insert 失败会使 context manager rollback 并原样传播异常。
- service 删除终态后的 best-effort business enqueue，所有 service state mutation 经由
  `_transition()` 传递当前 notification setting；disabled 时终态和 reply failure 都不
  创建 business event。Task 1 的 authenticated `NEW` reprocess 和其它状态 requeue
  判断未修改。
- `pending_notifications(..., business_enabled=False)` 在 SQL 过滤 business rows；在
  business 开启时也以 `PAUSE` 优先排序。flush 使用该开关，所以 backlog 不会消耗
  safety delivery 的查询 limit。

## 回归验证

```bash
.venv/bin/pytest -o addopts='' -q tests/plugin/test_repository.py tests/plugin/test_service.py tests/plugin/test_plugin.py
```

```text
131 passed in 4.52s
```

```bash
.venv/bin/pytest -o addopts='' -q
```

```text
390 passed in 5.31s
```

## 自审

- `python -m compileall -q` 和 `git diff --check` 通过。
- 新增 SQLite trigger 用例证明 outbox failure 会 rollback request/reply mutation，且不
  会把异常转成无通知的已提交状态。
- cancellation regression 在终态 transaction 已返回后才取消，仍读到 pending result
  event；disabled reply failure 用例同时证明不会创建 result 或 reply business event。
- 现有 metadata-only persistence 测试仍验证 secret dedupe material 不落库；本次新
  dedupe material 也只在 `_enqueue_notification()` 中先 SHA-256 后写入。
- 未执行真实 XHS/RedNote、MoviePilot、企业微信、Chromium 或登录相关操作。
