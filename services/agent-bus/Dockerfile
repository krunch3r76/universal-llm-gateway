FROM python:3.12-slim

WORKDIR /app

COPY services/agent-bus/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/agent-bus/src/ src/

CMD ["uvicorn", "src.main:app", "--uds", "/tmp/universal-protocol/agent-bus.sock"]
