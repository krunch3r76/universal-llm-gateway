# Cursor Rules Templates

This directory contains templates for common development patterns used in this project.

## Available Templates

### phase-prompt-template.md
A comprehensive template for creating multi-phase implementation prompts. Use this when:
- Breaking a large implementation into multiple phases
- Each phase builds on previous phases
- Need to track inter-phase dependencies
- Want consistent phase documentation structure

**Key sections**:
- Cross-Phase Dependencies (what each phase requires/provides)
- Implementation tasks with SLOC estimates
- Verification checklists
- Expected file changes

**Usage**: Copy this template when starting a new multi-phase implementation project and customize for your specific needs.

### life-project-instructions.md
Paste-ready **claude.ai Life project instructions** for ULG users with MCP life surface connected. Use when:
- Standing up a personal life workspace on claude.ai
- Pairing `outbound-voice-spec` + `prose-discipline` on Customize
- Need a de-personalized starting block (User fills in role/relationships — no operator name in public template)

**Cursor counterpart:** `outbound-voice-spec_ulg` rule on life-representation / correspondence liaison seats.

## Adding New Templates

When adding new templates:
1. Use descriptive filenames ending in `-template.md`
2. Include clear "when to use" guidance at the top
3. Reference the template in relevant `.mdc` rule files
4. Update this README with the new template