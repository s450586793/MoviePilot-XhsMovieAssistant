# Final blocker fix report

Fix base: `6eef2696c700e6d2bd8d2abb0c80e8d815720d1b`.

No live XHS/RedNote, Chromium, MoviePilot business chain, LLM, enterprise
WeChat, subscription, notification, QR scan, or public reply operation was
performed. All changed behavior was exercised with fake pages, fake browser
managers, fake MoviePilot objects, or cached/durable state.

## Finding 1: separate login and risk pages

Changed `browser.py` to expose a visible-DOM login classifier and to remove the
generic Chinese `验证码` risk marker while retaining explicit human/security
verification and structured `300012` handling. `xhs.py` now classifies risk
first and visible login second after navigation/reload and before reply submit.

RED:

```text
.venv/bin/pytest -o addopts='' -q tests/plugin/test_browser.py::test_capture_login_qrcode_allows_ordinary_sms_verification_copy tests/plugin/test_browser.py::test_ordinary_login_sms_copy_is_login_required_not_risk_control tests/plugin/test_xhs_gateway.py::test_fetch_mentions_uses_real_visible_login_classification tests/plugin/test_xhs_gateway.py::test_fetch_note_uses_real_visible_login_classification tests/plugin/test_xhs_gateway.py::test_reply_uses_real_visible_login_classification_without_submitting
5 failed in 1.44s
```

GREEN: the same command passed `5 passed in 0.91s`. The complete affected
modules passed:

```text
.venv/bin/pytest -o addopts='' -q tests/plugin/test_browser.py tests/plugin/test_xhs_gateway.py
140 passed in 1.15s
```

## Finding 2: reject untrusted candidate types

Changed `moviepilot.py` so a candidate type must normalize to the exact
requested type. Missing, empty, whitespace, unknown, and unsupported types are
rejected; the native MoviePilot enum `to_agent()` path remains accepted.

RED: the new candidate matrix produced `3 failed, 2 passed`; missing, empty,
and whitespace-only types incorrectly matched with score `0.90`.

GREEN: the candidate matrix plus the native-enum positive case passed
`6 passed in 0.57s`.

## Finding 3: reject movie seasons across boundaries

Changed `models.py` to apply one movie/season invariant to both `Resolution`
and `MediaMatch`. Changed `moviepilot.py` to return `FAILED` before loading the
MoviePilot runtime when a bypass-constructed movie match contains a season.

RED: the two model regressions and submit-boundary regression produced
`3 failed in 0.70s`. Models accepted the invalid shape and submit loaded the
runtime.

The first combined GREEN run exposed that `MatchDecision` revalidates its
nested model (`43 passed, 1 failed`), so the boundary fixture was narrowed to
use Pydantic V2 `model_construct()` at both bypassed layers. Final GREEN:

```text
.venv/bin/pytest -o addopts='' -q tests/plugin/test_models.py tests/plugin/test_moviepilot_gateway.py
44 passed in 0.69s
```

## Finding 4: preserve Chromium install outcome

Changed `__init__.py` to keep `chromium` and `chromium_code` independently of
the durable `browser` and `pause_code` fields. Install failure remains visible
beside durable `READY`; later success sets `AVAILABLE` and clears the old code;
durable `PAUSED` continues to override browser-session readiness without
erasing Chromium state. `/state` only reads the cache and repository. Static
status text and the Vue page display the independent value and stable code.

Backend RED produced `4 failed`: the state payload had no independent fields.
Frontend setup initially reported `vitest: not found`; after restoring the
existing lockfile with `npm ci`, the actual component RED was `1 failed,
8 passed`, because Chromium was not rendered.

GREEN:

```text
focused plugin state tests: 4 passed in 0.78s
npm test --prefix plugins.v2/xhsmovieassistant -- src/components/Page.spec.js
9 passed
```

## Deferred minors

`Page.vue` now marks feedback provenance. A successful refresh clears only a
stale state-load error and preserves action feedback. RED was `1 failed,
9 passed`; GREEN was `10 passed`.

A fresh Vite build reproducibly emitted trailing whitespace in
`dist/assets/remoteEntry.js` (`git diff --check` exit 2). `package.json` now
runs `normalize-dist.mjs` after Vite, and the package contract asserts both the
build command and whitespace-free delivery assets. A repeated fresh build
completed with stable asset names, no trailing-whitespace search matches, and
`git diff --check` exit 0.

