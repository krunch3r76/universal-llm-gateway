"""Detector modules — themed groupings of the gap detectors invoked by
``ops_audit_detectors.run_detectors``.

The parent ``ops_audit_detectors`` module owns the public taxonomy (kind sets,
severity map re-export) and the registry/runner. Each module here owns a
small cohesive set of detectors and uses the shared ``_finding`` builder.
"""

from .agent_skill import detect_agent_skill_related_skills_no_relationship

__all__ = ["detect_agent_skill_related_skills_no_relationship"]
