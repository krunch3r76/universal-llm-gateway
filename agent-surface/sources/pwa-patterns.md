<!-- target:* -->
# PWA Architecture Patterns

**Principles**: offline-first, cached resources, installable, re-engageable

## Cache Strategies (MANDATORY)

| Resource Type | Strategy | Rationale |
|---|---|---|
| App shell (HTML/CSS/JS) | Cache First | Fast load, update in background |
| API responses | Network First | Fresh data when online, fallback offline |
| User content | Network First | Always fresh, cache for offline |
| Static assets (images/icons) | Cache First | Rarely change, long cache |
| Dynamic content | Stale While Revalidate | Show cached, update background |

## Service Worker Lifecycle

### Install → Activate → Fetch

```javascript
// Install: precache app shell, force activate
self.addEventListener('install', event => {
    self.skipWaiting();
    event.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(APP_SHELL)));
});

// Activate: clean old caches, claim clients
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(names =>
            Promise.all(names.filter(n => n !== CACHE_NAME).map(n => caches.delete(n)))
        )
    );
    return self.clients.claim();
});
```

### Fetch Patterns

```javascript
// Cache First (app shell)
event.respondWith(caches.match(req).then(cached => cached || fetch(req)));

// Network First (API)
event.respondWith(
    fetch(req)
        .then(res => { cacheIfOk(res.clone()); return res; })
        .catch(() => caches.match(req))
);

// Stale While Revalidate
event.respondWith(
    caches.match(req).then(cached => {
        const fresh = fetch(req).then(res => { cacheIfOk(res.clone()); return res; });
        return cached || fresh;
    })
);
```

## Background Sync

```javascript
// App: register sync
navigator.serviceWorker.ready.then(reg => reg.sync.register('sync-entries'));

// SW: handle sync event
self.addEventListener('sync', event => {
    if (event.tag === 'sync-entries') event.waitUntil(syncPending());
});

async function syncPending() {
    const pending = await db.getAll('pending');
    for (const item of pending) {
        await fetch('/api/entries', { method: 'POST', body: JSON.stringify(item) });
        await db.delete('pending', item.id);
    }
}
```

## IndexedDB Patterns

| Operation | Pattern |
|---|---|
| Open DB | `openDB('name', version, { upgrade(db) { db.createObjectStore(...) } })` |
| Queue action | `db.add('pending', { action, timestamp: Date.now() })` |
| Process queue | Iterate pending → execute → delete on success |
| Conflict resolution | Last-write-wins with timestamp, or merge non-conflicting fields |

### Sync Status

| State | Meaning |
|---|---|
| `synced` | Confirmed on server |
| `pending` | Queued locally |
| `syncing` | In progress |
| `error` | Failed, needs retry |

## Optimistic UI Flow

1. Update UI immediately
2. Save to IndexedDB
3. Try server sync
4. On failure → queue for background sync

## Anti-Patterns

| ❌ Avoid | ✅ Instead |
|---|---|
| Blocking operations in fetch handler | Async with `event.respondWith((async () => ...)())` |
| Caching everything unbounded | Cache with `MAX_CACHE_SIZE` per strategy |
| Sync without idempotency | Use temp IDs + `X-Temp-Id` header, deduplicate server-side |
| No old cache cleanup on activate | Delete caches where `name !== CACHE_NAME` |
| Missing error handling on fetch | Always `.catch()` with cache fallback |

## Verification

### Service Worker
- [ ] Appropriate cache strategy per resource type
- [ ] Old caches cleaned on activate
- [ ] No blocking operations in fetch handler
- [ ] Error handling for fetch failures
- [ ] Updates handled gracefully (skipWaiting + clients.claim)

### Offline Support
- [ ] App works with no network
- [ ] Actions queued when offline
- [ ] Background sync registered
- [ ] Clear sync status in UI
- [ ] Conflict resolution defined

### Performance
- [ ] App shell cached (fast first load)
- [ ] Cache size limited
- [ ] Stale data refreshed in background
- [ ] Network requests not duplicated

## Detection

```bash
rg "addEventListener.*fetch" sw.js -A 10 | rg -v "await|\.then"  # blocking ops
rg "cache\.put" sw.js | rg -v "MAX|LIMIT|SIZE"                   # unbounded cache
rg "fetch\(" --type js | rg -v "catch|try"                        # missing error handling
rg "sync.*register" --type js                                     # sync registrations
```
<!-- /target:* -->
