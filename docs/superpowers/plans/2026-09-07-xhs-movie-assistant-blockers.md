# XhsMovieAssistant Merge Blocker Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development and superpowers:test-driven-development. Every production change starts with a focused failing test and records RED/GREEN evidence.

**Goal:** Close the four Important findings left by the 2026-09-06 final scoped review so the MoviePilot V2 plugin can be reviewed for merge again.

**Architecture:** Keep the existing plugin boundaries. Recovery consumes the durable sanitized note snapshot already stored in SQLite. Repository methods own atomic request/reply state plus business-notification outbox writes. Notification delivery prioritizes safety events in SQL. Worker locks are released on every thread exit. The detail page uses MoviePilot's supported Vue federation render mode because v2.15.6 `PageRender.vue` sends only static event params and cannot bind editable per-row fields.

**Tech Stack:** Python 3.11+, MoviePilot V2 `>=2.15.6`, SQLite WAL, Pydantic V2, pytest 8.4, Vue 3/Vite federation assets for the plugin detail/config UI.

**Spec:** `docs/superpowers/specs/2026-09-06-xhs-movie-assistant-design.md`

## Global Constraints

- Keep all existing authorization, secret-redaction, dry-run, idempotency, and cancellation guarantees.
- Do not access real XHS/RedNote, MoviePilot, enterprise WeChat, or any user service during implementation or tests.
- Do not start Chromium, scan QR codes, create subscriptions, or publish comments.
- Use only MoviePilot V2 interfaces available in `>=2.15.6`; no V3-only API.
- Page rendering must remain free of browser, LLM, MoviePilot chain, and notification side effects.
- All plugin write routes remain `POST` with `auth="bear"`.
- Do not persist or render cookies, `xsec_token`, API keys, raw notification JSON, or full stable user IDs.
- Preserve the existing `src/xhs_probe` suite and the pre-existing untracked `src/xhs_mp_bridge.egg-info/` directory.

---

### Task 1: Resume Durable NEW Requests Without Refetching XHS

**Files:**
- Modify: `plugins.v2/xhsmovieassistant/service.py`
- Modify: `tests/plugin/test_service.py`

**Required behavior:**
- Authenticated `reprocess(request_id)` accepts a recovered request already in `NEW` without calling `repository.requeue()`.
- A recovered `NEW` request with a durable `NoteContext` snapshot resumes through resolver and MoviePilot even when the old mention is absent from the latest XHS notification page.
- Reprocessing does not fetch XHS again, does not require a transient `xsec_token`, and does not increment `attempt_count` a second time after `recover_interrupted()` already did so.
- Existing terminal-state requeue behavior and authorization checks remain unchanged.

- [ ] Write a regression test that persists `FETCHED -> RESOLVING`, runs `recover_interrupted()`, builds a restarted service with no returned mentions, and calls authenticated `reprocess()` directly.
- [ ] Run the focused test and verify RED because `requeue()` rejects `NEW`.
- [ ] Implement the minimum status-aware reprocess change.
- [ ] Run `tests/plugin/test_service.py` and the full suite; record RED/GREEN output.
- [ ] Commit with Chinese message `fix: 恢复持久化的新请求`.

### Task 2: Release The Activity Lock On Stop-Entry

**Files:**
- Modify: `plugins.v2/xhsmovieassistant/__init__.py`
- Modify: `tests/plugin/test_plugin.py`

**Required behavior:**
- Once `_start_worker()` acquires `_activity_lock`, every thread exit path releases it exactly once.
- If the poll stop event is set after the thread is created but before its operation begins, the poll operation is not called, the worker exits, and a later management action can acquire the lock.
- Existing generation checks, bounded stop behavior, non-reentrant polling, and worker-start failure cleanup remain unchanged.

- [ ] Write a deterministic regression test that sets the stop event at thread entry and proves the operation is skipped and `_activity_lock` is reacquirable.
- [ ] Run the focused test and verify RED because the current early return bypasses `finally`.
- [ ] Move the stop-entry branch under the existing `try/finally` without broad lifecycle refactoring.
- [ ] Run `tests/plugin/test_plugin.py` and the full suite; record RED/GREEN output.
- [ ] Commit with Chinese message `fix: 释放提前停止的任务锁`.

### Task 3: Make Business Outbox Writes Atomic And Prioritize Safety Events