## Changed files

- `plugins.v2/xhsmovieassistant/browser.py`: separate visible login from risk.
- `plugins.v2/xhsmovieassistant/xhs.py`: compose risk/login page outcomes.
- `plugins.v2/xhsmovieassistant/models.py`: enforce movie/season invariants.
- `plugins.v2/xhsmovieassistant/moviepilot.py`: strict candidate type and submit guard.
- `plugins.v2/xhsmovieassistant/__init__.py`: independent Chromium cache/state.
- `plugins.v2/xhsmovieassistant/src/components/Page.vue`: Chromium row and feedback provenance.
- `plugins.v2/xhsmovieassistant/src/components/Page.spec.js`: component regressions.
- `plugins.v2/xhsmovieassistant/package.json`: deterministic post-build normalization.
- `plugins.v2/xhsmovieassistant/normalize-dist.mjs`: strip generated trailing whitespace.
- `plugins.v2/xhsmovieassistant/dist/assets/*`: rebuilt tracked Vue federation delivery.
- `tests/plugin/test_browser.py`: login/risk and QR regressions.
- `tests/plugin/test_xhs_gateway.py`: real-classifier composition regressions.
- `tests/plugin/test_models.py`: movie/TV season contract tests.
- `tests/plugin/test_moviepilot_gateway.py`: candidate and submit-boundary tests.
- `tests/plugin/test_plugin.py`: Chromium state, coexistence, and no-work tests.
- `tests/plugin/test_package.py`: deterministic build/package contract.
- This report records the final evidence.

## Final verification

```text
.venv/bin/pytest -o addopts='' -q tests/plugin/test_browser.py tests/plugin/test_xhs_gateway.py tests/plugin/test_models.py tests/plugin/test_moviepilot_gateway.py tests/plugin/test_plugin.py tests/plugin/test_package.py
256 passed in 2.37s

.venv/bin/pytest -o addopts='' -q
417 passed in 6.28s

.venv/bin/pytest -o addopts='' -q --cov=xhs_probe --cov=xhsmovieassistant --cov-report=term-missing --cov-fail-under=80
417 passed; total coverage 89.09%

npm test --prefix plugins.v2/xhsmovieassistant
12 passed

npm run build --prefix plugins.v2/xhsmovieassistant
vite build && node normalize-dist.mjs; build succeeded

.venv/bin/pytest -o addopts='' -q tests/plugin/test_package.py
10 passed in 0.80s

.venv/bin/python -m compileall -q src plugins.v2 tests
exit 0

git diff --check
exit 0
```

The generated-asset secret scan found no API-key, `xsec_token`, authorization,
cookie, or bearer-secret pattern. Package tests also verify the source/bundle
secret and host-API contracts.

The bounded compatibility smoke used the existing clean MoviePilot checkout at
tag `v2.15.6`, its existing dependency environment, `prepare_v2_backend()`, and
a guard that rejected non-loopback DNS/socket access. It reported
`SMOKE_PREFIX_OK routes=12 attempts=[]`: the real `_PluginBase` import, Vue
render mode, all authenticated route shapes, JSON-serializable form, and safe
defaults passed. `SubscribeChain` import also resolved in that pre-existing
environment, but no chain was instantiated or called; runtime-chain behavior,
deployment, and live-host acceptance are not claimed.

## Self-review and residual concerns

The whole branch was reviewed against the binding design and blocker brief.
Authorization, secret redaction, dry-run, idempotency, cancellation,
reply-category, atomic outbox, durable pause, and POST-only write-route behavior
remain covered by the `417`-test suite. No new external call exists in page or
state rendering, and the submit guard executes before runtime loading.

`npm ci` reported five audit findings in the existing locked development
dependency tree (three moderate, one high, one critical). The lockfile was not
changed and no force upgrade was attempted in this narrow fix. Live runtime
chain, QR login, deployment, and external-service UAT remain outside this
verification boundary.

The pre-existing untracked `src/xhs_mp_bridge.egg-info/` directory was not read,
edited, removed, staged, or committed.
