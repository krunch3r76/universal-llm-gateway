"""Standard context sizes and remote catalog sync configuration for measurement jobs.

Holds descending context ladder constants and feature flags for optional background
remote catalog synchronization after local profile writes complete.
"""

# Remote sync configuration (disabled by default)
REMOTE_SYNC_ENABLED = False
REMOTE_SYNC_TIMEOUT_MS = 500

STANDARD_CONTEXTS = [131072, 65536, 32768, 16384, 8192, 4096, 2048, 1024]
