-- 027_journal_transcript_entity.sql
-- Auto-create transcript entity on journal_write and link continues edges.
-- Adds session_id (explicit session identifier) and prior_session_id (links to
-- the preceding session for automatic continues-edge creation) to session_journals.
-- Note: 'continues' edge type was added in migration 026.

ALTER TABLE session_journals ADD COLUMN session_id TEXT;
ALTER TABLE session_journals ADD COLUMN prior_session_id TEXT;
