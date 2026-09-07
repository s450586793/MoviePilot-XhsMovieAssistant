# Task 4 Report: Editable Manual Resolution Page

## Scope

- Base: `ca6b301ea21666430af995c85b49a6a682ce2b58`.
- Implemented MoviePilot `vue` federation for `XhsMovieAssistant`; its render assets are tracked below `plugins.v2/xhsmovieassistant/dist/assets`.
- Preserved the existing Task 2 activity lock. The manual endpoint remains the service boundary and backend validation authority.
- No live XHS, MoviePilot, browser, QR scan, subscription, reply, notification, or LLM operation was started.

## Reference Evidence

The implementation follows the supplied MoviePilot v2.15.6 references:

- `plugins.v2/courseorganizer/package.json`, `vite.config.js`, and `src/main.js`: Vue, Vite 5, the OriginJS federation plugin, `remoteEntry.js`, `./Config` and `./Page` exposes.
- `plugins.v2/courseorganizer/src/components/Page.vue:4-6,72-78,194-200`: remotes receive the host `api` prop and unwrap the host response rather than create their own HTTP/authentication client.
- `plugins.v2/agentresourceofficer/vite.config.js:1-49` and `src/components/Config.vue:1-28`: same federation configuration and host-provided `initialConfig` pattern.
- `plugins.v2/hrblocker/__init__.py:get_api`: separate route registration pattern used as the baseline for adding the authenticated read-only state endpoint.
- `frontend/src/pages/plugin-app.vue:61-80` and `src/components/dialog/PluginConfigDialog.vue:65-67,245-255`: v2.15.6 injects an instance-scoped generic API client as `api`; it owns authentication and source-instance routing. The remote must not add an API key or query token.

## RED

Tests were added before the implementation and run while no production federation files or `/state` route existed.

```text
.venv/bin/pytest -o addopts='' -q tests/plugin/test_plugin.py \
  -k 'api_routes_are_post_only or state_endpoint_returns_cached or every_direct_endpoint_fails_closed'
3 failed, 11 passed, 45 deselected

.venv/bin/pytest -o addopts='' -q tests/plugin/test_package.py \
  -k 'vue_federation or vue_page_uses'
2 failed, 5 deselected
```

The first command failed because `/state` was absent. The second failed because the Vue package, sources, and `dist/assets` were absent. These were the expected RED conditions.

## Implementation

- [`__init__.py`](../../../../plugins.v2/xhsmovieassistant/__init__.py): `get_render_mode()` returns `("vue", "dist/assets")`; `GET /state` is bearer-authenticated, composes only cached/durable data, and sanitizes its payload. The durable row projection now includes `original_title` for editing.
- [`Config.vue`](../../../../plugins.v2/xhsmovieassistant/src/components/Config.vue): retains every backend default: dry-run/real subscription switches, four reply-category switches, site and polling fields, authorization IDs, and all five reply template status keys/defaults.
- [`Page.vue`](../../../../plugins.v2/xhsmovieassistant/src/components/Page.vue): loads `plugin/XhsMovieAssistant/state` through `props.api`; shows cached browser/login/activity/pause state plus durable match/error/reply fields; retains every management/diagnostic action. Actionable rows have editable title, original title, `movie|tv`, year, and season inputs. The manual body is built from the current draft, rejects empty titles and unknown media types locally, presents success/failure feedback, and refreshes after an action.
- [`vite.config.js`](../../../../plugins.v2/xhsmovieassistant/vite.config.js), package files, source entry, and generated `dist/assets`: use the official Vue federation contract. The scoped CSS is mobile-first, uses functional status colour, 44 px controls, 16 px inputs, stable desktop grids, dark mode, and reduced-motion support.

## GREEN And Verification

```text
npm install --no-audit --no-fund
added 36 packages in 26s

npm run build
vite v5.4.21 ... 14 modules transformed ... built in 1.76s

.venv/bin/pytest -o addopts='' -q tests/plugin/test_plugin.py tests/plugin/test_package.py
66 passed

.venv/bin/pytest -o addopts='' -q
396 passed in 5.82s

.venv/bin/pytest -o addopts='' -q --cov=xhsmovieassistant --cov-fail-under=80
396 passed; total coverage 89.05% (threshold 80%)

.venv/bin/pytest -o addopts='' -q tests/plugin/test_package.py \
  -k 'market_metadata_matches_plugin_class or vue_federation_package_and_tracked_build_are_installable'
2 passed, 5 deselected
```

