-- Migration 009: Entity access log + seeded_by column
-- v2.2 Phase 2 supplement — captures read-path attention signals for salience scoring.
-- Also adds seeded_by to assertions for agent provenance tracking.
-- No FK on entity_id: access logs are operational telemetry, not referential data.

ALTER TABLE assertions ADD COLUMN seeded_by TEXT DEFAULT NULL;

CREATE TABLE IF NOT EXISTS entity_access_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id TEXT NOT NULL,
  agent TEXT NOT NULL,
  operation TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'agent',
  session_id TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_eal_entity_agent ON entity_access_log(entity_id, agent);
CREATE INDEX IF NOT EXISTS idx_eal_session ON entity_access_log(session_id);
CREATE INDEX IF NOT EXISTS idx_eal_created ON entity_access_log(created_at);

-- Weekly aggregates for long-term retention after raw log compaction (30-day TTL)
CREATE TABLE IF NOT EXISTS entity_access_summary (
  entity_id TEXT NOT NULL,
  agent TEXT NOT NULL,
  week_start TEXT NOT NULL,
  agent_access_count INTEGER DEFAULT 0,
  boot_access_count INTEGER DEFAULT 0,
  session_count INTEGER DEFAULT 0,
  PRIMARY KEY (entity_id, agent, week_start)
);
