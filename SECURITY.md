# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Draftly, please report it responsibly.

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, please email security@draftly.dev with:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Security Measures

- Clerk handles authentication and session management
- Webhook signatures are verified (HMAC-SHA256 for GitHub, Ed25519 for Discord)
- JWT tokens are validated using Clerk's JWKS endpoint
- Database connections use TLS in production
- Secrets are never committed to the repository
