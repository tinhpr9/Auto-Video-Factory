# TDD Evidence — MoneyPrinterTurbo Integration

**Branch:** feat/moneyprinterturbo-core  
**Repo:** tinhpr9/Auto-Video-Factory  
**Date:** 2026-08-18  

## Upstream Pin

```
MONEYPRINTERTURBO_REPO = https://github.com/harry0703/MoneyPrinterTurbo
MONEYPRINTERTURBO_REF  = b42e945b497176c823579f9b1895d9323446de23
Upstream commit date   = 2026-08-17T10:56:41Z
Upstream author        = harry0703
```

## Integration Strategy

**OPTION A — Orchestration/Adapter (selected)**

- Auto-Video-Factory remains a thin adapter; does NOT vendor MPT source
- GitHub Actions clones MPT at pinned SHA → applies config → runs `cli.py`
- Update path: bump `MONEYPRINTERTURBO_REF` in `mpt_adapter.py` + test + PR

## Required Secrets

| Secret | Purpose | Free tier |
|--------|---------|-----------|
| `PEXELS_API_KEY` | Stock video materials | ✅ free at pexels.com/api |
| `LLM_API_KEY` | Script generation (OpenAI-compatible) | ❌ paid |

## Optional Secrets

| Secret | Purpose |
|--------|---------|
| `PIXABAY_API_KEY` | Fallback video source |
| `LLM_BASE_URL` | Custom OpenAI-compatible endpoint |

## TDD Cycle

### RED (before implementation)
- All 43 tests collected, 42 FAIL (mpt_adapter not yet imported)

### GREEN (after implementation)
- All 43 tests PASS

### Tests Coverage

| Class | Tests |
|-------|-------|
| TestUpstreamPin | 4 |
| TestDurationMapping | 6 |
| TestVoiceMapping | 4 |
| TestBuildCliArgs | 11 |
| TestBuildConfigToml | 6 |
| TestLocateOutputVideo | 3 |
| TestSanitizeResultMetadata | 3 |
| TestWorkflowFile | 12 |

## Adversarial Checks

- [x] Upstream SHA pinned — not floating `main`
- [x] MPT clone uses `git checkout <SHA>`, not `git clone --branch main`
- [x] Topic passed via env var → Python list → subprocess(shell=False)
- [x] No shell interpolation of user topic input
- [x] No API keys in CLI args
- [x] Secrets only in config.toml (ephemeral, not in artifact)
- [x] `test -s video.mp4` before upload
- [x] retention-days: 1
- [x] timeout-minutes: 45
- [x] permissions: contents: read
- [x] Old V3.2 preserved as render-video-v32-fallback.yml
- [x] No hardcoded tokens in any file
