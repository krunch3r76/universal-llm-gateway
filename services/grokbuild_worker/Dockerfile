# grokbuild-worker container fallback.
#
# Deploy preference is bare-metal systemd; this image mirrors the FastAPI
# contract so callers cannot tell which shape is running. Bind-mount host
# paths at identical locations so config defaults stay unchanged across
# shapes (see services/grokbuild_worker/README.md).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/libs

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY services/grokbuild_worker/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY libs/ /app/libs/
COPY services/grokbuild_worker /app/services/grokbuild_worker

EXPOSE 8090

CMD ["python", "-m", "services.grokbuild_worker.main"]
