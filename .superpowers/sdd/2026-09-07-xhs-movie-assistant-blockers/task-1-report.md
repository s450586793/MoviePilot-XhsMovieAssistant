# Task 1 Report: Durable NEW Recovery

## TDD Evidence

### RED

Command:

```text
.venv/bin/pytest -o addopts='' -q tests/plugin/test_service.py -k reprocess_resumes_recovered_new_request_from_durable_snapshot
```

Result before the production change:

```text
FAILED tests/plugin/test_service.py::test_reprocess_resumes_recovered_new_request_from_durable_snapshot
xhsmovieassistant.repository.InvalidTransition: Cannot requeue NEW
1 failed, 40 deselected
```

The regression persists `FETCHED -> RESOLVING`, calls `recover_interrupted()`, creates a restarted service with no mentions, and calls authenticated `reprocess()` directly.

### GREEN

Command:

```text
.venv/bin/pytest -o addopts='' -q tests/plugin/test_service.py -k reprocess_resumes_recovered_new_request_from_durable_snapshot
```

Result after the production change:

```text
1 passed, 40 deselected
```

## Implementation

`AssistantService.reprocess()` now invokes `repository.requeue()` only for the established `_REPROCESSABLE` terminal statuses. An authenticated recovered `NEW` request proceeds directly with its durable `NoteContext` snapshot.

## Verification

```text
.venv/bin/pytest -o addopts='' -q tests/plugin/test_service.py
41 passed

.venv/bin/pytest -o addopts='' -q
384 passed
```

## Self-review

- Authorization remains enforced by `_require_authorized()` before either path.
- Terminal-state requeue behavior remains unchanged for every `_REPROCESSABLE` status.
- The new regression confirms no XHS mention or note fetch, no transient token requirement, one resolver call, MoviePilot matching/submission, and no second `attempt_count` increment.
- No external services or Chromium were used.

## Fix Round 1: Preserve Non-NEW Eligibility

### RED

Command:

```text
.venv/bin/pytest -o addopts='' -q tests/plugin/test_service.py -k reprocess_rejects_resolving_request
```

Result before the production change:

```text
FAILED tests/plugin/test_service.py::test_reprocess_rejects_resolving_request
AssertionError: Regex pattern did not match.
Regex: 'Cannot requeue RESOLVING'
Input: 'Cannot transition RESOLVING to FETCHED'
1 failed, 41 deselected
```

The added regression creates a durable `RESOLVING` request and verifies that authenticated `reprocess()` preserves the established `requeue()` rejection before resolver or MoviePilot work begins.

### GREEN

Command:

```text
.venv/bin/pytest -o addopts='' -q tests/plugin/test_service.py -k 'reprocess_resumes_recovered_new_request_from_durable_snapshot or reprocess_rejects_resolving_request'
```

Result after the production change:

```text
2 passed, 40 deselected
```

### Verification

```text
.venv/bin/pytest -o addopts='' -q tests/plugin/test_service.py
42 passed in 2.77s

.venv/bin/pytest -o addopts='' -q
385 passed in 4.97s
```

### Self-review

- `reprocess()` now skips `repository.requeue()` only for `RequestStatus.NEW`.
- `NEW` still resumes solely from the durable snapshot and preserves its interrupted recovery attempt count.
- All other statuses, including `RESOLVING`, continue through `requeue()` and preserve its prior terminal-state eligibility rules and error messages.
- The new regression confirms no XHS, resolver, or MoviePilot calls occur for a rejected in-progress request.
- No external services or Chromium were used.
