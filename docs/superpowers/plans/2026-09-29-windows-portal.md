# Windows Portal Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan inline.

**Goal:** Deliver a complete native Windows package for the existing website frontend and backend.

**Architecture:** The existing FastAPI process serves both the static frontend and API. Thin CMD wrappers invoke Python installation and runtime modules. JSON launch configuration, credentials and data live outside the application directory; no ATS execution changes.

**Tech Stack:** Python 3.10+, FastAPI, Uvicorn, SQLite, Windows CMD, tzdata.

**Spec:** The user's latest request: deploy only the website frontend/backend on Windows. Preserve existing portal behavior and Ubuntu support.

## Global Constraints

- No hardcoded stress-test modes; do not modify the ATS runner/uploader.
- Default local URL is http://127.0.0.1:8080; host/port remain configurable.
- Do not overwrite existing config, token or archived data during installation.
- No dependency on Bash, Node.js, Docker or PowerShell execution-policy changes.
- Be explicit that this Linux workspace cannot provide native Windows execution evidence.

## Review Focus

- Chinese and space-containing install/data paths must work.
- Repeated installation must retain credentials and archived bytes.
- Invalid JSON, missing credentials and invalid ports must fail clearly.
- Windows reserved names and case-insensitive collisions must not corrupt logs.
- Timezone must resolve without a system IANA database.

### Task 1: Windows runtime and storage compatibility

**Files:** config.py, launcher.py, storage.py, repository.py, service.py, requirements.txt, tests/test_deployment.py.

**Interfaces:** launcher.initialize(config_file, data_dir) creates a JSON configuration without replacing existing data; launcher.load_config(config_file, environ) returns validated runtime settings; launcher.main(argv) supports init/check/start. Settings.from_env accepts an optional environment mapping. The existing create_app(settings) interface is unchanged.

- [x] Write failing tests for idempotent initialization, environment overrides, Windows directory defaults, bad settings, missing tokens, tzdata-only operation, full upload/read/restart, reserved filenames and case collisions.
- [x] Run focused tests and confirm failure due to missing behavior.
- [x] Implement the configuration/launcher and Windows storage guards.
- [x] Run focused tests and existing Python suite; expect all passing.
- [x] Commit the tested runtime changes.

### Task 2: Windows entry points and complete package

**Files:** install.cmd, start.cmd, scripts/install_windows.py, README-Windows.md, README.md, docs/validation.md.

**Interfaces:** installer creates .venv/Scripts/python.exe, installs requirements and invokes launcher init/check; start.cmd invokes launcher start using that interpreter.

- [x] Add quoted CMD wrappers and a standard-library installer with checked subprocess failures.
- [x] Document fresh install, start/stop/restart, persistent data, port/LAN configuration, backups and optional Ubuntu data transfer.
- [x] Run actual HTTP launch/receive/read/restart smoke test and the existing browser core journey.
- [x] Review the whole change with a fresh reviewer and fix material findings.
- [ ] Build a uniquely named complete ZIP without credentials, caches or a virtual environment; validate ZIP contents and save it.

## Execution record

- Baseline: existing Python suite run before changes.
- Execution is authorized by the user; proceed without another approval gate. Work in this task's isolated scratch checkout on feature/windows-portal.
- Task 1: complete in bbe1d43; 26 new tests observed failing before implementation, then 26 passing. Added a true HTTP launch/restart acceptance test; full suite 60 passing.
- Task 2: Windows CPython 3.12 wheels downloaded successfully. Browser journey through the new launcher passed. Native Windows execution is unavailable and explicitly documented.
- Final review: Windows short-name alias risk fixed by rejecting '~' in Windows artifact names. Invalid host wildcards regraded Important for the configuration check contract and fixed. Both defects reproduced with failing tests before fixes; no deferred minor findings.
- Reviewer limitations: native CMD/ACL/firewall execution is unavailable; explicitly disclosed. ZIP completeness will be verified during packaging. ATS runner/uploader remains out of scope per user request.
