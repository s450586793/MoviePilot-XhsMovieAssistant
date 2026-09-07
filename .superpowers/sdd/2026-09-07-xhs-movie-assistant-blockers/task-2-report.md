# Task 2 Report: Release The Activity Lock On Stop-Entry

## 范围与基线

- Base：`771ee61cf343f63eae701dfe4455e32ce856eeab`
- 所属文件：`plugins.v2/xhsmovieassistant/__init__.py`、`tests/plugin/test_plugin.py`、本报告。
- 发现的预先存在未跟踪目录：`src/xhs_mp_bridge.egg-info/`；未读取、修改、暂存或提交。

## TDD 证据

### RED

先新增 `test_poll_worker_stopped_at_entry_releases_activity_lock`。该测试将
`entrypoint.threading.Thread` 替换为受控同步线程，在调用实际线程 target 前设置
`plugin._stop_event`。它验证：poll operation 没有执行、worker 已退出，并且
`_activity_lock` 能被后续调用重新取得。

命令：

```bash
.venv/bin/pytest -o addopts='' -q tests/plugin/test_plugin.py -k poll_worker_stopped_at_entry_releases_activity_lock
```

输出：

```text
F                                                                        [100%]
FAILED tests/plugin/test_plugin.py::test_poll_worker_stopped_at_entry_releases_activity_lock
E       assert False is True
E        +  where False = <built-in method acquire of _thread.lock object ...>(blocking=False)
1 failed, 56 deselected in 1.27s
```

失败原因：`run()` 在 poll stop-entry 分支直接 `return`，发生在既有 `try/finally`
之前，因此已取得的 `_activity_lock` 未释放。

### GREEN

将 poll stop-entry 判断和 activity 状态设置移入原有 `try` 块。早退现在会执行既有
`finally` 中的 lock release；generation 检查、bounded stop、non-reentrant polling 和
`worker.start()` 异常清理均未改变。

命令：

```bash
.venv/bin/pytest -o addopts='' -q tests/plugin/test_plugin.py -k poll_worker_stopped_at_entry_releases_activity_lock
```

输出：

```text
.                                                                        [100%]
1 passed, 56 deselected in 0.70s
```

## 回归验证

```bash
.venv/bin/pytest -o addopts='' -q tests/plugin/test_plugin.py
```

```text
57 passed in 1.23s
```

```bash
.venv/bin/pytest -o addopts='' -q
```

```text
386 passed in 4.86s
```

## 自审

- `git diff --check` 通过。
- 回归测试使用真实 `_start_worker()` 和真实 lock；仅替换线程调度以确定性制造 stop-entry 窗口。
- 若删除本次生产修复，聚焦测试恢复 RED，证明覆盖了原始锁泄漏。
- 未执行任何真实 XHS/RedNote、MoviePilot、企业微信、Chromium 或登录相关操作。
