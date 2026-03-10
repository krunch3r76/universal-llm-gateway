FROM python:3.12-slim

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# git is needed for `git ls-files` in the project directory tools
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /ms-playwright /data/databases

# Install dependencies in a separate layer for cache efficiency
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright system dependencies (requires root)
RUN playwright install-deps firefox

# Non-root user — container boundary is the real security primitive,
# but defense-in-depth: process runs without root privileges
RUN useradd -r -u 1000 -m -d /home/mcp -s /sbin/nologin mcp
RUN chown -R mcp:mcp /app /ms-playwright /data/databases /home/mcp

ENV HOME=/home/mcp
USER mcp

# Browser binaries as non-root (heavy; cached here — above code COPY so code
# changes do not invalidate this layer)
RUN playwright install firefox

# Optional: pass --build-arg SOURCE_VERSION=$(date +%s) to force cache bust
ARG SOURCE_VERSION=0
# Use ARG so changing SOURCE_VERSION invalidates this and all following layers
RUN echo "$SOURCE_VERSION" > /tmp/source_version

# Application code last — only this layer rebuilds on code changes
COPY --chown=mcp:mcp server.py .
COPY --chown=mcp:mcp mcp_events.py .
COPY --chown=mcp:mcp tools/ tools/

EXPOSE 443

CMD ["python", "server.py"]
