"""morning_digest.py - 7am email of overnight director buys (B-116).

Reads the freshest exported `dealings.json` (produced by the morning pipeline:
export_dashboard_json.py -> build_dashboard.py) and emails a summary of the
overnight BUY signals to Rupert. Pre-earnings buys (B-114) are highlighted.

**Safety / sending model**
- **Dry-run by DEFAULT** - prints the digest, sends nothing. Add `--send` to send.
- SMTP credentials are read from ENVIRONMENT VARIABLES only (never stored in
  code): `DD_SMTP_HOST`, `DD_SMTP_PORT` (default 587), `DD_SMTP_USER`,
  `DD_SMTP_PASS` (a Gmail *app password*, not your account password).
- Recipient is locked to rspiegelberg@gmail.com (B-116 decision).
- **Freshness guard:** if `dealings.json` is not from today (the morning pipeline
  didn't run / failed), the digest is NOT sent - stale data never goes out.

**Setup (one-time, Rupert):**
1. Create a Gmail App Password (Google Account -> Security -> App passwords).
2. Set env vars (PowerShell, user scope), e.g.:
       setx DD_SMTP_HOST smtp.gmail.com
       setx DD_SMTP_USER you@gmail.com
       setx DD_SMTP_PASS "<the 16-char app password>"
3. Test:   python .scripts\\morning_digest.py            (dry-run, prints)
4. Send:   python .scripts\\morning_digest.py --send
5. Schedule the `--send` command at 07:00 daily via Windows Task Scheduler,
   AFTER the morning export+build.

(Alternative delivery - an in-assistant scheduled task - is also possible; this
standalone script was chosen as the robust default. Switchable later.)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RECIPIENT = "rspiegelberg@gmail.com"  # locked (B-116)

# Freshest dealings.json candidates, most-canonical first.
_DEALINGS_CANDIDATES = [
    REPO / "outputs" / "data" / "dealings.json",
    HERE / "dashboard" / "data" / "dealings.json",
    REPO / "dashboard" / "data" / "dealings.json",
]


# --- Pure helpers (unit-tested; no I/O / no network) ------------------------

def _fmt_gbp(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    if v >= 1_000_000:
        return f"GBP {v/1_000_000:.1f}m"
    if v >= 1_000:
        return f"GBP {v/1_000:.0f}k"
    return f"GBP {v:.0f}"


def is_fresh(dealings: dict, today: date) -> bool:
    """True when the export's as_of_date is today (pipeline ran). Stale -> don't send."""
    return (dealings or {}).get("as_of_date") == today.isoformat()


def build_digest(dealings: dict, today: date) -> dict:
    """Build the digest from a dealings payload.

    Returns {'subject', 'text', 'n_buys', 'n_pre_earnings', 'has_content'}.
    'Overnight' buys = the export's `today` BUY rows; if none, falls back to the
    `this_week` BUYs so a quiet night still gives useful context.
    """
    rows = [r for r in (dealings.get("today") or [])
            if (r.get("txn_type") or "BUY").upper() == "BUY"]
    scope = "today"
    if not rows:
        rows = [r for r in (dealings.get("this_week") or [])
                if (r.get("txn_type") or "BUY").upper() == "BUY"]
        scope = "this week"

    pe_rows = [r for r in rows if r.get("near_reporting_date")]
    n_buys, n_pe = len(rows), len(pe_rows)
    d = today.strftime("%a %d %b %Y")

    if not rows:
        return {
            "subject": f"Directors Dealings -{d}: no new director buys overnight",
            "text": f"No new director BUY signals overnight ({d}).",
            "n_buys": 0, "n_pre_earnings": 0, "has_content": False,
        }

    lines = [f"Directors Dealings -{d}",
             f"{n_buys} director BUY signal(s) {scope}"
             + (f"; {n_pe} are PRE-EARNINGS (buy within 60d of upcoming results)."
                if n_pe else ".")]
    if pe_rows:
        lines.append("")
        lines.append("** Pre-earnings buys (higher conviction): **")
        for r in pe_rows:
            est = " (est date)" if r.get("near_reporting_est") else ""
            lines.append(
                f"  - {r.get('ticker','?')}  {r.get('company','')}  "
                f"{r.get('director','')}  {_fmt_gbp(r.get('value_gbp'))}  "
                f"results ~{r.get('near_reporting_date')}{est}")
    other = [r for r in rows if not r.get("near_reporting_date")]
    if other:
        lines.append("")
        lines.append("Other buys:")
        for r in other:
            lines.append(
                f"  - {r.get('ticker','?')}  {r.get('company','')}  "
                f"{r.get('director','')}  {_fmt_gbp(r.get('value_gbp'))}")
    lines.append("")
    lines.append("Full dashboard: open outputs/index.html (or your local server).")

    subj = f"Directors Dealings -{d}: {n_buys} buy(s)"
    if n_pe:
        subj += f", {n_pe} pre-earnings"
    return {"subject": subj, "text": "\n".join(lines),
            "n_buys": n_buys, "n_pre_earnings": n_pe, "has_content": True}


# --- I/O + send -------------------------------------------------------------

def load_dealings(path: Path | None = None) -> tuple[dict, Path | None]:
    if path:
        return json.loads(path.read_text(encoding="utf-8")), path
    for cand in _DEALINGS_CANDIDATES:
        if cand.exists():
            return json.loads(cand.read_text(encoding="utf-8")), cand
    raise FileNotFoundError(
        "dealings.json not found. Run export_dashboard_json.py + build_dashboard.py "
        "first, or pass --dealings PATH.")


def _send_email(subject: str, text: str) -> None:
    """Send via SMTP using env-var credentials. Raises if not configured."""
    host = os.environ.get("DD_SMTP_HOST")
    user = os.environ.get("DD_SMTP_USER")
    pw = os.environ.get("DD_SMTP_PASS")
    port = int(os.environ.get("DD_SMTP_PORT", "587"))
    if not (host and user and pw):
        raise RuntimeError(
            "SMTP not configured. Set DD_SMTP_HOST / DD_SMTP_USER / DD_SMTP_PASS "
            "(a Gmail app password) - see this script's docstring.")
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = RECIPIENT
    msg.set_content(text)
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)


def run(*, send: bool = False, dealings_path: Path | None = None,
        force: bool = False, today: date | None = None) -> dict:
    today = today or date.today()
    dealings, used_path = load_dealings(dealings_path)
    digest = build_digest(dealings, today)
    fresh = is_fresh(dealings, today)

    print(f"[digest] source: {used_path}")
    print(f"[digest] fresh (as_of == today): {fresh}  "
          f"(as_of_date={dealings.get('as_of_date')})")
    print(f"[digest] buys={digest['n_buys']} pre_earnings={digest['n_pre_earnings']}")
    print("-" * 60)
    print(f"Subject: {digest['subject']}")
    print(digest["text"])
    print("-" * 60)

    if not send:
        print("[digest] DRY-RUN - nothing sent. Add --send to send.")
        return {**digest, "sent": False, "fresh": fresh}

    if not fresh and not force:
        print("[digest] STALE data (morning pipeline didn't run today) - NOT sending. "
              "Use --force to override.")
        return {**digest, "sent": False, "fresh": fresh, "skipped": "stale"}

    if not digest["has_content"]:
        # Quiet night: suppress the send by default (no noise).
        print("[digest] no new buys - suppressing the send (quiet night).")
        return {**digest, "sent": False, "fresh": fresh, "skipped": "empty"}

    _send_email(digest["subject"], digest["text"])
    print(f"[digest] SENT to {RECIPIENT}.")
    return {**digest, "sent": True, "fresh": fresh}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Morning digest of overnight director buys (B-116).")
    ap.add_argument("--send", action="store_true",
                    help="actually send the email (default is dry-run: print only).")
    ap.add_argument("--dealings", type=Path, default=None,
                    help="path to dealings.json (default: auto-detect the freshest).")
    ap.add_argument("--force", action="store_true",
                    help="send even if the data is stale (overrides the freshness guard).")
    args = ap.parse_args(argv)
    run(send=args.send, dealings_path=args.dealings, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
