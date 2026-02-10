# Makefile for universal-llm-gateway
# Simple monorepo - standard git workflow

# Ensure we use bash
SHELL := /usr/bin/env bash

.PHONY: help verify-uml status

help:
	@echo "Available targets:"
	@echo "  verify-uml    - Verify PlantUML SVG files for syntax errors"
	@echo "  status        - Show git status"
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
