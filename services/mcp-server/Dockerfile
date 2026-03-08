FROM python:3.12-slim

WORKDIR /app

# Install dependencies in a separate layer for cache efficiency
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY server.py .
COPY tools/ tools/

# Non-root user — container boundary is the real security primitive,
# but defense-in-depth: process runs without root privileges
RUN useradd -r -u 1000 -s /sbin/nologin mcp
USER mcp

EXPOSE 443

CMD ["python", "server.py"]
