FROM python:3.12-slim

WORKDIR /app

COPY services/journal-ingest/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared libs — available as `from transport_utils import ...`, `from model_id import ...`
COPY libs/ /app/libs/
ENV PYTHONPATH=/app/libs

COPY services/journal-ingest/ingest.py .

CMD ["python", "ingest.py"]
