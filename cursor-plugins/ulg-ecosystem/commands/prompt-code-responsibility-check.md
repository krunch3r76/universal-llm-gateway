Analyze **only** code explicitly referenced (¬repo-wide search unless asked).

## Goal
Identify: SRP violations → split | Natural language → rename

Breaking changes allowed if simplifies design.

## Input
`@path.py` | `@path.py:Lx-Ly` | pasted code

## Output Format

### 1. Scope
Exact artifacts reviewed

### 2. SRP Findings
Per issue:
- **Location**: file + function
- **Responsibilities**: bulleted list
- **Impact**: 1 sentence
- **Split**: names + boundaries
- **Files**: create/modify/delete

### 3. Natural Language
| Type | Old | New |
|------|-----|-----|
| Function | verb phrase | `fetch_user_config()` |
| Variable | noun phrase | `validated_result` |
| Docstring | Inputs/Outputs contract |

### 4. Breaking Changes
Removed/changed functions | Migration snippets

### 5. Refactor Plan
Steps + targets | New files ≤300 SLOC

### 6. Verification
- [ ] Event-driven: ∃! update path
- [ ] Non-blocking: ¬blocking I/O, ¬`await gather()`
- [ ] SRP: <3 responsibilities, handlers ≤80 SLOC
- [ ] Compile/lint: `python -m compileall`, `ruff check`

## SRP Triggers
| Condition | Action |
|-----------|--------|
| ≥3 responsibilities | MUST split |
| Handler >80 SLOC | Split to helpers |
| ≥3 independent `if` | Extract per-concern |
| ≥3 domain responsibilities | Directory split |
| Domain + utils mixed | Extract to `utils/` |

## FOL
Include ≥1 invariant: `∀ id, id ∈ ACTIVE ⟺ stream_active(id)`
