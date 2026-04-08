-- Migration 005: Add content_hash column to entities for file integrity tracking

ALTER TABLE entities ADD COLUMN content_hash TEXT;
