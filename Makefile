# Makefile for universal-llm-gateway
# Simple monorepo - standard git workflow

# Ensure we use bash
SHELL := /usr/bin/env bash

.PHONY: help verify-uml status skill-graph-check skill-graph-reconcile claude-bundles

help:
	@echo "Available targets:"
	@echo "  verify-uml    - Verify PlantUML SVG files for syntax errors"
	@echo "  status        - Show git status"
	@echo "  skill-graph-check     - Read-only skill graph drift check (JSON report)"
	@echo "  skill-graph-reconcile - Explicit add+prune skill graph reconcile"
	@echo ""
	@echo "Git Workflow:"
	@echo "  This is a simple monorepo. Use standard git commands:"
	@echo "    git add ."
	@echo "    git commit -m \"message\""
	@echo "    git push"

status:
	@echo "=== Git Status ==="
	@git status

# Verify PlantUML SVG files for syntax errors
verify-uml:
	@echo "Verifying PlantUML SVG files..."
	@if [ -n "$(UML_DIR)" ]; then \
		echo ">>> verify-plantuml-svg.sh $(UML_DIR)"; \
		verify-plantuml-svg.sh "$(UML_DIR)"; \
	else \
		echo ">>> verify-plantuml-svg.sh ."; \
		verify-plantuml-svg.sh .; \
	fi

skill-graph-check:
	python scripts/cortex/gen_skill_stubs.py --check

skill-graph-reconcile:
	python scripts/cortex/ingest_skills.py && python scripts/cortex/gen_skill_stubs.py --generate && python scripts/rag/attribute_skill_vocabulary.py

claude-bundles:
	python scripts/cortex/gen_claude_bundles.py
