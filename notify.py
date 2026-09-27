"""notify.py — Email the run report + Excel via SMTP (Gmail App Password).

Credentials are read from the environment (via config), never hard-coded. If
they are not set (e.g. local runs without secrets), emailing is skipped with a
log message instead of failing.

Frequency is controlled by config.EMAIL_MODE:
  * "actionable" (default) — only email when a Buy / Strong Buy / Exit appears,
    so the 10-minute loop does not spam you.
  * "hourly" — email at most once per hour (throttle state in a temp file that
    survives across cycles of the continuous-session loop job).
  * "always" — email every run (used by the evening summary job).
  * "off" — never email.
"""

import os
import ssl
import time
import smtplib
import logging
import tempfile
from email.message import EmailMessage
from datetime import datetime

import config

log = logging.getLogger("notify")

_HOURLY_STAMP = os.path.join(tempfile.gettempdir(), "psx_engine_last_email")
_HOURLY_SECONDS = 3600


def _hourly_due():
    try:
        age = time.time() - os.path.getmtime(_HOURLY_STAMP)
    except OSError:
        return True
    return age >= _HOURLY_SECONDS


def _mark_hourly_sent():
    with open(_HOURLY_STAMP, "w") as f:
        f.write(str(time.time()))


def _should_send(results):
    mode = (config.EMAIL_MODE or "actionable").lower()
    if mode == "off":
        return False, "EMAIL_MODE=off"
    if mode == "always":
        return True, "always"
    if mode == "hourly":
        if _hourly_due():
            return True, "hourly window elapsed"
        return False, "hourly throttle — sent within the last hour"
    actionable = [r["symbol"] for r in results
                  if r["signal"]["signal"] in config.ACTIONABLE_SIGNALS]
    if actionable:
        return True, "actionable signals: " + ", ".join(actionable)
    return False, "no actionable signals this run"


def _creds_ok():
    if not (config.SMTP_USER and config.SMTP_APP_PASSWORD and config.EMAIL_TO):
        log.info("Email skipped — SMTP_USER / SMTP_APP_PASSWORD / EMAIL_TO "
                 "not configured.")
        return False
    return True


def _deliver(subject, body, attachment_path=None):
    """Low-level SMTP send. Returns True on success."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_USER
    msg["To"] = config.EMAIL_TO
    msg.set_content(body)
    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as f:
            msg.add_attachment(
                f.read(), maintype="application",
                subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=os.path.basename(attachment_path))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(config.SMTP_USER, config.SMTP_APP_PASSWORD)
            s.send_message(msg)
        log.info("Email sent to %s.", config.EMAIL_TO)
        return True
    except Exception as e:
        log.warning("Email send failed: %s", e)
        return False


def send_report(results, report_text, attachment_path=None):
    """Email the run report with the Excel attached (gated by EMAIL_MODE)."""
    if not _creds_ok():
        return False
    ok, reason = _should_send(results)
    if not ok:
        log.info("Email skipped — %s.", reason)
        return False

    # final_score is None for a symbol with no usable data, and the expanded
    # universe produces plenty of those (456 of 3,192 rows on 2026-09-07), so a
    # bare sort raises "'<' not supported between NoneType and float" and costs
    # the whole email. excel_export already guards this the same way.
    ranked = sorted([r for r in results if r["shariah"]["eligible_for_ranking"]],
                    key=lambda r: (r["scoring"]["final_score"]
                                   if r["scoring"]["final_score"] is not None else -1),
                    reverse=True)
    top = ranked[0] if ranked else None
    subject = f"PSX Engine {datetime.now():%Y-%m-%d %H:%M} — "
    subject += (f"{top['symbol']} {top['signal']['signal']} "
                f"(score {top['scoring']['final_score']})" if top else "report")
    body = (report_text[:4000]
            + "\n\n[Full ranking attached as an Excel file.]\n"
            + config.DISCLAIMER)
    sent = _deliver(subject, body, attachment_path)
    if sent:
        _mark_hourly_sent()
    return sent


def send_text(subject, body, attachment_path=None):
    """Send a plain-text email (e.g. the evening summary). Respects
    EMAIL_MODE != 'off' and the presence of credentials."""
    if not _creds_ok():
        return False
    if (config.EMAIL_MODE or "").lower() == "off":
        log.info("Email skipped — EMAIL_MODE=off.")
        return False
    return _deliver(subject, body, attachment_path)
