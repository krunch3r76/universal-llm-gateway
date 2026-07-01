<!-- target:* -->
# Python 3.12+

**Verify**: `ruff check --select=UP --fix .`

## Stdlib (prefer over third-party)
| Use | Replaces |
|-----|----------|
| `tomllib` | toml/tomli |
| `itertools.pairwise()` | range(len-1) |
| `itertools.batched()` | custom chunking |
| `zip(..., strict=True)` | silent truncation |
| `asyncio.TaskGroup` | gather() |
| `Path.walk()` | os.walk() |

## Types

### Type Hints (MANDATORY)
**Invariant**: ∀ functions, methods: typed parameters ∧ typed return

| Context | Requirement |
|---------|-------------|
| All functions/methods | Type all parameters + return type |
| Public APIs | Explicit types (no `Any` without justification) |
| Complex logic | Type intermediate variables for clarity |
| Generic code | Use `TypeVar` or `class[T]` syntax |
| Dicts/Lists | Specify contents: `dict[str, int]`, `list[Model]` |

**Exceptions**: Private helpers with obvious context may omit variable types

```python
# ✅ Correct
def fetch_user(user_id: str) -> User | None:
    cached: dict[str, User] = _get_cache()
    return cached.get(user_id)

# ❌ Missing types
def fetch_user(user_id):
    cached = _get_cache()
    return cached.get(user_id)
```

### Modern Syntax
| ✅ Use | ❌ Avoid |
|--------|----------|
| `X \| Y` | `Union[X, Y]` |
| `X \| None` | `Optional[X]` |
| `isinstance(x, A \| B)` | `isinstance(x, (A, B))` |
| `@override` | (none) |
| `class X(StrEnum)` | `class X(str, Enum)` |
| `class Q[T]` | `class Q(Generic[T])` |
| `type Alias = X` | `Alias: TypeAlias = X` |

**Mandatory**: `@override` on ALL overridden methods

## Patterns
| Use | For |
|-----|-----|
| `match/case` | dispatch, 3+ branches |
| `e.add_note()` | exception context |
| `TaskGroup` | concurrent async |
| `@dataclass(slots=True, kw_only=True)` | efficient dataclasses |

## Detection
```bash
rg "Union\[|Optional\[" --type py
rg "asyncio\.gather\(" --type py
```
<!-- /target:* -->
