FROM python:3.12-slim

WORKDIR /app

COPY services/cortex-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared libs — available as `from transport_utils import ...`, `from model_id import ...`
COPY libs/ /app/libs/
ENV PYTHONPATH=/app/libs

COPY services/cortex-api/src/ src/
COPY services/cortex-api/migrations/ migrations/

CMD ["uvicorn", "src.main:app", "--uds", "/tmp/universal-protocol/cortex-api.sock"]
