"""Email review notifications (SendGrid v3 API, best-effort)."""

from __future__ import annotations

from html import escape
from string import Template

import httpx
import structlog

from draftly.app.config import get_settings

logger = structlog.get_logger(__name__)

REVIEW_EMAIL_TEMPLATE = Template("""\
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
  <h1>Documentation Review Required</h1>
  <p>Hi ${reviewer_name},</p>
  <p>Draftly produced a documentation change that needs your review:</p>
  <blockquote>${summary}</blockquote>
  <p><a href="${dashboard_url}">Review in Dashboard</a></p>
  <p>This review link expires in 24 hours.</p>
</body>
</html>
""")


async def build_review_email_html(
    *,
    reviewer_name: str,
    summary: str,
    dashboard_url: str,
) -> str:
    return REVIEW_EMAIL_TEMPLATE.substitute(
        reviewer_name=escape(reviewer_name),
        summary=escape(summary),
        dashboard_url=escape(dashboard_url),
    )


async def send_email(to: str, subject: str, html_content: str) -> dict:
    settings = get_settings()
    if not settings.sendgrid_api_key:
        logger.warning("sendgrid_not_configured")
        return {"ok": False, "status": "skipped", "reason": "no_api_key"}

    headers = {
        "Authorization": f"Bearer {settings.sendgrid_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {
            "email": settings.sendgrid_from_email,
            "name": settings.sendgrid_from_name,
        },
        "subject": subject,
        "content": [{"type": "text/html", "value": html_content}],
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers=headers,
                json=payload,
                timeout=10,
            )
    except httpx.HTTPError as exc:
        logger.exception("sendgrid_transport_failed")
        return {"ok": False, "status": "failed", "error": str(exc)}
    if resp.status_code not in (200, 201, 202):
        logger.error("sendgrid_send_failed", status=resp.status_code, body=resp.text)
        return {"ok": False, "status": "failed", "error": resp.text}
    return {"ok": True, "status": "sent"}


async def send_review_notification(
    *,
    to: str,
    reviewer_name: str,
    review_id: str,
    summary: str,
    dashboard_url: str,
) -> dict:
    html = await build_review_email_html(
        reviewer_name=reviewer_name,
        summary=summary,
        dashboard_url=dashboard_url,
    )
    return await send_email(to, f"Review Required: {summary[:80]}", html)
