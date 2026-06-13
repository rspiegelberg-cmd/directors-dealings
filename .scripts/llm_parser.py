"""Stage 2 LLM fallback parser — Anthropic API via stdlib urllib.

Called ONLY when the regex parser returns warnings. Sends the
HTML-stripped plain text + a focused prompt to Claude Sonnet and
expects a JSON object in the same shape as the regex extracted_dict.

Public surface:
    parse_with_llm(html, url, rns_id, announced_at, *, run_id=None,
                   model="claude-sonnet-4-6") -> (extracted_list, warnings)
    MissingApiKeyError, LLMParserError

Notes:
- No third-party packages. The call uses `urllib.request` against the
  documented REST endpoint `https://api.anthropic.com/v1/messages`.
- A tiny `.env` loader at import time reads project-root `.env` if
  present (very forgiving: blank lines and `#` comments are skipped).
- `parser_source` is always `'llm'` for writes coming from here, but
  this module returns a list of extracted dicts; the orchestrator is
  responsible for setting the column when writing.
- Cost is recorded via `llm_cost.record_call(...)` using
  `usage.input_tokens` and `usage.output_tokens` from the response.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

# Try to import parser helpers; fall back to no-op imports under tests.
try:
    from parse_pdmr import _fingerprint, html_to_text  # type: ignore
except ImportError:  # pragma: no cover -- only happens in odd test layouts
    _fingerprint = None
    html_to_text = None

try:
    import llm_cost  # type: ignore
except ImportError:  # pragma: no cover
    llm_cost = None


ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024


class MissingApiKeyError(Exception):
    """Raised when ANTHROPIC_API_KEY is unset.

    The orchestrator should treat this as a 'skip LLM, route to pending'
    rather than a hard crash.
    """


class LLMParserError(Exception):
    """Raised when the API response doesn't validate against the
    expected JSON shape, or the HTTP call itself fails.
    """


# --- .env loader (stdlib, ~10 lines) ---------------------------------------

def _load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            # Don't overwrite existing env vars.
            os.environ.setdefault(k, v)
    except OSError:
        pass


_load_env_file()


# --- Prompt -----------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are extracting a single UK PDMR (director dealing) transaction from
the plain-text body of an RNS filing. Return a JSON object with EXACTLY
these keys (do not invent extra keys; missing -> null):

  date            (ISO YYYY-MM-DD)
  ticker          (LSE bare ticker, no .L suffix; e.g. "CHH")
  company         (free-text company name)
  director        (PDMR full name)
  role            (PDMR role/position, or null)
  type            (one of: BUY, SELL, SELL_TAX, EXERCISE, GRANT, SIP)
  shares          (integer count)
  price           (price per share in GBP; pence -> pounds; 0 if grant)
  value           (price * shares, GBP, 2dp; 0 if grant)
  warnings        (array of free-form strings; [] when clean)

If the filing is a BUNDLED multi-PDMR notification, set every required
field to null and add "bundled_multi_PDMR" to warnings. Do not split.
If foreign currency, set price/value to 0 and add "foreign_currency".

Filing URL: {url}
RNS ID    : {rns_id}
Announced : {announced_at}

Body text (newline-separated, HTML stripped):
----
{body}
----

Respond with the JSON object only, no commentary.

Example clean response:
{{"date": "2026-04-27", "ticker": "CHH", "company": "Churchill China plc",
"director": "Jane Doe", "role": "CFO", "type": "BUY", "shares": 1000,
"price": 3.21, "value": 3210.0, "warnings": []}}
"""


def _build_prompt(body: str, url: str, rns_id: str, announced_at: str) -> str:
    # Truncate body to keep token usage bounded.
    capped = body[:8000]
    return _PROMPT_TEMPLATE.format(
        url=url, rns_id=rns_id, announced_at=announced_at, body=capped
    )


# --- HTTP call --------------------------------------------------------------

def _post_messages(
    api_key: str,
    prompt: str,
    model: str,
    timeout: int = 60,
) -> dict:
    """POST to the Anthropic Messages API.

    Single one-shot retry on transient network errors (URLError, generic
    connection-closed/reset/timeout). HTTPError is NOT retried because
    401/403/429 won't change on an immediate second attempt. The retry
    waits 2s, matching the pattern Rupert hit during the 2026-05-12
    backfill on rns_id 9513416 (WinError 10054: connection forcibly
    closed) where a single retry would have recovered it.
    """
    payload = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )

    last_transient: Exception | None = None
    for attempt in range(2):  # initial + one retry
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            # Auth/quota/server-side errors won't recover on retry.
            # Capture response body for diagnosis (Anthropic includes a JSON
            # error message that explains why the request was rejected).
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                pass
            raise LLMParserError(
                f"Anthropic API HTTP {e.code} {e.reason}: {err_body}"
            ) from e
        except urllib.error.URLError as e:
            last_transient = e
            if attempt == 0:
                time.sleep(2.0)
                continue
            raise LLMParserError(
                f"Anthropic API URLError after retry: {e.reason}"
            ) from e
        except (ConnectionError, TimeoutError, OSError) as e:
            # WinError 10054 surfaces as ConnectionResetError (subclass
            # of ConnectionError / OSError); treat as transient.
            last_transient = e
            if attempt == 0:
                time.sleep(2.0)
                continue
            raise LLMParserError(
                f"Anthropic API connection error after retry: {e}"
            ) from e
        except Exception as e:
            raise LLMParserError(f"Anthropic API call failed: {e}") from e

    # Defensive: loop exhausted without return/raise.
    raise LLMParserError(
        f"Anthropic API call failed after retry: {last_transient}"
    )


