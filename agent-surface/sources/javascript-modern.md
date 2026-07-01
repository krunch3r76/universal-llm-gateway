<!-- target:* -->
# JavaScript Modern Features (ES6+)

**Requirement**: Use modern JavaScript (ES6+) for cleaner, more maintainable code.

## Standard Library & Built-in Features (PREFER FIRST)

| Feature | ES Version | Replaces | Use Case |
|---------|-----------|----------|----------|
| `Promise.allSettled()` | ES2020 | Manual promise tracking | Wait for all, handle failures individually |
| `Promise.any()` | ES2021 | Race with first success | First successful result |
| `Optional chaining ?.` | ES2020 | Manual null checks | Safe property access |
| `Nullish coalescing ??` | ES2020 | `\|\|` with falsy values | Default only for null/undefined |
| `Array.at()` | ES2022 | Negative index workarounds | Access from end: `arr.at(-1)` |
| `Object.hasOwn()` | ES2022 | `hasOwnProperty.call()` | Safer property check |
| `String.replaceAll()` | ES2021 | Regex with /g flag | Replace all occurrences |

## Modern Syntax (USE CONSISTENTLY)

| Feature | ES Version | Syntax | Replaces |
|---------|-----------|--------|----------|
| Arrow functions | ES6 | `(x) => x * 2` | `function(x) { return x * 2; }` |
| Template literals | ES6 | `` `Hello ${name}` `` | `'Hello ' + name` |
| Destructuring | ES6 | `const {x, y} = obj` | `var x = obj.x; var y = obj.y;` |
| Spread operator | ES6 | `[...arr1, ...arr2]` | `arr1.concat(arr2)` |
| Rest parameters | ES6 | `function f(...args)` | `arguments` object |
| Default parameters | ES6 | `function f(x = 10)` | Manual default check |
| const/let | ES6 | `const x = 1; let y = 2;` | `var x = 1;` |
| Classes | ES6 | `class Person { }` | Prototype pattern |
| Modules | ES6 | `import/export` | `require()` (if bundling) |

## Async Improvements

| Feature | ES Version | Use Instead Of |
|---------|-----------|----------------|
| `async/await` | ES2017 | Nested `.then()` chains |
| `Promise.all()` | ES6 | Manual promise coordination |
| `Promise.race()` | ES6 | Manual first-completion |
| Top-level await | ES2022 | Wrapper async function in modules |

```javascript
// ❌ Nested promises
fetch(url)
    .then(res => res.json())
    .then(data => processData(data))
    .catch(err => handleError(err));

// ✅ async/await
try {
    const res = await fetch(url);
    const data = await res.json();
    processData(data);
} catch (err) {
    handleError(err);
}
```

## Object & Array Methods

```javascript
const doubled = arr.map(x => x * 2);
const filtered = arr.filter(x => x > 10);
const found = arr.find(x => x.id === 5);
const exists = arr.some(x => x.active);
const allValid = arr.every(x => x.valid);

const entries = Object.entries(obj);
const merged = {...obj1, ...obj2};
```

## Quick Anti-Pattern Check

| ❌ Avoid | ✅ Use Instead |
|---------|---------------|
| `var x = 1` | `const x = 1` or `let x = 1` |
| `'string' + var` | `` `string ${var}` `` |
| `obj && obj.prop && obj.prop.val` | `obj?.prop?.val` |
| `x \|\| defaultVal` (when x can be 0/'') | `x ?? defaultVal` |
| `.then().then().catch()` | `async/await` with try/catch |
| `arr[arr.length - 1]` | `arr.at(-1)` |
| `obj.hasOwnProperty(key)` | `Object.hasOwn(obj, key)` |
| `function() { }` (anonymous) | Arrow function `() => { }` |
| Manual loop for transformation | `.map()`, `.filter()`, `.reduce()` |

## Browser API Modernization

| Feature | Modern API | Replaces |
|---------|-----------|----------|
| `fetch()` | Modern HTTP | `XMLHttpRequest` |
| `IntersectionObserver` | Viewport detection | Scroll listeners |
| `ResizeObserver` | Element size changes | Window resize + calculation |
| `MutationObserver` | DOM changes | Polling or events |
| `requestAnimationFrame` | Animations | `setTimeout` for animations |
| `requestIdleCallback` | Low-priority work | `setTimeout(fn, 0)` |
| Web Crypto API | `crypto.subtle` | Insecure crypto libraries |

## Verification

Before commit:
- [ ] No `var` declarations
- [ ] Template literals for interpolation
- [ ] Arrow functions (except when `this` binding needed)
- [ ] `async/await` (not nested `.then()`)
- [ ] Destructuring where appropriate
- [ ] `const` by default, `let` only for reassignment
- [ ] Optional chaining `?.` for safe access
- [ ] Nullish coalescing `??` for defaults
- [ ] Modern array methods (`.map()`, `.filter()`, etc.)

## Detection

```bash
rg "var " --type js
rg "function\s*\(" --type js
rg "\.then\(.*\.then\(" --type js
rg "XMLHttpRequest" --type js
rg "if.*&&.*\." --type js
```
<!-- /target:* -->
