-- Add 'issued_by' relationship type for financial account → issuer org relationships.
INSERT OR IGNORE INTO relationship_types (type, description) VALUES ('issued_by', 'Account issued by organization');
