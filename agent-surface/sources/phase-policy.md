<!-- target:* -->
# Phase Policy

**Multi-phase = complete start-to-finish execution**

## Rules
- NO backward compat between phases
- Each phase can break the project temporarily
- Only final state matters
- Delete/rewrite previous phase code freely

## Verification
- [ ] Previous patterns eliminated if replaced
- [ ] No backward compat (unless justified)
- [ ] Old files deleted if unneeded

## Prompt Structure
1. **Objective**: Phase goals
2. **Scope**: In/out
3. **Breaking Changes**: What can break
4. **Cross-Phase Dependencies**: Previous/next phase interfaces
5. **Tasks**: Concrete changes
6. **Verification**: Completion criteria
<!-- /target:* -->
