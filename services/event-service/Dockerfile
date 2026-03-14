FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    apt-get update && apt-get install -y --no-install-recommends netcat-openbsd && \
    rm -rf /var/lib/apt/lists/*

COPY *.py /app/event_service/

RUN useradd -m -u 1000 appuser && mkdir -p /data && chown appuser:appuser /data
USER appuser

VOLUME /data

CMD ["python", "-m", "event_service"]
