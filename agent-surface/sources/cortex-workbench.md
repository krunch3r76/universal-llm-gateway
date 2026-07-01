<!-- target:* -->
# Cortex Workbench

## Architecture Constraint

The Cortex Workbench is a **single-file JSX artifact** deployed in an AI
artifact sandbox. This imposes hard constraints:

- ∀ components, utilities, constants: co-located in the single JSX file
- ¬ local module imports (no `import X from './utils'`)
- ¬ build step, ¬ bundler, ¬ separate CSS files
- React + Tailwind CSS available in sandbox; no other dependencies

**SLOC exception**: the single-file constraint means a general 300/400-line
modularization limit does not apply. As the file grows, maintain clear section
separation via comment headers (`// --- Section Name ---`). If the file exceeds
~600 lines, raise with the user — may need an architectural rethink.

## MCP URL Invariant (CRITICAL)

**Invariant**: ∀ commits: the default MCP URL constant is an empty string.

The MCP server URL is injected at deploy time by the hosting session, or
entered by the user via a setup screen. ¬ hardcode URLs in committed source.

```javascript
// ✅ Correct (committed)
const DEFAULT_MCP_URL = "";

// ❌ Forbidden (committed)
const DEFAULT_MCP_URL = "https://mcp.example.com/mcp";
```

## Iteration Workflow

1. Edit the single JSX artifact file in the repo
2. The hosting web session reads the file from the project at session start
3. The hosting session sets the MCP URL and deploys as a JSX artifact
4. User tests in the artifact panel
5. Issues reported back → next iteration

### Quality Checks (before commit)

- [ ] Default MCP URL is empty string
- [ ] No hardcoded API keys, tokens, or server URLs
- [ ] Component renders without errors (JSX syntax valid)
- [ ] MCP config object passed into API calls is wrapped in `useMemo` (see
      effect-stability note below)
- [ ] All `useCallback` dependencies are correct
- [ ] No `var` declarations (const/let only)
- [ ] Tailwind classes are valid utility names

### Verification

Since this runs in a sandbox (not a local dev server), verification is:
1. **Syntax**: read the file, confirm valid JSX structure
2. **Functional**: the hosting session deploys and confirms rendering
3. **No automated test** — the artifact sandbox is the test environment

## React Patterns

| Pattern | Convention |
|---|---|
| State | `useState` hooks at component top |
| Side effects | `useEffect` with proper deps |
| Callbacks | `useCallback` for functions passed as props or in deps |
| Refs | `useRef` for DOM access and mutable containers |
| Components | Functional only, props destructured |
| Keys | Stable keys (entity `id` preferred over array index) |

### MCP / effect stability (CRITICAL)

The MCP config object passed to the API-calling function must be **memoized**.
If it is derived inline on every render, a new object is created each time →
any `useCallback`/`useEffect` that depends on it sees a new reference → the
effect re-fires every render → infinite loop → repeated MCP auth prompts (one
per API call).

```javascript
// ✅ Correct — stable reference, effect runs once on mount
const mcpServer = useMemo(() => getMcpConfig(mcpUrl), [mcpUrl]);

// ❌ Forbidden — new object every render, infinite useEffect loop
const mcpServer = getMcpConfig(mcpUrl);
```

Quality checklist: confirm any object used in `useEffect`/`useCallback` deps
that is derived from state is wrapped in `useMemo` with the right dependency
array.

## Styling

- **Framework**: Tailwind CSS utility classes
- Match the existing theme palette and typography exactly; ∀ new UI elements:
  match the existing palette — ¬ introduce new colors without explicit user
  approval.

## API Interaction

All data access goes through the hosting model's messages API with MCP server
passthrough:

```javascript
const data = await callModel(mcpServer, systemPrompt, userPrompt);
const { toolResults, text } = extractFromResponse(data);
```

- The call wrapper hits the messages API with an `mcp_servers` config
- The response extractor separates text, tool results, and tool calls
- Tool results are the primary data source; text is fallback
- ∀ new data operations: follow this pattern, ¬ direct MCP calls

## File Structure (within single file)

Maintain this section ordering:

1. Configuration constants
2. API helpers (call wrapper, response extractor, JSON parse helper)
3. Shared UI components (spinner, badge, tab button)
4. Feature components (setup screen, entity list item, assertion card)
5. Main component with hooks → loaders → render
<!-- /target:* -->