The final bounded v2.15.6 compatibility smoke checks marketplace metadata (`system_version >=2.15.6`), the Vue render mode, package dependencies, and the tracked federation exposes. It does not claim that the real runtime chain passed.

本轮已补充 `npm test` 脚本及 Vitest 交互测试；前端行为由真实 Vue/jsdom 测试与 `tests/plugin/test_package.py` 的源码/构建产物契约共同覆盖。

Additional checks passed with no output:

```text
.venv/bin/python -m compileall -q plugins.v2/xhsmovieassistant
git diff --check
rg -n -i '(api[_-]?key|xsec[_-]?token|authorization|cookie|bearer ...|[?&](token|apikey|api_key)=)' \
  plugins.v2/xhsmovieassistant/dist plugins.v2/xhsmovieassistant/src
```

The secret scan found no matches. The build-only `plugins.v2/xhsmovieassistant/node_modules/` directory was removed before staging. The pre-existing untracked `src/xhs_mp_bridge.egg-info/` remains untouched.

## Self Review

- `GET /state` is explicit bearer auth and has a direct test proving it reads only the cached/durable boundaries while browser, LLM, MoviePilot, service, and notification collaborators would fail if called.
- The source/build contract verifies `props.api`, edited draft values in the manual body, client-side empty-title/type rejection, refresh after an action, full Config key coverage, and no credentials/query tokens in generated assets.
- Backend `_manual_resolution()` remains unmodified and authoritative for strict validation; sender authorization, deterministic matching, duplicate checks, and dry-run/real subscription gates remain in the existing service path.
- Residual limitation: there is no live MoviePilot v2.15.6 host or external service exercise in this task, by design and task constraint.

## Fix Round 1: Manual Resolution Feedback And Touch Targets

### RED

在修改 `Page.vue` 与 `Config.vue` 前，新增的真实前端测试暴露了以下回归：

```text
npm test
Page.spec.js: 8 tests, 6 failed
Config.spec.js: 1 touch target failed
```

失败覆盖未知影视类型被隐式提交为 `movie`、`FAILED` 和 `NEED_CONFIRMATION` 被按成功展示、成功订阅和重复结果无差异反馈，以及按钮最小宽度未达到 44 px。此前 package contract 也因缺少 `test` script 以 `KeyError: 'test'` 失败。

### Changes

- `draftFor()` 保留接口返回的未知类型，只有用户明确选择 `movie` 或 `tv` 才允许人工确认提交。
- `actionFeedback()` 按业务状态展示 `FAILED`、`NEED_CONFIRMATION`、`SUBSCRIBED`、重复订阅/在库与 `DRY_RUN_MATCHED` 的不同提示；操作后仍刷新 `/state`。
- `Page.vue` 与 `Config.vue` 的所有可交互按钮统一最小宽度和高度为 44 px。
- 新增 Vitest、Vue Test Utils 与 jsdom；`vuetify-stubs.js` 只替换 Vuetify 展示层，页面状态、事件和请求体保持真实运行。
- 新增 10 个前端交互测试，覆盖类型拒绝、编辑值精确提交、空标题拒绝、业务反馈/刷新、配置默认值和所有按钮的 jsdom 计算样式双维 44 px 断言。
- package/build contract 现在验证测试依赖、`remoteEntry` 与唯一 expose chunk 的对应关系、关键编译分支、44 px CSS 及前端测试和 expose chunk 被 Git 跟踪。

### GREEN

```text
npm test
2 test files passed, 10 tests passed

npm run build
vite v5.4.21; 14 modules transformed; build succeeded

.venv/bin/pytest -o addopts='' -q tests/plugin/test_plugin.py tests/plugin/test_package.py
66 passed

.venv/bin/pytest -o addopts='' -q
396 passed

.venv/bin/pytest -o addopts='' -q --cov=xhsmovieassistant --cov-fail-under=80
396 passed; coverage 89.05%

.venv/bin/python -m compileall -q plugins.v2/xhsmovieassistant
passed
```

对 `plugins.v2/xhsmovieassistant/dist` 和 `src` 的凭据模式扫描未发现匹配项。未执行真实 XHS、Chromium、MoviePilot、LLM、二维码或订阅链路，符合本任务限制。