# --- Response validation ----------------------------------------------------

_VALID_TYPES = {"BUY", "SELL", "SELL_TAX", "EXERCISE", "GRANT", "SIP"}
_REQUIRED_FIELDS = ("date", "ticker", "director", "type", "shares")


def _extract_json_object(text: str) -> dict | None:
    """Pull the first {...} block from a model response."""
    if not text:
        return None
    # Direct parse if response is pure JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back: greedy {...} match
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _validate_and_normalise(
    obj: dict,
    url: str,
    announced_at: str,
) -> tuple:
    """Convert the model's JSON to the orchestrator's extracted_dict shape."""
    warnings = list(obj.get("warnings") or [])
    missing: list = []
    for k in _REQUIRED_FIELDS:
        v = obj.get(k)
        if v is None or v == "" or (k == "shares" and not int(v or 0)):
            missing.append(k)
    if missing:
        warnings.append(f"llm_missing_fields:{','.join(missing)}")
        return [], warnings

    tx_type = obj.get("type")
    if tx_type not in _VALID_TYPES:
        warnings.append(f"llm_invalid_type:{tx_type!r}")
        return [], warnings

    try:
        shares = int(obj["shares"])
    except (TypeError, ValueError):
        warnings.append("llm_invalid_shares")
        return [], warnings

    try:
        price = float(obj.get("price") or 0.0)
    except (TypeError, ValueError):
        price = 0.0

    try:
        value = float(obj.get("value") or 0.0)
    except (TypeError, ValueError):
        value = round(price * shares, 2) if (price and shares) else 0.0

    director = str(obj["director"]).strip()
    date = str(obj["date"]).strip()
    ticker = str(obj["ticker"]).strip().rstrip(".")
    company = str(obj.get("company") or "").strip()
    role = obj.get("role")
    if role is not None:
        role = str(role).strip() or None

    if _fingerprint is None:
        # Defensive: fingerprint helper must be present in production
        raise LLMParserError("parse_pdmr._fingerprint unavailable")
    fp = _fingerprint(date, ticker, director, tx_type, shares)

    extracted = {
        "fingerprint": fp,
        "date": date,
        "ticker": ticker,
        "company": company,
        "director": director,
        "role": role,
        "type": tx_type,
        "shares": shares,
        "price": price,
        "value": value,
        "context": None,
        "url": url,
        "announced_at": announced_at,
    }
    return [extracted], warnings


# --- Public entry -----------------------------------------------------------

def parse_with_llm(
    html: str,
    url: str,
    rns_id: str,
    announced_at: str,
    *,
    run_id: str | None = None,
    model: str = DEFAULT_MODEL,
) -> tuple:
    """Call Claude Sonnet to extract one PDMR transaction.

    Returns `(extracted_list, warnings_list)`. The orchestrator records
    the spend and writes `parser_source='llm'` for any rows that come
    back clean.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise MissingApiKeyError(
            "ANTHROPIC_API_KEY not set. Add it to .env or "
            "$env:ANTHROPIC_API_KEY before running."
        )

    if html_to_text is None:
        raise LLMParserError("parse_pdmr.html_to_text unavailable")

    body = html_to_text(html)
    prompt = _build_prompt(body, url, rns_id, announced_at)

    raw = _post_messages(api_key, prompt, model)

    # Record cost.
    usage = raw.get("usage") or {}
    in_tok = int(usage.get("input_tokens", 0))
    out_tok = int(usage.get("output_tokens", 0))
    if llm_cost is not None and (in_tok or out_tok):
        try:
            llm_cost.record_call(in_tok, out_tok, model, run_id=run_id)
        except Exception:
            pass  # ledger failure shouldn't break parsing

    # Extract text content.
    content_text = ""
    for block in raw.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            content_text += block.get("text", "")

    obj = _extract_json_object(content_text)
    if obj is None:
        return [], [f"llm_unparseable_response:{content_text[:120]!r}"]

    return _validate_and_normalise(obj, url, announced_at)
