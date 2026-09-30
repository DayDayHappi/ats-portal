# ATS Portal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Ship a locally deployable Ubuntu ATS result archive with dynamic modes, issue classification and original log reading.
**Architecture:** Separate data contracts, ATS adapter, classifier, repository, file storage and ingestion service behind a versioned HTTP API. Independent standard-library sender with durable outbox.
**Tech Stack:** Python 3.10+, FastAPI, Uvicorn, SQLite, vanilla browser UI.
**Spec:** docs/design.md

## Global Constraints
- Local-only Ubuntu delivery; no cloud registration/deployment.
- Dynamic mode identifiers; no scenario enum or per-scenario UI branches.
- Original test status preserved; anomalies independent.
- Preserve raw file bytes; persistent data separate from source.
- Default loopback binding; authenticated machine upload.

## Review Focus
- New/renamed modes and unknown or missing report fields must not silently hide data.
- Interrupted/concurrent duplicate uploads must remain retryable and not corrupt committed runs.
- PASS with error strings and recovered runs still surface anomalies.
- Giant lines and hostile log text must not freeze the UI or execute markup.
- Sender retries, missing reports and abnormal exits must preserve test outcome.

### Task 1: Ingestion, persistence and classification
**Files:** ats_portal/{config,models,adapter,classifier,repository,storage,service,web}.py, config/rules.json, tests/test_portal.py
**Interfaces:** create_app(Settings)->FastAPI; manifest-v1 HTTP API from spec; Repository stores normalized runs; LocalStorage stores verified original bytes.
- [x] Write integration tests for dynamic modes, PASS anomalies, duplicate/conflicting uploads, missing/corrupt files, date attribution, persistence and raw bytes.
- [x] Run pytest; expected missing implementation failures.
- [x] Implement modules and API, including bounded log reading/search and auth.
- [x] Run full pytest; expected all tests pass; commit.

### Task 2: Browser workbench
**Files:** ats_portal/static/{index.html,app.js,style.css,favicon.svg}, tests/ui-check.mjs
**Interfaces:** consume /api/v1/options, /runs, /runs/{id}, /artifacts/.../lines and downloads; URL hash detail navigation.
- [x] Add browser acceptance checks for filtering new modes, issue-only view, details, logs and mobile overflow; initially unavailable app fails.
- [x] Implement sidebar, run table, filters, metrics and detail log workspace, with honest empty/error/loading states.
- [x] Run browser checks and inspect rendered screenshots; expected core journey usable and no overflow; commit.

### Task 3: Sender and Ubuntu delivery
**Files:** tools/ats_upload.py, scripts/{install,start,service}.sh, README.md, docs/{api,extension,validation}.md, tests/test_sender.py
**Interfaces:** CLI send/retry/wrap; standard-library HTTP protocol consumes Task 1 contract. Install creates venv/config/data dirs; start invokes uvicorn.
- [x] Write sender tests for durable retry, stable run ids and process exit preservation.
- [x] Run tests; expected missing implementation failures.
- [x] Implement sender, deployment scripts and documentation.
- [x] Run complete test suite and live HTTP sender integration; commit.
- [x] Fresh-context code review, fix material issues, package and save ZIP.
