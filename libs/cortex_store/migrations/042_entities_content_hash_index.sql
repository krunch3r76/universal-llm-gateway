-- Migration 042: Add index on entities(content_hash) for efficient lookup.
--
-- Required by the content_hash filter on GET /entities and the
-- find_document_with_content_hash duplicate-gate in mcp-server.
-- Without this index, content_hash queries full-scan entities.
-- Idempotent via IF EXISTS.
CREATE INDEX IF NOT EXISTS idx_entities_content_hash ON entities(content_hash);
