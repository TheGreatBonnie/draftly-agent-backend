# Code Example Guidelines

Standards for code examples in documentation. All examples must be runnable, minimal, and tested.

## General Principles

- **Minimal** — only the code needed to demonstrate the concept
- **Complete** — includes imports, setup, and teardown
- **Runnable** — copy-paste executes without modification (given prerequisites)
- **Annotated** — comments explain non-obvious parts

## Language Tags

Always specify the language:

```python
# ✓ Good
```python
import requests
```

```bash
# ✓ Good
```bash
curl -X POST ...
```

## Structure by Doc Type

### Tutorial / How-To

```python
# Prerequisites stated upfront
# pip install package-name

from package import Client

client = Client(api_key="<API_KEY>")  # Replace with your key
result = client.do_thing(param="value")
print(result)
```

### Conceptual

```python
# Illustrates pattern, not a full program
class UserService:
    def __init__(self, db: Database):
        self.db = db

    async def get_user(self, user_id: str) -> User:
        return await self.db.users.find(user_id)
```

### API Reference

```python
# Method signature + minimal usage
client.users.create(email="user@example.com", name="User Name", role="member")
```

### Configuration

```yaml
# Full valid config snippet
auth:
  provider: oauth2
  client_id: "<CLIENT_ID>"
  client_secret: "<CLIENT_SECRET>"
  scopes: ["read", "write"]
```

## Placeholders

Use `<UPPER_SNAKE_CASE>` for user-replaceable values:

```python
api_key = "<YOUR_API_KEY>"
project_id = "<PROJECT_ID>"
database_url = "postgresql://<USER>:<PASS>@<HOST>:<PORT>/<DB>"
```

Never use `example.com`, `foo`, `bar`, `xxx` as placeholders.

## Error Handling

Include realistic error handling in tutorials:

```python
try:
    result = client.risky_operation()
except AuthenticationError:
    # Handle expired token
    client.refresh_token()
    result = client.risky_operation()
except RateLimitError as e:
    # Back off and retry
    time.sleep(e.retry_after)
    result = client.risky_operation()
```

## Output Examples

Show expected output when helpful:

```bash
$ draftly migrate --env production
Migrating database...
Applied 3 migrations (2.1s)
✓ Migration complete
```

## Anti-Patterns

| Anti-Pattern | Fix |
|--------------|-----|
| `...` or `# ...` omissions | Show complete code or use `// ...` with explanation |
| Hardcoded secrets | Use `<PLACEHOLDER>` with note |
| Unused imports/variables | Remove them |
| Production anti-patterns (no timeouts, no retries) | Show production-ready patterns |
| Framework-specific code in generic docs | Separate framework-specific examples |

## Testing

All examples in tutorials and how-to guides must be tested via CI. Reference examples should be validated by type checker or linter where applicable.