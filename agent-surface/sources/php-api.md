<!-- target:* -->
# Backend API Patterns (PHP)

**For PHP 8.3+ REST APIs with JSON responses. MySQL ≥8.0.**

**Principles**: consistent JSON responses, validate before processing, structured errors, stateless

## HTTP Status Codes (MANDATORY)

| Code | Meaning | Use Case |
|------|---------|----------|
| 200 | OK | Successful GET, PUT, PATCH, DELETE |
| 201 | Created | Successful POST creating resource |
| 204 | No Content | Successful DELETE, no body |
| 400 | Bad Request | Malformed request, validation error |
| 401 | Unauthorized | Missing or invalid authentication |
| 403 | Forbidden | Valid auth, insufficient permissions |
| 404 | Not Found | Resource doesn't exist |
| 409 | Conflict | Duplicate entry |
| 422 | Unprocessable | Detailed validation errors |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Error | Unexpected server error |
| 503 | Unavailable | Temporary outage |

## Response Format

```php
// Success
function sendSuccess(array $data, int $statusCode = 200): never {
    http_response_code($statusCode);
    header('Content-Type: application/json; charset=utf-8');
    header('X-Content-Type-Options: nosniff');
    echo json_encode($data, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE);
    exit;
}

// Error
function sendError(string $message, int $statusCode = 400, ?array $details = null): never {
    http_response_code($statusCode);
    header('Content-Type: application/json; charset=utf-8');
    $response = ['error' => $message, 'status' => $statusCode];
    if ($details !== null) $response['details'] = $details;
    echo json_encode($response, JSON_THROW_ON_ERROR);
    exit;
}
```

## Input Validation

```php
$input = json_decode(file_get_contents('php://input'), true);
if (json_last_error() !== JSON_ERROR_NONE) sendError('Invalid JSON', 400);

// Validate fields, collect errors
$errors = [];
if (!isset($data['content']) || trim($data['content']) === '') {
    $errors['content'] = 'Content is required';
}
if (isset($data['id'])) {
    $id = filter_var($data['id'], FILTER_VALIDATE_INT);
    if ($id === false) $errors['id'] = 'Must be a valid integer';
}
if (!empty($errors)) sendError('Validation failed', 422, $errors);
```

## CORS

```php
function setCorsHeaders(): void {
    $origin = $_SERVER['HTTP_ORIGIN'] ?? '';
    $allowed = ['https://yourdomain.com', 'http://localhost:8080'];

    if (in_array($origin, $allowed, true)) {
        header("Access-Control-Allow-Origin: $origin");
    }

    header('Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, Authorization');
    header('Access-Control-Max-Age: 86400');

    if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
        http_response_code(204);
        exit;
    }
}
```

## Error Handling

```php
try {
    $user = requireAuth($pdo);
    $validated = validateEntry($input);
    $entryId = createEntry($pdo, $user['user_id'], $validated);
    sendSuccess(['entry_id' => $entryId], 201);
} catch (PDOException $e) {
    error_log("Database error: " . $e->getMessage());
    sendError('Database error occurred', 500);
} catch (Exception $e) {
    error_log("Unexpected error: " . $e->getMessage());
    sendError('An unexpected error occurred', 500);
}
```

## Endpoint Routing

```php
// Match on method, dispatch to handlers
match ($_SERVER['REQUEST_METHOD']) {
    'GET' => handleGet($pdo),
    'POST' => handleCreate($pdo),
    'PUT' => handleUpdate($pdo),
    'DELETE' => handleDelete($pdo),
    default => sendError('Method not allowed', 405),
};
```

## MySQL 8.0+ Features

| Feature | Use Case |
|---|---|
| Window functions (`ROW_NUMBER`, `RANK`, `LAG`, `LEAD`) | Ranking, running totals, analytics |
| CTEs (`WITH ... AS`) | Complex queries, hierarchical data |
| `JSON_TABLE`, `JSON_EXTRACT` | Structured JSON column queries |

## Transaction Pattern

```php
try {
    $pdo->beginTransaction();
    // ... multiple statements ...
    $pdo->commit();
} catch (Exception $e) {
    $pdo->rollBack();
    throw $e;
}
```

## Anti-Patterns

| ❌ Avoid | ✅ Instead |
|---|---|
| Default 200 for everything | Proper HTTP status codes |
| Mixed response formats (text + JSON) | Always `sendSuccess()` / `sendError()` |
| Raw `$_GET`/`$_POST` without validation | `filter_var()` / `filter_input()` first |
| Exposing `$e->getMessage()` to client | Log internally, generic error to client |
| `header('Access-Control-Allow-Origin: *')` | Whitelist specific origins |
| SQL without prepared statements | Always `$pdo->prepare()` |
| No transactions for multi-step writes | Wrap in `beginTransaction` / `commit` |

## Verification

- [ ] All responses use proper HTTP status codes
- [ ] All responses are JSON with Content-Type header
- [ ] Input validation before processing
- [ ] Prepared statements for all queries
- [ ] CORS headers configured (not `*` in production)
- [ ] Errors logged but not exposed to client
- [ ] Transactions for multi-step operations
- [ ] MySQL 8.0+ features used when appropriate

## Detection

```bash
rg "echo json_encode" --type php | rg -v "http_response_code"    # missing status codes
rg "query.*\\\$|WHERE.*\\\$" --type php | rg -v "prepare"        # SQL injection
rg "getMessage\(\)" --type php -A 2 | rg "echo|json_encode"      # exposed errors
rg "Allow-Origin: \*" --type php                                  # open CORS
rg "\\\$_(GET|POST)" --type php | rg -v "filter_var|filter_input" # raw input
```
<!-- /target:* -->
