-- Migration 028: Add urgency/outcome to matters_with_deadlines view
-- Applied: 2026-04-10
-- Description: Expose deadline entity attributes (urgency, outcome) so the
-- REST endpoint can filter out resolved deadlines from boot briefings.

DROP VIEW IF EXISTS matters_with_deadlines;

CREATE VIEW matters_with_deadlines AS
SELECT
    m.id AS matter_id,
    m.name AS matter_name,
    d.name AS deadline_name,
    json_extract(d.attributes, '$.date') AS deadline_date,
    json_extract(d.attributes, '$.description') AS deadline_description,
    json_extract(d.attributes, '$.urgency') AS urgency,
    json_extract(d.attributes, '$.outcome') AS outcome
FROM entities m
JOIN relationships r ON r.to_entity = m.id AND r.type = 'deadline_for'
JOIN entities d ON r.from_entity = d.id
WHERE m.type = 'legal_matter'
ORDER BY deadline_date;