**Files:**
- Modify: `plugins.v2/xhsmovieassistant/repository.py`
- Modify: `plugins.v2/xhsmovieassistant/service.py`
- Modify: `plugins.v2/xhsmovieassistant/notifications.py`
- Modify: `tests/plugin/test_repository.py`
- Modify: `tests/plugin/test_service.py`

**Required behavior:**
- When business notifications are enabled, every terminal request transition and every persisted reply failure writes its metadata-only outbox event in the same SQLite transaction as the state mutation.
- If the outbox insert fails, the corresponding request/reply state mutation rolls back; the exception is not silently converted into a committed terminal state with no notification.
- Cancellation after the transaction cannot remove the already-durable outbox event.
- When business notifications are disabled, no result/reply business event is created.
- `PAUSE` events are selected before business events. With business notifications disabled, pending business backlog cannot consume the query limit or starve a later safety event.
- Dedupe material remains SHA-256 hashed before persistence; raw titles, comments, tokens, and notification bodies are never stored in the outbox.

- [ ] Add real SQLite transaction rollback tests using a failing outbox insert, plus a cancellation-window service regression.
- [ ] Add a backlog test with more than 20 disabled business events followed by one `PAUSE` event; verify the safety event is delivered.
- [ ] Run the focused tests and verify RED for both atomicity and starvation.
- [ ] Implement small repository transaction helpers and SQL-level safety prioritization; remove the post-transition best-effort business enqueue path.
- [ ] Run repository/service/notification-related tests and the full suite; record RED/GREEN output.
- [ ] Commit with Chinese message `fix: 原子提交通知并优先安全事件`.

### Task 4: Add An Editable Manual Resolution Page

**Files:**
- Modify: `plugins.v2/xhsmovieassistant/__init__.py`
- Create: `plugins.v2/xhsmovieassistant/package.json`
- Create: `plugins.v2/xhsmovieassistant/vite.config.js`
- Create: `plugins.v2/xhsmovieassistant/src/main.js`
- Create: `plugins.v2/xhsmovieassistant/src/components/Config.vue`
- Create: `plugins.v2/xhsmovieassistant/src/components/Page.vue`
- Create/update generated build assets under: `plugins.v2/xhsmovieassistant/dist/`
- Modify: `tests/plugin/test_plugin.py`
- Modify: `tests/plugin/test_package.py`
- Modify: `README.md`

**Required behavior:**
- Declare MoviePilot's supported Vue federation render mode and ship installable `dist/assets` output in the plugin package.
- Preserve every existing configuration field/default, including dry-run and all four category reply switches.
- The detail page still shows cached service/login state and recent request/match/error/reply results, and exposes the existing authenticated management/diagnostic operations.
- For each actionable request, the user can edit title, optional original title, media type (`movie|tv`), optional year, and optional season before submitting `/requests/{id}/manual`.
- The UI rejects an empty title or unknown media type before submission, shows actionable success/failure feedback, and refreshes the cached request list after an action.
- The backend remains authoritative: `_manual_resolution()` strict validation, current sender authorization, deterministic MoviePilot matching, duplicate checks, and dry-run/real-subscription switch still apply.
- No API key/token is embedded in generated assets or query strings; use the authenticated plugin API client supplied by MoviePilot's remote component host.

- [ ] Add package/render-mode tests that fail until source and built federation assets exist and contain no tracked secrets.
- [ ] Add focused frontend component tests or a deterministic build/source contract test proving user-edited values, not cached static params, form the manual POST body.
- [ ] Verify RED before changing render mode or adding frontend production files.
- [ ] Implement the smallest Vue Config/Page remote by following the official v2.15.6 federation pattern; build tracked `dist` assets.
- [ ] Run frontend build/tests, Python plugin/package tests, and the full Python suite; record RED/GREEN output.
- [ ] Commit with Chinese message `feat: 提供可编辑的人工确认页面`.

## Final Verification

- Run `.venv/bin/pytest -o addopts='' -q`.
- Run coverage with `--cov-fail-under=80`.
- Run `compileall` and `git diff --check`.
- Run package secret scanning and confirm only the pre-existing `src/xhs_mp_bridge.egg-info/` is untracked.
- Re-run the bounded MoviePilot v2.15.6 compatibility smoke without adding unrelated downloader dependencies and without claiming the blocked runtime-chain portion passed.
- Perform a broad whole-branch review against this plan and the binding design before presenting integration options.
