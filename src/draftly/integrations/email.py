"""Email review notifications (SendGrid v3 API, best-effort)."""

# ruff: noqa: E501  # HTML email markup is kept single-line so it stays readable
# and matches what clients receive; wrapping would add cosmetic whitespace.

from __future__ import annotations

from html import escape
from string import Template

import httpx
import structlog

from draftly.app.config import get_settings

logger = structlog.get_logger(__name__)

REVIEW_EMAIL_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<meta name="x-apple-disable-message-reformatting">
<title>Documentation Review Required</title>
<style>
  @media (max-width: 620px) {
    .wrapper { width: 100% !important; }
    .band { padding: 24px 20px !important; }
    .card { padding: 24px 20px !important; }
    .foot { padding: 18px 20px !important; }
  }
  @media (prefers-color-scheme: dark) {
    .page-bg { background-color: #101218 !important; }
    .card-bg { background-color: #1e2129 !important; }
    .txt-main { color: #f4f6fb !important; }
    .txt-body { color: #d6dae3 !important; }
    .txt-muted { color: #8a93a6 !important; }
    .callout-bg { background-color: #232b3a !important; border-left-color: #1260ed !important; }
    .foot-bg { background-color: #1a1d24 !important; }
  }
</style>
</head>
<body class="page-bg" style="margin:0;padding:0;background-color:#eef1f7;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#eef1f7;">
    <tr>
      <td align="center" style="padding:32px 12px;">
        <table role="presentation" class="wrapper card-bg" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;overflow:hidden;border-radius:12px;background-color:#ffffff;">
          <tr>
            <td bgcolor="#1260ed" class="band" style="background-color:#1260ed;padding:28px 32px;">
              <p style="margin:0 0 8px;font-size:12px;letter-spacing:1.5px;text-transform:uppercase;color:#cfe0ff;">Draftly</p>
              <h1 style="margin:0;font-size:24px;line-height:1.3;font-weight:bold;color:#ffffff;">Documentation Review Required</h1>
            </td>
          </tr>
          <tr>
            <td class="card" style="padding:28px 32px;">
              <p class="txt-main" style="margin:0 0 18px;font-size:16px;color:#1c1e26;">Hi ${reviewer_name},</p>
              ${intro}
              ${review_callout}
              ${review_button}
              ${review_meta}
            </td>
          </tr>
          <tr>
            <td class="foot foot-bg" style="padding:18px 32px;border-top:1px solid #e4e8f0;background-color:#fafbfd;">
              <p style="margin:0;font-size:12px;line-height:1.6;color:#8a93a6;">This is an automated notification from Draftly. Replies to this email are not monitored.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
""")


def _email_intro() -> str:
    return (
        '<p class="txt-body" style="margin:0 0 20px;font-size:16px;color:#3a4256;">'
        "Draftly produced a documentation change that needs your review.</p>"
    )


def _email_callout(*, source: str, title: str, summary: str) -> str:
    source = escape(source)
    title = escape(title)
    summary = escape(summary)
    return (
        '<table role="presentation" width="100%" cellpadding="0" '
        'cellspacing="0" border="0">'
        '<tr><td class="callout-bg" style="background-color:#f2f7ff;'
        'border-left:4px solid #1260ed;border-radius:8px;padding:16px 18px;">'
        f'<p style="margin:0 0 4px;font-size:11px;letter-spacing:1px;'
        f'text-transform:uppercase;color:#44507a;">Change source · {source}</p>'
        f'<p class="txt-main" style="margin:0 0 8px;font-size:17px;'
        f'font-weight:bold;color:#1c1e26;">{title}</p>'
        f'<p class="txt-body" style="margin:0;font-size:14px;line-height:1.6;'
        f'color:#3a4256;">{summary}</p>'
        "</td></tr></table>"
    )


def _email_button(dashboard_url: str) -> str:
    """Bulletproof CTA: MSO VML for Outlook desktop, styled anchor otherwise."""
    url = escape(dashboard_url)
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:24px 0 0;">'
        '<tr><td align="center" bgcolor="#1260ed" style="border-radius:6px;">'
        "<!--[if mso]>"
        '<v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:w="urn:schemas-microsoft-com:office:word" '
        f'href="{url}" style="height:46px;v-text-anchor:middle;width:200px;" '
        'arcsize="14%" stroke="f" fillcolor="#1260ed">'
        "<w:anchorlock/>"
        '<center style="color:#ffffff;font-family:Arial,sans-serif;'
        'font-size:16px;font-weight:bold;">Review in Dashboard</center>'
        "</v:roundrect><![endif]-->"
        "<!--[if !mso]><!-->"
        f'<a href="{url}" target="_blank" rel="noopener" '
        'style="display:inline-block;padding:12px 28px;background-color:#1260ed;'
        'color:#ffffff;font-family:Arial,sans-serif;font-size:16px;'
        'font-weight:bold;text-decoration:none;border-radius:6px;">'
        "Review in Dashboard</a><!--<![endif]-->"
        "</td></tr></table>"
    )


def _email_meta(reviewer_name: str) -> str:
    reviewer_name = escape(reviewer_name)
    return (
        f'<p class="txt-muted" style="margin:22px 0 0;font-size:13px;color:#7a8292;">'
        f"For {reviewer_name} · This review link expires in 24 hours.</p>"
    )


async def build_review_email_html(
    *,
    reviewer_name: str,
    summary: str,
    dashboard_url: str,
    title: str = "Documentation Change",
    source: str = "draftly",
) -> str:
    return REVIEW_EMAIL_TEMPLATE.substitute(
        reviewer_name=escape(reviewer_name),
        intro=_email_intro(),
        review_callout=_email_callout(source=source, title=title, summary=summary),
        review_button=_email_button(dashboard_url),
        review_meta=_email_meta(reviewer_name),
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
    title: str = "Documentation Change",
    source: str = "draftly",
) -> dict:
    html = await build_review_email_html(
        reviewer_name=reviewer_name,
        summary=summary,
        dashboard_url=dashboard_url,
        title=title,
        source=source,
    )
    return await send_email(to, f"Review Required: {summary[:80]}", html)
