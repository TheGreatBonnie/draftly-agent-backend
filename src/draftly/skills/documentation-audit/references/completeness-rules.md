# Documentation Audit — Completeness Rules

## Purpose
Rules for detecting missing or incomplete documentation content during audits.

## Required Coverage Areas
Every documented feature/area must cover:
- **Overview**: What it does and why it exists
- **Prerequisites**: Dependencies, permissions, setup
- **Configuration**: All settings with defaults and valid ranges
- **Usage**: Common workflows with code examples
- **API Reference**: Public functions, classes, endpoints with signatures
- **Error Handling**: Common errors, codes, remediation
- **Migration/Upgrade**: Breaking changes and upgrade paths

## Completeness Thresholds
| Area | Minimum Requirement |
|------|---------------------|
| Overview | ≥ 3 sentences, includes purpose |
| Prerequisites | All external deps listed |
| Configuration | Every setting documented |
| Usage | ≥ 1 runnable example per primary workflow |
| API Reference | 100% public surface covered |
| Error Handling | Top 5 errors by frequency |
| Migration | Every breaking change has path |

## Detection Rules
- **Missing section**: Required heading absent from page
- **Empty section**: Heading present but < 50 words of content
- **Outdated example**: Code example references removed/renamed API
- **Unverified claim**: Statement without code/link evidence
- **Orphan page**: No inbound links from index or related pages

## Audit Scoring
- Each missing required section: -0.15
- Each empty section: -0.10
- Each outdated example: -0.05
- Each unverified claim: -0.05
- Page passes completeness if score ≥ 0.70

## Integration
Used by `documentation_audit.py` freshness scan and `documentation-evaluation` skill.
Cross-references `documentation-research` for coverage mapping.