# Documentation Style Guide

Enforces consistent voice, formatting, and technical writing conventions across all Draftly-generated documentation.

## Voice & Tone

- **Direct** — imperative mood for instructions ("Run the command", not "You should run the command")
- **Technical** — precise terminology, no marketing language
- **Practical** — focus on what the developer needs to accomplish
- **Active** — "The API returns" not "The API will return"

## Formatting Rules

| Element | Convention |
|---------|------------|
| Headings | ATX style (`##`), sentence case, no trailing colon |
| Code inline | Backticks for identifiers, paths, commands, flags |
| Code blocks | Fenced with language tag (` ```python `) |
| Placeholders | `<UPPER_SNAKE_CASE>` (e.g., `<API_KEY>`, `<PROJECT_ID>`) |
| Bold | UI elements only (buttons, tabs, menu items) |
| Italics | *Emphasis* or first use of a term |
| Lists | Numbered for ordered steps, bulleted for options |
| Tables | For parameters, options, comparisons, version matrices |

## Prohibited Patterns

| Pattern | Replace With |
|---------|--------------|
| "In order to" | "To" |
| "You will need to" | "You need to" or imperative |
| "Please note that" | Direct statement |
| "It is recommended that" | "Recommend" or imperative |
| "The following example shows" | Direct example |
| "As mentioned previously" | Link to the section |

## Heading Hierarchy

```
# Page Title (H1 - auto from frontmatter)
## Major Section (H2)
### Subsection (H3)
#### Detail (H4 - avoid if possible)
```

Never skip levels. Maximum depth: H3 for most pages, H4 only for complex references.

## Cross-References

- Internal: `[Link text](../path/to/page.md)` — relative paths
- External: `[Link text](https://example.com)` — full URLs
- Anchor links: `[Section](#section-slug)` — lowercase, hyphens
- Always use descriptive link text, never "here" or "click here"

## Version References

- Current version: "In v2.0..." (not "In the latest version")
- Deprecated: "Deprecated in v2.0, removed in v3.0"
- Upcoming: "Planned for v2.1" (with issue/PR reference)