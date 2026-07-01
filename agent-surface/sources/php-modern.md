<!-- target:* -->
# PHP 8.3+ Modern Features

**Requirement**: PHP ≥8.3. Use modern features for type safety, security, and conciseness.

## PHP 8.3 Features

| Feature | Use Case |
|---|---|
| `json_validate()` | Validate JSON without decoding |
| Typed class constants | Type safety for constants |
| `#[\Override]` attribute | Document method overrides |
| Dynamic class constant fetch | `$class::CONSTANT_NAME` |

## Type System (USE CONSISTENTLY)

| Feature | Version | Syntax | Replaces |
|---|---|---|---|
| Property types | 8.0+ | `private string $name` | PHPDoc only |
| Union types | 8.0+ | `int\|float` | PHPDoc `@param int\|float` |
| Nullable | 7.1+ | `?string` | `string\|null` |
| Never type | 8.1+ | `never` | No return type for exit/throw |
| Intersection types | 8.1+ | `Countable&Iterator` | Multiple interfaces |
| DNF types | 8.2+ | `(A&B)\|(C&D)` | Complex type unions |
| `readonly` properties | 8.1+ | `readonly string $id` | Immutable after construction |
| `readonly` classes | 8.2+ | `readonly class User` | All properties readonly |

```php
// ✅ Modern: readonly class + constructor promotion
readonly class UserDTO {
    public function __construct(
        public string $id,
        public string $email,
        public ?string $name = null,
    ) {}
}
```

## Null Safety

| Feature | Version | Syntax |
|---|---|---|
| Null coalescing | 7.0+ | `$x ?? 'default'` |
| Null coalescing assignment | 7.4+ | `$x ??= 'default'` |
| Nullsafe operator | 8.0+ | `$obj?->method()` |

## Match Expression (PHP 8.0+)

USE instead of `switch` when returning values:

```php
$status = match($httpCode) {
    200, 201 => 'success',
    400, 422 => 'validation_error',
    401, 403 => 'auth_error',
    404 => 'not_found',
    default => 'unknown_error',
};
```

## Constructor Property Promotion (PHP 8.0+)

```php
// ✅ Promoted
class Entry {
    public function __construct(
        private string $id,
        private string $content,
        private DateTime $createdAt,
        private ?string $userId = null,
    ) {}
}
```

## Attributes (PHP 8.0+)

Replace PHPDoc annotations: `#[\Override]`, `#[Route('/api/entries', methods: ['GET'])]`

## Array Functions (Modern)

```php
$ids = array_map(fn($e) => $e->id, $entries);
$active = array_filter($entries, fn($e) => $e->isActive());
// Named arguments (8.0+)
array_filter(array: $entries, callback: fn($e) => $e->isActive());
```

## Error Handling

| Feature | Version | Use Case |
|---|---|---|
| `throw` expression | 8.0+ | `$x ?? throw new InvalidArgumentException(...)` |
| `TypeError`, `ValueError` | 8.0+ | Type and value errors |

## Security (CRITICAL)

### Password Hashing

| ✅ Use | ❌ Never |
|---|---|
| `password_hash($pw, PASSWORD_ARGON2ID)` | `md5($pw)` |
| `password_verify($pw, $hash)` | `sha1($pw)` |
| `password_needs_rehash($hash, PASSWORD_ARGON2ID)` | `hash('sha256', $pw)` |

### SQL Injection Prevention

```php
// ✅ Prepared statements
$stmt = $pdo->prepare('SELECT * FROM entries WHERE user_id = ? AND id = ?');
$stmt->execute([$userId, $entryId]);

// ❌ NEVER interpolate
$pdo->query("SELECT * FROM entries WHERE user_id = '$userId'");
```

### Input Validation

```php
$email = filter_var($_POST['email'], FILTER_VALIDATE_EMAIL)
    ?: throw new InvalidArgumentException('Invalid email');
$id = filter_var($_GET['id'], FILTER_VALIDATE_INT)
    ?: throw new InvalidArgumentException('Invalid ID');
```

### PDO Configuration

```php
$pdo = new PDO($dsn, $user, $pass, [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    PDO::ATTR_EMULATE_PREPARES => false,
]);
```

## Anti-Patterns

| ❌ Avoid | ✅ Use Instead |
|---|---|
| `$x = $y ?: 'default'` | `$x = $y ?? 'default'` |
| `if (isset($x)) $x->method()` | `$x?->method()` |
| `switch` for values | `match` expression |
| Verbose constructor | Constructor property promotion |
| PHPDoc for types | Native property/parameter types |
| `md5()`, `sha1()` passwords | `password_hash()` with ARGON2ID |
| String concatenation in SQL | Prepared statements |
| `mysqli_*` functions | PDO with prepared statements |
| `@` error suppression | Proper error handling |
| Raw `$_POST`, `$_GET` | `filter_var()` / `filter_input()` first |

## Verification

- [ ] No `md5()` or `sha1()` for passwords
- [ ] All queries use prepared statements
- [ ] Input validation before processing
- [ ] Type hints on all functions/methods
- [ ] `match` instead of `switch` where appropriate
- [ ] Constructor property promotion
- [ ] Nullsafe `?->` where appropriate
- [ ] `#[\Override]` on overridden methods
- [ ] JSON responses with `JSON_THROW_ON_ERROR`

## Detection

```bash
rg "md5\(.*password|sha1\(.*password" --type php
rg "mysql_query|mysqli_query" --type php
rg "\\\$_(GET|POST|REQUEST)\[" --type php | rg -v "filter_var|filter_input"
rg "public function __construct.*\{" --type php -A 5 | rg "this->"
rg "switch.*\(" --type php
rg "query.*\\\$|prepare.*['\"].*\\\$" --type php
rg "function.*\(.*\)" --type php | rg -v ": (void|int|string|bool|array|float|mixed)"
```
<!-- /target:* -->
