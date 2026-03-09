FROM python:3.12-slim

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# git is needed for `git ls-files` in the project directory tools
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /ms-playwright
RUN mkdir -p /data/databases

# Install dependencies in a separate layer for cache efficiency
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright system dependencies (requires root)
RUN playwright install-deps firefox

# Copy application code
COPY server.py .
COPY mcp_events.py .
COPY tools/ tools/

# Non-root user — container boundary is the real security primitive,
# but defense-in-depth: process runs without root privileges
RUN useradd -r -u 1000 -s /sbin/nologin mcp
RUN chown -R mcp:mcp /app /ms-playwright /data/databases

USER mcp

# Browser binaries (as non-root)
RUN playwright install firefox

EXPOSE 443

CMD ["python", "server.py"]
