# Auto Video Factory — V3

Mobile-first web app that turns one Vietnamese story topic into a narrated vertical short video.

V3 keeps the V2 generation pipeline and adds a phone-friendly web flow:

- enter one topic in the browser;
- choose an estimated 45 / 60 / 90 second length;
- choose narration voice and visual style;
- create a background job;
- watch progress from the phone;
- preview and download the finished MP4;
- API credentials remain server-side only.

The server still defaults to `offline`, so starting the web app never spends API credits by accident.

> **Private-use V3:** the web API does not include user authentication yet. Do not expose an `openai` deployment to the public internet until an access-control/rate-limit layer is added.

## Requirements

- Python 3.10+
- FFmpeg + ffprobe
- eSpeak only for offline/demo mode

## Install

```bash
python -m pip install -e ".[dev]"
```

## Run the phone web UI — offline demo

```bash
export AVF_PROVIDER=offline
auto-video-factory-web
```

Then open `http://SERVER_IP:8000` from the phone/browser.

Environment controls:

```text
AVF_PROVIDER=offline|openai
AVF_HOST=0.0.0.0
AVF_PORT=8000
AVF_OUTPUT_ROOT=output/web
AVF_MAX_WORKERS=1
```

## Run with AI providers

Keep the provider key on the server. The browser never receives or submits it.

```bash
export OPENAI_API_KEY="YOUR_SERVER_SIDE_KEY"
export AVF_PROVIDER=openai
auto-video-factory-web
```

Optional model overrides:

```text
AVF_SCRIPT_MODEL=gpt-5-mini
AVF_IMAGE_MODEL=gpt-image-1-mini
AVF_IMAGE_QUALITY=low
AVF_TTS_MODEL=gpt-4o-mini-tts
```

## Web flow

```text
Phone browser
  -> POST /api/jobs
  -> returns job_id immediately
  -> GET /api/jobs/{job_id} for progress
  -> GET /api/jobs/{job_id}/video when complete
```

The checked-in `docs/openapi-v3.json` is the versioned HTTP contract used by the tests.

### Request

```json
{
  "topic": "Một kiếm tu bị tông môn ruồng bỏ nhưng thức tỉnh sức mạnh cổ xưa",
  "duration_seconds": 60,
  "voice": "marin",
  "style": "xianxia-cinematic"
}
```

Supported duration presets map to scene counts:

- 45 seconds -> 6 scenes
- 60 seconds -> 8 scenes
- 90 seconds -> 12 scenes

These are story-length estimates, not an exact final media duration guarantee.

## Visual styles

- `xianxia-cinematic` — cinematic cold xianxia-inspired fantasy
- `ink-wash` — original ink-wash inspired fantasy
- `dark-fantasy` — original moonlit dark fantasy, non-graphic

Prompts require original characters/designs and explicitly avoid copying named characters, franchises, or a named living artist.

## CLI remains available

Offline:

```bash
python -m auto_video_factory \
  --topic "Một kiếm tu bị tông môn ruồng bỏ nhưng thức tỉnh kiếm ý" \
  --output output/offline-demo
```

AI provider:

```bash
export OPENAI_API_KEY="YOUR_SERVER_SIDE_KEY"
python -m auto_video_factory \
  --provider openai \
  --topic "Một kiếm tu bị tông môn ruồng bỏ nhưng thức tỉnh kiếm ý" \
  --scenes 8 \
  --width 1080 --height 1920 --fps 30 \
  --output output/ai-demo
```

## Failure behavior

- missing AI key fails server-side before provider calls;
- browser responses never contain the key;
- failed web jobs expose a generic `GENERATION_FAILED` error instead of raw provider exceptions;
- 401/403 provider auth failures are not retried;
- 408/409/429 and 5xx/network provider failures use bounded retries;
- video download is blocked until the job is completed;
- unknown jobs return 404 and unfinished videos return 409.

## Test

```bash
PYTHONPATH=. pytest -q
PYTHONPATH=. pytest --cov=auto_video_factory --cov-report=term-missing
```

The suite covers the mobile page, web API contract, safe errors, job lifecycle, download flow, duration mapping, visual-style wiring, V2 provider contracts, and a real offline render path.

## V3 boundary

Included: mobile web UI, async in-process job queue, progress polling, preview/download, safe server-side provider configuration, duration presets, voice/style selection, versioned OpenAPI contract.

Not included yet: persistent database/queue across server restarts, multi-user accounts, batch generation, scheduled publishing, automatic TikTok/YouTube posting, or reference-image identity locking.

## V3.1 private deploy

V3.1 adds a private access-code gate and a container target for phone-first deployment.
The HTML shell and `/health` stay public, while config, generation jobs, job status,
and video bytes require a short-lived bearer session whenever `AVF_ACCESS_CODE` is configured.
AI mode refuses to start without an access code of at least 12 characters.

Server secrets/config:

```bash
AVF_PROVIDER=openai
OPENAI_API_KEY=<server secret>
AVF_ACCESS_CODE=<private code, at least 12 characters>
AVF_MAX_WORKERS=1
AVF_MAX_JOBS_PER_HOUR=12
```

Build/run with any Docker-compatible host:

```bash
docker build -t auto-video-factory:0.3.1 .
docker run --rm -p 8000:8000 \
  --env-file deploy.env \
  auto-video-factory:0.3.1
```

Then open the HTTPS URL from a phone, enter the private access code, create a video,
preview it, and download the MP4. The browser stores only the opaque session token in
`sessionStorage`; the raw access code is not persisted.

The container includes `ffmpeg`, `ffprobe`, `espeak`, and DejaVu fonts and runs as a
non-root user. Most cloud hosts provide `PORT`; V3.1 accepts that automatically.

> Deployment note: generated files live under `AVF_OUTPUT_ROOT` and are ephemeral unless
> the host mounts persistent storage. For a personal single-user deployment this is fine
> as long as the video is downloaded before the container is recycled. A later queue/storage
> version should move artifacts to object storage before multi-user or long-lived public use.

## GitHub Actions Renderer (V3.2)

The repository includes a manual **Auto Video Factory** workflow for phone-first use:

1. Add `OPENAI_API_KEY` as a repository Actions secret if using the `openai` provider (optional for `offline` mode).
2. Open **Actions → Auto Video Factory → Run workflow**.
3. Enter the topic (`topic`) and choose `duration_seconds` (45 / 60 / 90), `voice` (marin / onyx), `style` (xianxia-cinematic / ink-wash / dark-fantasy), and `provider` (openai / offline).
4. When the run finishes, download `auto-video-output` from **Artifacts**. Artifacts are retained for 1 day.

Use `provider=offline` for a zero-cost smoke render test without requiring API keys. The workflow never accepts an API key as a user input and uses step-scoped secrets with read-only repository permissions.
