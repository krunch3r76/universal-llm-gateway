-- 082: friction triage vocabulary (a:39610). same_class_as is symmetric; duplicates points from the duplicate to the original.
INSERT OR IGNORE INTO session_edge_types (type, description, directional) VALUES
    ('same_class_as', 'Both nodes are instances of the same defect or phenomenon class', FALSE),
    ('duplicates',    'from_node is a duplicate report of to_node',                    TRUE);
