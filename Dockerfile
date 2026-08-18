FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AVF_HOST=0.0.0.0 \
    AVF_OUTPUT_ROOT=/data/output

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        espeak \
        ffmpeg \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY pyproject.toml README.md ./
COPY auto_video_factory ./auto_video_factory
RUN python -m pip install --no-cache-dir . \
    && mkdir -p /data/output \
    && chown -R appuser:appuser /app /data

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request; p=os.getenv('AVF_PORT') or os.getenv('PORT') or '8000'; urllib.request.urlopen(f'http://127.0.0.1:{p}/health', timeout=3)" || exit 1

CMD ["auto-video-factory-web"]
