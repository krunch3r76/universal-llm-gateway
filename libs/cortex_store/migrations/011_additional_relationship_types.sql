-- Formalize relationship types that exist as data but not in migrations.
-- filed_by: tax document filed by organization
-- assessed_on: property tax assessed on property/parcel
-- pertains_to: statement pertains to account
-- payment_on: escrow payment on mortgage account
-- references: generic cross-reference between entities
INSERT OR IGNORE INTO relationship_types (type, description) VALUES ('filed_by', 'Tax document filed by organization');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES ('assessed_on', 'Property tax assessed on property');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES ('pertains_to', 'Statement pertains to account');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES ('payment_on', 'Escrow payment on mortgage account');
INSERT OR IGNORE INTO relationship_types (type, description) VALUES ('references', 'Generic cross-reference between entities');
