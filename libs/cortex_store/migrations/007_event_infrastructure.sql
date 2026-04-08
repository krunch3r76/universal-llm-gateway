-- Migration 007: Event infrastructure — relation types, salience cache, event chains

-- 8 event-to-event relation types (INSERT OR IGNORE for idempotency)
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('precedes', 'A happened before B');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('causes', 'A directly caused B');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('enables', 'A is a precondition for B');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('contradicts', 'A and B are in tension');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('elaborates', 'B provides detail on A');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('co_occurs', 'A and B happened simultaneously');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('supersedes', 'B replaces A');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('responds_to', 'B is a response to A');

-- 6 entity-to-event participation types
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('participant', 'Entity is involved in event');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('subject_of', 'Entity is the primary actor');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('object_of', 'Entity is affected by event');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('location_of', 'Place where event occurred');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('role_in', 'Entity holds a specific role in event');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES
  ('triggers', 'Entity caused event to occur');

-- Salience scoring cache (EST dual-track state, fingerprint-based invalidation)
CREATE TABLE IF NOT EXISTS entity_salience_cache (
  entity_id TEXT PRIMARY KEY REFERENCES entities(id),
  salience_score REAL NOT NULL DEFAULT 0.0,
  temporal_score REAL,
  structural_score REAL,
  contextual_score REAL,
  frequency_score REAL,
  fast_state_hash TEXT,
  slow_state_hash TEXT,
  last_surprise REAL,
  fingerprint TEXT,
  computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  boot_section_cache TEXT
);

-- Materialized event chain index
CREATE TABLE IF NOT EXISTS event_chains (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chain_name TEXT NOT NULL,
  root_event_id TEXT REFERENCES entities(id),
  domain TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_chain_members (
  chain_id INTEGER REFERENCES event_chains(id),
  event_id TEXT REFERENCES entities(id),
  position INTEGER NOT NULL,
  PRIMARY KEY (chain_id, event_id)
);
