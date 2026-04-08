-- Journal entity gate: entity_ids stores a JSON array of entity IDs
-- referenced by a session, enabling boot-time injection of current entity state.
ALTER TABLE session_journals ADD COLUMN entity_ids TEXT DEFAULT '[]';
