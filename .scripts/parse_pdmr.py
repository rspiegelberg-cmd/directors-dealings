"""Stage 2 PDMR parser — table-aware extraction of UK director dealings.

Returns `(extracted_list, warnings_list, parser_source)` from
`parse_announcement(html, url, rns_id, announced_at)`. The
`parser_source` value is always `'regex'` here; the LLM parser writes
`'llm'` on its own writes.

Design contract (from spec 02 D1): the parser never silently
mis-attributes a transaction. Bundled multi-PDMR filings are refused
with an enriched warning that lists the named PDMRs and roles, rather
than fanned out per-person.

Sprint 3 (B-001 + B-004 + B-016 + B-017) — added table-aware
extraction. The parser now uses BeautifulSoup to recognise the
standard Investegate two-table layout: a key-value issuer/instrument
table plus a per-row transaction table. Multi-row bulk filings (DRIPs,
SIP / SAYE, year-end disclosures) now contribute every row, not just
the first. Director and company cells are read by cell-boundary
rather than by flat-text regex, eliminating the bleed-through that
produced 'Kingfisher plc\\nb' director names and the 'emission
allowance market participant' boilerplate in the company field.

If the table-aware path can't recognise the layout (foreign issuers,
SIP-specific variants, malformed HTML), the parser falls back to the
existing regex-based single-row extraction.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


# --- Regex helpers ----------------------------------------------------------

# Ordinal-aware embedded date matcher. Handles:
#   "27 April 2026", "27th April 2026", "April 27, 2026",
#   "April 27th, 2026", "2026-04-27", "27/04/2026", "27-04-2026",
#   "05.05.26", "05.05.2026"   (dot separator; some JSE/foreign issuers)
_EMBEDDED_DATE_RE = re.compile(
    r"\b(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})\b"
    r"|\b([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})\b"
    r"|\b(\d{4}-\d{2}-\d{2})\b"
    r"|\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b",
    re.IGNORECASE,
)

_DATE_FMTS = (
    "%d %B %Y",
    "%d %b %Y",
    "%B %d %Y",
    "%b %d %Y",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",   # dotted, 4-digit year ("05.05.2026")
    "%d.%m.%y",   # dotted, 2-digit year ("05.05.26"); strptime pivots at 68/69
)

# Captures monetary numbers with optional currency markers.
# GBP variants we coerce:  £1,234.56  £0.50  50p  GBp 50  50 GBp  1,234.5
# Foreign variants we detect-but-do-not-coerce: $, USD, EUR, €, CHF, JPY, ZAR
NUMBER_RE = re.compile(
    r"(?P<curr>£|\$|€|GBp|GBP|USD|EUR|CHF|JPY|ZAR)?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<post>p\b|pence\b|GBp\b|GBP\b|USD\b|EUR\b|CHF\b|JPY\b|ZAR\b)?",
    re.IGNORECASE,
)

# ZAR is also detected via a bare "R" prefix before digits (JSE filings
# use "R203.30" format without spelling out "ZAR").
_ZAR_PREFIX_RE = re.compile(r"\bR\d", re.IGNORECASE)

_FOREIGN_CURR_MARKERS = ("$", "USD", "EUR", "€", "CHF", "JPY", "ZAR")

# Bundled-PDMR markers (any-one fires).
#   - "Notification N of M"
#   - "PDMR Notification 1" / "PDMR 1" heading
#   - Two distinct "Details of the person..." sections
#   - A numbered name list under "Name(s)" (Schroders-style)
_BUNDLED_PDMR_RE = re.compile(
    r"(?:Notification\s+\d+\s+of\s+\d+)"
    r"|(?:PDMR\s*(?:Notification)?\s*[1-9]\b)"
    r"|(?:1\.\s*Details\s+of\s+the\s+person.*?2\.\s*Details\s+of\s+the\s+person)"
    r"|(?:Name\(s\)\s*\n\s*1\.[^\n\r]+\n\s*2\.)",
    re.IGNORECASE | re.DOTALL,
)

# Per-PDMR section heading inside bundled filings.
_PDMR_SECTION_RE = re.compile(
    r"(?:Notification\s+(\d+)\s+of\s+\d+|PDMR\s+(\d+))",
    re.IGNORECASE,
)

# Numbered-name list (Schroders pattern): "Name(s)\n1. Foo\n2. Bar"
_NUMBERED_NAMES_RE = re.compile(
    r"Name\(s\)\s*\n((?:\s*\d+\.\s*[^\n\r]+\n?)+)",
    re.IGNORECASE,
)
_NUMBERED_POSITIONS_RE = re.compile(
    r"Position(?:/status)?\s*\n((?:\s*\d+\.\s*[^\n\r]+\n?)+)",
    re.IGNORECASE,
)

# Type-classification keywords, ordered by specificity (first match wins).
_TYPE_KEYWORDS = (
    # SIP / share-incentive / sharesave (non-discretionary)
    ("SIP", re.compile(
        r"share\s+incentive\s+plan|SIP\s+trustee|"
        r"share\s*save|sharesave|partnership\s+shares|"
        r"matching\s+shares|free\s+shares|dividend\s+shares\s+under\s+the\s+SIP",
        re.IGNORECASE,
    )),
    # SELL_TAX
    ("SELL_TAX", re.compile(
        r"shares?\s+sold\s+to\s+cover\s+tax|"
        r"tax\s+withholding|"
        r"sale\s+to\s+cover\s+(?:tax|withholding)",
        re.IGNORECASE,
    )),
    # GRANT
    ("GRANT", re.compile(
        r"grant\s+of\s+(?:options?|share\s+options?|conditional\s+share\s+award)|"
        r"long\s*term\s+incentive\s+plan|"
        r"conditional\s+share\s+award|"
        r"performance\s+share\s+plan\s+grant|"
        r"award\s+of\s+(?:options?|conditional\s+shares)|"
        # B-092 (2026-06-03): broader grant patterns for incentive/bonus plan awards.
        # Matches "Grant of 2026 Deferred Bonus Share Awards", "Grant of Share Awards
        # under the 2016 Bodycote Incentive Plan", etc.  Requires the word "awards?" to
        # follow 0-6 qualifying words so it doesn't over-fire on bare "grant of shares".
        r"grant\s+of\s+(?:(?:\w+\s+){1,6})?(?:share\s+)?awards?",
        re.IGNORECASE,
    )),
    # EXERCISE
    ("EXERCISE", re.compile(
        r"exercise\s+of\s+options?|"
        r"option\s+exercise|"
        r"vesting\s+of\s+(?:rsus?|share\s+awards?)|"
        r"\bTVR\b|"
        # B-092 (2026-06-03): additional non-discretionary event patterns that the
        # holding pen audit surfaced in PRN filings.  Each addition is validated
        # against the positive set (ZigUp 9555699, Capita 8883488, etc.) and the
        # negative set (genuine on-market purchases) to confirm no false promotions.
        r"employee\s+benefit\s+trust|"        # EBT transfers: "Transfer of shares from EBT"
        r"\brsus?\b|"                          # RSU vesting in any form (TBS RSUs, MSCI RSUs, etc.)
        r"nil[- ]?cost\s+(?:share\s+)?options?|"  # nil-cost options: broader than singular `nil-cost option`
        r"allocation\s+of\s+(?:bonus\s+|deferred\s+)?shares?\s+under",  # plan allocations
        re.IGNORECASE,
    )),
    # SELL
    # NOTE: classification is now scoped to the "Nature of the transaction"
    # cell / bounded tx block (not whole-page text), so bare \bsale\b is safe
    # here and no longer collides with page-chrome ("...asset disposal...").
    # SELL_TAX is matched earlier in this tuple, so "sale to cover tax" is
    # claimed before these patterns. Covers noun-first ("share sale"),
    # number-infixed ("sale of 77,000 ordinary shares") and on-market phrasings.
    ("SELL", re.compile(
        r"disposal\s+of\s+(?:shares?|ordinary\s+shares?)|"
        r"sale\s+of\s+(?:[\d,]+\s+)?(?:ordinary\s+)?shares?|"
        r"\bshare\s+sale\b|"
        r"on[-\s]?market\s+sale|"
        r"\bdisposal\b|"
        r"\bsold\b|"
        r"\bsale\b",
        re.IGNORECASE,
    )),
    # BUY (last — fallback for "acquisition" / "purchase")
    ("BUY", re.compile(
        r"acquisition\s+of\s+(?:shares?|ordinary\s+shares?)|"
        r"purchase\s+of\s+shares?|"
        r"\bacquisition\b|"
        r"\bpurchased?\b|"
        r"market\s+purchase",
        re.IGNORECASE,
    )),
)


# --- HTML -> text -----------------------------------------------------------

class _TextExtractor(HTMLParser):
    """Strip HTML, preserving paragraph and cell breaks."""

    BLOCK_TAGS = {
        "p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "header", "footer",
    }
    CELL_TAGS = {"td", "th"}
    SKIP_TAGS = {"script", "style", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self._parts.append("\n")
        elif tag in self.CELL_TAGS:
            self._parts.append("\t")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        # Collapse blank lines, normalise whitespace per line
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def html_to_text(html: str) -> str:
    """Strip HTML to plain text, preserving paragraph + cell separators."""
    p = _TextExtractor()
    p.feed(html)
    p.close()
    return p.text()


# --- Date parsing -----------------------------------------------------------

def _try_one_date(s: str) -> str | None:
    """Try every format in _DATE_FMTS; return ISO date or None."""
    s = s.strip().rstrip(".,;")
    # Strip ordinal suffix on day-of-month before strptime
    s = re.sub(r"(\d{1,2})(?:st|nd|rd|th)\b", r"\1", s, flags=re.IGNORECASE)
    s = s.replace(",", "")
    s = re.sub(r"\s+", " ", s)
    for fmt in _DATE_FMTS:
        try:
            parsed = datetime.strptime(s, fmt)
        except ValueError:
            continue
        # B-005: defence-in-depth -- 2-digit-year formats (%d.%m.%y) cause
        # Python's strptime to pivot at 68/69, so "69" -> 1969. Reject any
        # parsed year before 1990; nothing in PDMR data legitimately
        # predates the regime.
        if parsed.year < 1990:
            print(
                f"WARN: rejected suspicious date with year {parsed.year}: {s!r}",
                file=sys.stderr,
            )
            return None
        return parsed.strftime("%Y-%m-%d")
    return None


# Explicit "Date of the transaction" label.
#
# Capture up to 200 chars AFTER the label, INCLUDING newlines (DOTALL).
# Some Investegate templates put the value in a separate <td> cell that
# ends up several lines below the label after html_to_text() strips the
# tags. Examples:
#   "Date of the transaction\n\xa0\n\n05.05.26"        (cross-cell layout)
#   "Date of transaction\n5 May 2026"                  (Mondi-style)
# A 200-char window is wide enough for both yet narrow enough to avoid
# bleeding into unrelated fields.
_TX_DATE_LABEL_RE = re.compile(
    r"Date\s+of\s+(?:the\s+)?transaction\s*[:\-]?\s*(.{1,200})",
    re.IGNORECASE | re.DOTALL,
)


def parse_iso_date(text: str) -> str | None:
    """Find the transaction date.

    Requires an explicit "Date of [the] transaction" label match followed
    by a parseable date in the next ~200 chars. Returns None when the
    label is missing or no parseable date follows -- the caller emits
    `could_not_parse_tx_date` so the filing goes to pending review.

    The legacy "latest-wins fallback" (return max date anywhere in the
    document) has been REMOVED. It produced wrong transaction dates by
    silently picking up option expiries, AGM dates, or page-footer
    timestamps. Failing loudly is safer than failing silently.
    """
    if not text:
        return None
    m = _TX_DATE_LABEL_RE.search(text)
    if not m:
        return None
    for em in _EMBEDDED_DATE_RE.finditer(m.group(1)):
        raw = next((g for g in em.groups() if g), None)
        if not raw:
            continue
        iso = _try_one_date(raw)
        if iso:
            return iso
    return None


# --- Bundled-PDMR detection -------------------------------------------------

# Pattern for harvesting bundled names + roles. Names appear in the
# "Details of the person discharging managerial responsibilities" block,
# usually with explicit "Name:" + "Position:" labels.
_NAME_ROLE_RE = re.compile(
    r"Name[s]?\s*[:\-]\s*([^\n\r]{2,80})"
    r"(?:.{0,400}?Position[s]?\s*[:\-]\s*([^\n\r]{2,80}))?",
    re.IGNORECASE | re.DOTALL,
)


def _bundled_name_warning(text: str) -> str | None:
    """If `text` is a bundled multi-PDMR filing, return an enriched
    warning naming each numbered PDMR.  Else return None.
    """
    # Three layouts can carry a bundle:
    #   - Investegate-style numbered name list (Schroders pattern), or
    #   - Multiple "Details of the person discharging managerial
    #     responsibilities" sections without the "1./2." prefixes, or
    #   - Multiple "Details of PDMR / person closely associated"
    #     sections (the abbreviation Anglo American and others use in
    #     their MAR Article 19 disclosures — same structure as the
    #     long-form heading, different wording).
    #
    # B-023 (2026-05-19): AAL filing 8950385 mixes the LONG form on
    # section 1 with the SHORT form ("Details of PDMR / PCA") on
    # sections 2-3. The previous regex only matched the long form,
    # so multi_details=1 and the detector silently let the filing fan
    # out into a single-row mis-extraction. Adding `PDMR\s*/\s*PCA\b`
    # picks up the short form too. \b on the PCA end avoids
    # false-positive matches like "PCAs" or "PCApplications".
    multi_details = len(re.findall(
        r"Details\s+of\s+(?:the\s+)?(?:"
        r"person\s+discharging\s+managerial\s+responsibilities"
        r"|PDMR\s*/\s*person\s+closely\s+associated"
        r"|person\s+closely\s+associated"
        r"|PDMR\s*/\s*PCA\b"
        r")",
        text, re.IGNORECASE,
    )) >= 2
    if not _BUNDLED_PDMR_RE.search(text) and not multi_details:
        return None

    found: list = []
    seen = set()

    # Pattern A: Schroders-style numbered name list under "Name(s)"
    nm_block = _NUMBERED_NAMES_RE.search(text)
    if nm_block:
        names = re.findall(r"\s*\d+\.\s*([^\n\r]+)", nm_block.group(1))
        # Try to pair with corresponding positions if present
        pos_block = _NUMBERED_POSITIONS_RE.search(text)
        positions: list = []
        if pos_block:
            positions = re.findall(r"\s*\d+\.\s*([^\n\r]+)", pos_block.group(1))
        for i, name in enumerate(names):
            name = name.strip().rstrip(".,")
            role = (positions[i].strip().rstrip(".,") if i < len(positions) else "unknown role")
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                found.append(f"{name} ({role})")

    # Pattern B: "Name: X\nPosition: Y" labelled pairs (per-section in
    # "Notification N of M" filings)
    if not found:
        for nm, rl in _NAME_ROLE_RE.findall(text):
            name = nm.strip().rstrip(".,")
            role = (rl or "").strip().rstrip(".,") or "unknown role"
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                found.append(f"{name} ({role})")

    if not found:
        return "bundled multi-PDMR filing — names not extractable from boilerplate"
    return "bundled multi-PDMR filing — names: [" + ", ".join(found) + "]"


# --- Ticker + director extraction -------------------------------------------

_TICKER_HEADLINE_RE = re.compile(r"\(([A-Z]{2,5}\.?)\)")
_TICKER_TIDX_RE = re.compile(r"\bTIDM[:\s]+([A-Z]{2,5}\.?)\b")
# Investegate URL slug: /announcement/(rns|prn)/<company-slug>--<ticker-slug>/<headline>/<rns_id>
_TICKER_URL_RE = re.compile(
    r"/announcement/(?:rns|prn)/[a-z0-9-]+?--([a-z][a-z0-9]{0,5}\.?)/",
    re.IGNORECASE,
)
# Investegate /company/TICKER link present on every filing page.
_TICKER_COMPANY_LINK_RE = re.compile(r"/company/([A-Z][A-Z0-9.]{1,5})\b")
# Bracketed-ticker false-positive blacklist (currencies, country codes, jargon).
_TICKER_BLACKLIST = frozenset({
    'EUR', 'USD', 'GBP', 'CHF', 'JPY', 'PLC', 'LTD',
    'UK', 'US', 'EU', 'AIM', 'LSE', 'TIDM', 'LEI',
    'PDMR', 'RNS', 'PRN', 'GBp', 'CAD', 'AUD', 'SGD',
    'HKD', 'NZD', 'SEK', 'NOK', 'DKK', 'LSEG', 'ISIN',
})
# Body bracketed-ticker — only fire when preceded by a word character (e.g. "Barclays (BARC)").
_TICKER_BODY_BRACKET_RE = re.compile(r"[A-Za-z]\s*\(([A-Z]{2,5}\.?)\)")


def _extract_ticker(
    text: str,
    headline: str | None = None,
    url: str | None = None,
    html: str | None = None,
) -> str | None:
    """Multi-source ticker extraction, in descending order of reliability.

    1. URL slug (canonical for Investegate)
    2. /company/TICKER link in body HTML/text
    3. Headline bracketed (TICKER), filtered by blacklist
    4. Body bracketed (TICKER) adjacent to a word, filtered by blacklist
    5. TIDM: legacy fallback
    """
    # 1. URL slug (canonical for Investegate; preserve trailing dot e.g. RR., NG.)
    if url:
        m = _TICKER_URL_RE.search(url)
        if m:
            return m.group(1).upper()

    # 2. /company/TICKER link
    haystack = html if html else (text or "")
    m = _TICKER_COMPANY_LINK_RE.search(haystack)
    if m:
        cand = m.group(1).upper()
        if cand not in _TICKER_BLACKLIST:
            return cand

    # 3. Headline bracketed
    if headline:
        m = _TICKER_HEADLINE_RE.search(headline)
        if m:
            cand = m.group(1).upper().rstrip(".")
            if cand not in _TICKER_BLACKLIST:
                # preserve trailing dot if present in original
                return m.group(1).upper() if m.group(1).endswith(".") else cand

    # 4. Body bracketed — must be adjacent to a word character (CompanyName style).
    body = text or ""
    for bm in _TICKER_BODY_BRACKET_RE.finditer(body):
        cand_raw = bm.group(1)
        cand = cand_raw.upper().rstrip(".")
        if cand and cand not in _TICKER_BLACKLIST:
            return cand_raw.upper() if cand_raw.endswith(".") else cand

    # 5. TIDM: legacy fallback
    m = _TICKER_TIDX_RE.search(body)
    if m:
        return m.group(1).rstrip(".")

    return None


# Company / issuer name. Patterns:
#   "Issuer: Foo PLC"
#   "Name of the issuer: Foo PLC"
#   Within "Details of the issuer" section: "Name\nFoo PLC"
_COMPANY_LINE_RE = re.compile(
    r"(?:Issuer|Name\s+of\s+(?:the\s+)?issuer)\s*[:\-]?\s*\n?\s*([^\n\r]{2,120})",
    re.IGNORECASE,
)
# Catches the "Details of the issuer ... a) Name\nFoo PLC" form
_COMPANY_ISSUER_SECTION_RE = re.compile(
    r"Details\s+of\s+the\s+issuer.*?Name\s*\n([^\n\r]{2,120})",
    re.IGNORECASE | re.DOTALL,
)

# B-016 — sentinel boilerplate strings that must NEVER appear as a
# resolved company name. These come from the MAR Article 19 regulatory
# disclosure block ("Details of the issuer, emission allowance market
# participant, auction platform, auctioneer or auction monitor"). The
# old regex-on-flat-text extractor anchored on "issuer" and captured
# the rest of that label, producing ~30 mis-classified company values.
_COMPANY_BOILERPLATE_RE = re.compile(
    r"emission\s+allowance\s+market\s+participant|"
    r"auction\s+platform|"
    r"auctioneer|"
    r"auction\s+monitor",
    re.IGNORECASE,
)


def _is_boilerplate_company(name: str | None) -> bool:
    """True if the candidate company name is the MAR Article 19 boilerplate."""
    if not name:
        return False
    return bool(_COMPANY_BOILERPLATE_RE.search(name))


def _extract_company(text: str, headline: str | None = None) -> str | None:
    m = _COMPANY_ISSUER_SECTION_RE.search(text or "")
    if m:
        cand = m.group(1).strip().rstrip(".,")
        if not _is_boilerplate_company(cand):
            return cand
    m = _COMPANY_LINE_RE.search(text or "")
    if m:
        cand = m.group(1).strip().rstrip(".,")
        if not _is_boilerplate_company(cand):
            return cand
    if headline:
        bracket = headline.find("(")
        if bracket > 0:
            cand = headline[:bracket].strip().rstrip("-").strip()
            if cand and not _is_boilerplate_company(cand):
                return cand
    return None


# Director name. Two layouts:
#   "Name: Taalib Shaah"            (single-line)
#   "Name\nTaalib Shaah"            (label on its own line; value below)
_DIRECTOR_NAME_RE = re.compile(
    r"\bName\b\s*[:\-]?\s*\n?\s*([A-Z][A-Za-z'\-\.\s]{1,79})",
)
# Position/status: same two layouts.
_DIRECTOR_POSITION_RE = re.compile(
    r"Position\s*(?:/?\s*Status)?\s*[:\-]?\s*\n?\s*([^\n\r]{2,120})",
    re.IGNORECASE,
)


def _extract_director(text: str) -> tuple:
    """Return (name, role). Either may be None.

    Sprint 11 Fix #4: role is run through ``_validate_role_cell`` to
    reject prose-bleed captures (the legacy regex path is the source of
    all 25 bad role rows currently in the live DB).
    """
    name = None
    role = None
    m = _DIRECTOR_NAME_RE.search(text or "")
    if m:
        name = m.group(1).strip().rstrip(".,")
    m = _DIRECTOR_POSITION_RE.search(text or "")
    if m:
        role = _validate_role_cell(m.group(1).strip().rstrip(".,"))
    return name, role


# --- Type classification ----------------------------------------------------

def _classify_type(text: str) -> tuple:
    """Return (type, warnings_list)."""
    if not text:
        return None, ["could_not_classify_type"]
    for label, rgx in _TYPE_KEYWORDS:
        if rgx.search(text):
            return label, []
    return None, ["could_not_classify_type"]


# Flat-text "Nature of the transaction" cell/line recogniser, used by the
# legacy regex path so it can classify the SAME scoped text the table-aware
# path uses (via _find_kv_in_soup / _LABEL_NATURE_RE) — never whole-page text.
# Captures up to ~200 chars after the label, on the same line OR the next
# line (the PDF-rendered Investegate pages routinely break label/value).
_NATURE_TEXT_RE = re.compile(
    r"Nature\s+of\s+(?:the\s+)?(?:transaction|deal)"
    r"\s*[:\-]?\s*(?:\n\s*)?([^\n\r]{1,200})",
    re.IGNORECASE,
)


def _scoped_nature_text(text: str) -> str | None:
    """Return the 'Nature of the transaction' value from flat page text.

    Phase 3 (2026-06-02): the legacy fallback path used to feed the WHOLE
    stripped page to `_classify_type`, so a stray word in the Investegate
    news ticker / sidebar ("…asset disposal…") flipped a buy into a sell
    (JMAT/GEN/UTL/CAD on 1–2 Jun). This scopes classification to the
    transaction's own nature cell, mirroring the table-aware path at the
    `_find_kv_in_soup(soup, _LABEL_NATURE_RE)` call site.

    Returns the captured cell text, or None if no nature label is present.
    """
    if not text:
        return None
    m = _NATURE_TEXT_RE.search(text)
    if m:
        return m.group(1).strip()
    return None


# Anchors that mark the start of the transaction-detail region in flat text.
# Used to carve a tightly-bounded block when no explicit "Nature" cell exists,
# so type classification never sees the page header/footer/news-ticker chrome.
_TX_BLOCK_ANCHOR_RE = re.compile(
    r"(?:Details\s+of\s+(?:the\s+)?(?:transaction|PDMR)"
    r"|Description\s+of\s+the\s+financial\s+instrument"
    r"|Date\s+of\s+(?:the\s+)?transaction"
    r"|Price\s*\(s\)\s+and\s+volume\s*\(s\))",
    re.IGNORECASE,
)
_TX_BLOCK_MAX_CHARS = 600


def _bounded_tx_block(text: str) -> str | None:
    """Return a tightly-bounded slice of `text` around the transaction detail.

    Fallback for the legacy path when no explicit 'Nature of the transaction'
    cell is present. Anchors on the first transaction-detail label and returns
    a capped window starting there, so a type keyword in unrelated page chrome
    (sidebar headlines, footer 'related disposals' lists) can't be classified.
    Returns None if no anchor is found (caller then passes None →
    'could_not_classify_type', which is correctly BLOCKING).
    """
    if not text:
        return None
    m = _TX_BLOCK_ANCHOR_RE.search(text)
    if not m:
        return None
    start = m.start()
    return text[start:start + _TX_BLOCK_MAX_CHARS]


# --- Buy-strictness classification (Sprint 13) ------------------------------
# Patterns that indicate a non-discretionary event: vesting, LTIP, DRIP, SIP,
# Sharesave/SAYE, RSP, PSP, nil-cost exercise, grant, etc.
_NON_BUY_RE = re.compile(
    r"vesting\s+of\s+an\s+award"
    r"|restricted\s+share\s+plan|\bRSP\b"
    r"|long\s+term\s+incentive\s+plan|\bLTIP\b"
    r"|performance\s+share\s+plan|\bPSP\b"
    r"|deferred\s+bonus\s+plan"
    r"|sharesave|\bSAYE\b|save[- ]as[- ]you[- ]earn"
    r"|scrip\s+dividend|dividend\s+reinvestment|\bDRIP\b"
    r"|share\s+incentive\s+plan|\bSIP\b"
    r"|grant\s+of\s+(?:a\s+|an\s+|conditional\s+)?(?:award|options?)"
    r"|grant\s+of\s+conditional"
    r"|exercise\s+of\s+options?"
    r"|nil[- ]cost\s+option"
    r"|\bvested\b"
    r"|award\s+of\s+\d+"
    r"|employee\s+benefit\s+trust"
    # --- comp-event additions (2026-06-03; auditor-confirmed) -------------
    # Each token below is justified by a confirmed example in
    # docs/audits/reparse-buy-insert-verification_2026-06-03.md and proven on
    # the negative set (genuine on-market buys) not to demote them. The risk
    # the _STRICT_BUY_RE comment warns about ("acquisition of shares" describes
    # vestings) is handled there; here we match plan-SPECIFIC tokens only, not
    # the generic verb.
    #
    # Deferred Bonus Plan ("DBP") share purchases — BAE 9490111
    # ("Purchase of deferred shares under the DBP").
    r"|\bDBP\b|deferred\s+bonus|deferred\s+shares?"
    # Bonus Deferral Award — Unilever 9555689 ("grant of Bonus Deferral
    # Award" / "Purchase of Bonus Deferral Award forfeitable shares").
    r"|bonus\s+deferral|forfeitable\s+shares?"
    # Dividend-accrual / scrip in lieu of dividend — BATS 8871006, PRU 8890342
    # ("...dividend equivalent shares...", "...dividends accruing to deferred
    # share awards"). Generic "scrip" and "in lieu of dividend" forms too.
    r"|\bscrip\b|in\s+lieu\s+of\s+(?:a\s+)?dividend"
    r"|dividend(?:s)?\s+(?:accruing|equivalent)"
    # All-employee plan auto-purchases: SIP partnership/matching/free shares,
    # ESPP, generic "share purchase plan" — HAS 9517985 (US Employee Stock
    # Purchase Plan), PRU 9566410 (All Employee Share Purchase Plan),
    # RR 9467833 (share purchase plan for NEDs). NOTE these literally say
    # "Purchase of shares", so they fire _STRICT_BUY_RE; matching here demotes
    # them to MIXED (gated out of signals).
    r"|\bESPP\b|employee\s+stock\s+purchase\s+plan"
    r"|share\s+purchase\s+plan"
    r"|partnership\s+shares?|matching\s+shares?|free\s+shares?"
    # B-092 (2026-06-03): keep _NON_BUY_RE in sync with new EXERCISE
    # additions above so buy_strictness classification agrees with type.
    r"|employee\s+benefit\s+trust"   # EBT transfers
    r"|\brsus?\b"                     # RSU vesting
    r"|nil[- ]?cost\s+(?:share\s+)?options?"  # nil-cost options (broader)
    r"|allocation\s+of\s+(?:bonus\s+|deferred\s+)?shares?\s+under",  # plan allocation
    re.IGNORECASE,
)

# Patterns that indicate a genuine discretionary on-market purchase.
# The optional (?:\s+[\d,]+)? allows for "purchase of 5,000 shares"
# in addition to "purchase of shares" and "purchase of ordinary shares".
#
# B-093 (Sprint 20): the "Nature of the transaction" cell frequently reads
# just "Purchase" / "PURCHASE", or "Purchase of 345 PLC shares" (a company
# word sits between the number and "shares"), or the prose form
# "purchased 33,657 ordinary shares". The original regex required the literal
# "purchase of [ordinary] [N] shares" and so tagged all of these UNKNOWN,
# which the signal gate then suppressed (285 buys, incl. UTL/ULVR/LGEN/ABF/
# IMB NED buys). The patterns below recognise those real forms. Safe because
# the three table-aware call sites pass only the short scoped nature cell;
# the _NON_BUY_RE check still demotes vesting/LTIP/SIP dressed as a purchase
# to MIXED.
#
# IMPORTANT (Sprint 21 test gate): only "purchase"/"purchased" count as a
# discretionary buy — NOT "acquisition"/"acquired". UK PDMR boilerplate routinely
# describes a vesting/award as an "acquisition of shares" (e.g. "Acquisition of
# shares following the vesting of an award under the RSP"), so treating
# "acquisition" as strict-buy mis-labels vestings. The (?:\s+[\w.,&]+){0,4}?
# tolerates a number-with-commas, an "ordinary"/company qualifier, etc. between
# the verb and "shares" (e.g. "purchase of 5,000 PLC shares").
_STRICT_BUY_RE = re.compile(
    # phrase form: "purchase of [ordinary] [N,NNN] [company] shares"
    r"(?:on[- ]market\s+)?purchase\s+of(?:\s+[\w.,&]+){0,4}?\s+shares?"
    # prose verb form ("has purchased 33,657 ordinary shares")
    r"|\bpurchased\b(?:\s+[\w.,&]+){0,4}?\s+shares?"
    # bare nature-cell token: the cell is essentially just "Purchase"
    r"|^\s*(?:on[- ]market\s+)?purchase\b",
    re.IGNORECASE | re.MULTILINE,
)


def _classify_buy_strictness(text: str) -> str:
    """Classify the nature-of-transaction text into a buy_strictness label.

    Returns one of: STRICT_BUY | NON_BUY_ONLY | MIXED | UNKNOWN.

    STRICT_BUY   — on-market purchase language, no non-buy markers.
    NON_BUY_ONLY — vesting/LTIP/DRIP/SIP/RSP/PSP/etc.; not discretionary.
    MIXED        — both buy and non-buy language present (needs review).
    UNKNOWN      — no matching patterns found.

    Called at parse time so every new transaction row carries this label.
    Existing rows are backfilled by reparse_corpus.py reading cached HTML.
    """
    if not text:
        return "UNKNOWN"
    is_non_buy = bool(_NON_BUY_RE.search(text))
    is_strict = bool(_STRICT_BUY_RE.search(text))
    if is_strict and is_non_buy:
        return "MIXED"
    if is_strict:
        return "STRICT_BUY"
    if is_non_buy:
        return "NON_BUY_ONLY"
    return "UNKNOWN"


# --- Price + volume extraction ----------------------------------------------

# Labels for price + volume.
# Tolerates value-on-same-line ("Price: £4.316") AND value-on-next-line
# ("Price(s)\n£4.316 per Share"). The Investegate RNS PDF-rendered
# pages routinely put labels and values on separate lines.
_PRICE_LABEL_RE = re.compile(
    r"(?:Price\(s\)|Price\s+per\s+share|Unit\s+price|\bPrice\b)"
    r"\s*[:\-]?\s*(?:\n\s*)?([^\n\r]{1,80})",
    re.IGNORECASE,
)
_VOLUME_LABEL_RE = re.compile(
    r"(?:Volume\(s\)|Number\s+of\s+shares|Aggregate\s+volume|\bVolume\b|\bShares?\b)"
    r"\s*[:\-]?\s*(?:\n\s*)?([^\n\r]{1,80})",
    re.IGNORECASE,
)


# "Price(s)\nVolume(s):\n<price>\n<volume>" or
# "Price (s)\nVolume (s)\n<price>\n<volume>" — note optional space
# between "Price"/"Volume" and "(s)".
_PRICE_VOLUME_TABLE_RE = re.compile(
    r"Price\s*\(s\)\s*\n\s*Volume\s*\(s\)\s*[:\-]?\s*\n\s*"
    r"([^\n\r]{1,120})\s*\n\s*([^\n\r]{1,120})",
    re.IGNORECASE,
)


def _parse_price_vol(text: str) -> tuple:
    """Return (price_gbp, shares, warnings).

    Pence -> pounds conversion (`50p` -> 0.50). Foreign currency detected
    and returned as (0, 0, ['foreign_currency']).
    """
    if not text:
        return 0.0, 0, ["no_numeric_values"]

    warnings: list = []

    # Special-case: the "Price(s)\nVolume(s):\n<price>\n<volume>" table
    # layout that Investegate uses for the canonical RNS template.
    tbl = _PRICE_VOLUME_TABLE_RE.search(text)
    if tbl:
        # Synthesise label-matches against the captured groups so the
        # downstream loops keep their existing shape.
        price_match = type("M", (), {"group": lambda self, i: tbl.group(1) if i == 1 else None})()  # noqa: E501
        vol_match = type("M", (), {"group": lambda self, i: tbl.group(2) if i == 1 else None})()
    else:
        price_match = _PRICE_LABEL_RE.search(text)
        vol_match = _VOLUME_LABEL_RE.search(text)

    # Foreign-currency detect: only inside the Price block, not the
    # whole document (the page often has unrelated EUR/USD instrument
    # mentions in the footer / "related filings" list).
    # Also catch South African Rand via bare "R<digit>" prefix (JSE filings
    # write "R203.30" without spelling out "ZAR").
    if price_match:
        price_block = price_match.group(1)
        price_block_upper = price_block.upper()
        if _ZAR_PREFIX_RE.search(price_block):
            warnings.append("foreign_currency")
        else:
            for marker in _FOREIGN_CURR_MARKERS:
                if marker in price_block_upper:
                    warnings.append("foreign_currency")
                    break

    distinct_prices: set = set()
    price_gbp = 0.0
    shares = 0

    if price_match:
        block = price_match.group(1)
        for m in NUMBER_RE.finditer(block):
            num_str_raw = m.group("num")
            num_str = num_str_raw.replace(",", "")
            try:
                val = float(num_str)
            except ValueError:
                continue
            # D.2 (Sprint 9 Phase B) — reject candidates that look like
            # a total-consideration figure (£15,040) rather than a
            # per-share price. A real per-share UK price > £1000 is
            # vanishingly rare and never has a thousands comma in its
            # raw match (high-priced shares are denominated in pence,
            # e.g. NXT 9000p — no comma at the pence resolution).
            if val > 1000.0 and "," in num_str_raw:
                continue
            curr = (m.group("curr") or "").lower()
            post = (m.group("post") or "").lower()
            if curr in {"$", "usd", "eur", "€", "chf", "jpy", "zar"} or post in {"usd", "eur", "chf", "jpy", "zar"}:
                continue  # already flagged
            if post in {"p", "pence"} or curr == "gbp" and post == "p":
                price_gbp_local = val / 100.0
            elif curr.lower() == "gbp" or curr == "£":
                price_gbp_local = val
            else:
                # Bare number — no £, no p, no foreign marker. Treated as
                # pounds (original behaviour). NOTE: a reworked pence-detection
                # fix (Fix 2) is deferred — see
                # docs/specs/parser-fix-comp-events-and-pence-2026-06-03.md
                # task #11. The naive "bare >= 1.0 => pence" rule was reverted
                # because it corrupted genuine pound-quoted buys stored bare
                # (e.g. Shell 9580273: £2.7m -> £0.3252). A robust fix must
                # reconcile price x shares against the prose-stated total.
                price_gbp_local = val
            distinct_prices.add(round(price_gbp_local, 6))
            price_gbp = price_gbp_local

    if vol_match:
        block = vol_match.group(1)
        for m in NUMBER_RE.finditer(block):
            num_str = m.group("num").replace(",", "")
            try:
                val = float(num_str)
            except ValueError:
                continue
            # Whole-share count is an int >= 1
            if not (val >= 1 and abs(val - int(val)) < 1e-6):
                continue
            int_val = int(val)
            # C.2 — reject par-value pence notation.
            _post = (m.group("post") or "").lower()
            if _post in {"p", "pence"}:
                continue
            # D.2 (Sprint 9 Phase B) — reject date-component candidates.
            # `\bShares?\b` in _VOLUME_LABEL_RE can match narrative text
            # like "...acquired 1,615 ordinary shares on 19 May 2026...",
            # grabbing "19" (the day) as the volume. Three rejection
            # triggers — see _looks_like_date_bleed.
            if _looks_like_date_bleed(int_val, block, price_gbp):
                continue
            shares = int_val
            break

    if len(distinct_prices) > 1:
        warnings.append("multiple_distinct_prices")
        return 0.0, 0, warnings

    if price_gbp == 0.0 and shares == 0:
        warnings.append("could_not_separate_price_volume")

    return price_gbp, shares, warnings


def _looks_like_date_bleed(val: int, block: str, price_gbp: float) -> bool:
    """D.2 (Sprint 9 Phase B) — return True if `val` is implausibly a
    real share count and far more likely a date-component bleed.

    Three triggers (any-one fires):
      1. val in 1..31 AND a month word appears anywhere in the block
         AND we have no companion price yet (price_gbp < £1). A real
         "19 shares" volume almost always has a real labelled price.
      2. val in 1990..2099 AND val is the only integer in the block.
         A genuine "1990 shares" volume usually appears alongside a
         labelled price; isolated year-only integers are almost
         certainly a date-matcher miss.
      3. val < 10 AND price_gbp == 0.0. A tiny share count with no
         companion price almost certainly grabbed a date fragment
         ("on 5 May...") rather than a real volume.
    """
    # Trigger 1: day-of-month + month word + no companion price
    if 1 <= val <= 31 and _MONTH_WORD_RE.search(block):
        if price_gbp < 1.0:
            return True
    # Trigger 2: 4-digit year (HARD GUARD).
    # Year-as-shares refix (2026-05-31, Step A): a value in 1990..2099 is
    # rejected UNCONDITIONALLY. The previous Sprint-11 logic only rejected a
    # year-like integer when it was the *only* integer in the block — an
    # escape hatch that prose ("…purchased 127,083 Ordinary Shares … 2026")
    # always slipped through, because the 127,083 (or other tokens) counted
    # as "other integers" and let `2026` be accepted as the volume. A bare
    # 4-digit year is overwhelmingly a date bleed; the vanishingly rare
    # genuine ~2,0xx-share holding is better surfaced via pending-review
    # (the _plausibility_check year guard routes it there) than silently
    # trusted as a volume. See docs/specs/year-as-shares-refix-plan-2026-05-31.md.
    if 1990 <= val <= 2099:
        return True
    # Trigger 3: tiny share count with no companion price
    if val < 10 and price_gbp == 0.0:
        return True
    return False


# --- Fingerprint ------------------------------------------------------------

def _fingerprint(date: str, ticker: str, director: str, tx_type: str, shares: int) -> str:
    """Build the stable natural key matching legacy refresh.py.

    Shape: `date|ticker|director|type|shares` -> sha1 hex (first 16 chars).
    """
    raw = f"{date}|{ticker}|{director}|{tx_type}|{shares}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# --- Table-aware extraction (Sprint 3, B-001 + B-004 + B-016 + B-017) ------
#
# Investegate's standard RNS PDMR layout has two HTML tables:
#
#   Table 0 — issuer / instrument key-value pairs
#       row: ['Name', 'National Grid plc']
#       row: ['LEI',  '8R95QZMKZLJX5Q2XR704']
#       row: ['Nature of the transaction', 'Acquisition of shares ...']
#
#   Table 1 — per-transaction rows
#       header: ['Date of transaction', 'Name', 'Position / status',
#                'Price (s)', 'Volume (s)', 'Aggregated information']
#       row1:   ['2024-08-13', 'Jacqueline Agg', '...', '£9.8933',  '51', ...]
#       row2:   ['2025-02-12', ...]
#       ...
#
# The old regex-on-flat-text path could not distinguish cells in either
# table and would (a) only emit one transaction row per filing (B-001),
# (b) accidentally concatenate the company name with the next field
# label (B-004 — "Kingfisher plc\\nb"), and (c) capture regulatory
# boilerplate from the MAR Article 19 section header into the company
# field (B-016).

# Column-header recognisers for the transaction table.
_HDR_DATE_RE = re.compile(r"date\s+of\s+(?:the\s+)?transaction|^\s*date\s*$", re.IGNORECASE)
_HDR_NAME_RE = re.compile(r"^\s*name(?:\(s\))?\s*$|^\s*pdmr\s+name\s*$", re.IGNORECASE)
_HDR_POSITION_RE = re.compile(r"position|status|role", re.IGNORECASE)
_HDR_PRICE_RE = re.compile(r"price", re.IGNORECASE)
_HDR_VOLUME_RE = re.compile(
    r"volume|number\s+of\s+shares?|^\s*shares?\s*(?:\(s\))?\s*$|aggregate(?:d)?\s+volume",
    re.IGNORECASE,
)

# Sprint 11 Fix #3 — boilerplate-director rejection.
# Catches cells where the parser captured a label or descriptive
# phrase ("Person closely associated with X", "Trustee of Y", "PDMR",
# "The Company", "Director") instead of the actual PDMR name. All
# observed live-DB hits came from the legacy regex path (6 rows in
# audit_2026-05-27_initial.md). Anchored at the start of the stripped
# cell; case-insensitive. Real names like "Director Smith" are not
# possible because the lone token "Director" + optional `$` is the
# only "director" branch — anything followed by a real surname falls
# through to the existing narrative-capture check (B-004 / D.4).
_BOILERPLATE_DIRECTOR_RE = re.compile(
    r"^\s*("
    r"person\s+closely\s+associated"
    r"|trustee\s+of"
    r"|pdmr\b"
    r"|notifier"
    r"|the\s+company"
    r"|director\s*$"
    r"|managerial\s+responsibilities"
    r")",
    re.IGNORECASE,
)

# Label recognisers for the issuer key-value table.
#
# Two Investegate layouts in the wild:
#   Layout A: 2-cell rows -- ['Name', 'National Grid plc']
#   Layout B: 3-cell rows -- ['a)', 'Full name of the entity', 'Anglo American plc']
#
# Layout B is the older MAR Article 19 template. It carries BOTH a
# 'Name' cell (the PDMR) AND a 'Full name of the entity' cell (the
# issuer), so we can't use 'Name' as the universal issuer label -- it
# would accidentally pick up the PDMR. The company-resolution path
# below uses two strict passes (Layout-B-explicit first, then
# Layout-A 2-cell 'Name') to keep these disambiguated.
_LABEL_COMPANY_EXPLICIT_RE = re.compile(
    r"^\s*(?:full\s+name\s+of\s+(?:the\s+)?entity"
    r"|name\s+of\s+(?:the\s+)?entity"
    r"|issuer\s+name|name\s+of\s+(?:the\s+)?issuer)\s*$",
    re.IGNORECASE,
)
_LABEL_NAME_RE = re.compile(r"^\s*name\s*$", re.IGNORECASE)
_LABEL_NATURE_RE = re.compile(
    r"^\s*nature\s+of\s+(?:the\s+)?transaction\s*$",
    re.IGNORECASE,
)

# B-022 Pass 3 (2026-05-19): a cell text that LOOKS like a corporate
# name. Used only when Passes 1+2 of _find_company_in_soup have already
# failed. Anchored ^...$ so a partial-line hit like "Issuer name: Foo
# plc" can't match. Length cap (110 chars before the suffix) guards
# against accidentally grabbing a long disclosure paragraph that
# happens to end in 'plc'. The leading capital letter rules out
# lowercased footer text.
_COMPANY_SUFFIX_RE = re.compile(
    r"^[A-Z][\w &'\".,\-/]{0,110}?\s+"
    r"(?:plc|p\.l\.c\.|Ltd|Limited|Group)\s*\.?$",
    re.IGNORECASE,
)

# Director-name validation. Reject:
#   - newlines (means the cell text crossed a row boundary in old parsers)
#   - whole-word corporate suffixes when the candidate is short
#     (a "name" of "Kingfisher plc" is almost certainly the company)
_CORP_SUFFIX_RE = re.compile(
    r"\b(plc|ltd|limited|llp|llc|inc|n\.?v\.?|s\.?a\.?|s\.?p\.?a\.?)\b",
    re.IGNORECASE,
)


def _find_kv_in_soup(soup, label_re) -> str | None:
    """Find a row where the label cell matches label_re. Return the next cell or None.

    Handles two layouts:
      * 2-cell rows: cells[0] is the label, cells[1] is the value.
      * 3-cell rows: cells[1] is the label (cells[0] is a section/letter
        prefix), cells[2] is the value. This is the older MAR template
        used by Anglo American, Spirent, Netcall and many others.
    """
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        # Layout A — cells[0] is the label
        c0 = cells[0].get_text(" ", strip=True)
        if label_re.match(c0):
            c1 = cells[1].get_text(" ", strip=True)
            if c1:
                return c1
        # Layout B — cells[1] is the label (cells[0] is a section/letter prefix)
        if len(cells) >= 3:
            c1text = cells[1].get_text(" ", strip=True)
            if label_re.match(c1text):
                c2 = cells[2].get_text(" ", strip=True)
                if c2:
                    return c2
    return None


def _find_company_in_soup(soup) -> str | None:
    """Resolve the issuer company name across the Investegate layouts.

    Pass 1: look for Layout B's unambiguous issuer label
    ('Full name of the entity', 'Issuer name', 'Name of the issuer')
    in any 2- or 3-cell row.

    Pass 2 (fallback for Layout A): look for a 2-cell row whose first
    cell is exactly 'Name'. The 2-cell restriction is critical —
    Layout B's PDMR row is ['', 'Name', 'Foo Person'] and we must NOT
    pick that up as the company.

    Pass 3 (B-022, 2026-05-19): some filings — Netcall (NET) 8998766
    is the canonical case — have NO KV label at all. The company name
    sits as a colspan'd header cell at the top of Table 0 with no
    'Name' / 'Issuer name' label. Pass 3 scans Table 0's cells for
    text that looks like a corporate name (ends in plc / Ltd /
    Limited / Group with an optional period). Restricted to Table 0
    so the trade table (usually Table 1+) can't false-positive on a
    director's affiliation string. Only fires when Passes 1+2 yield
    nothing.
    """
    # Pass 1: explicit issuer labels
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        c0 = cells[0].get_text(" ", strip=True)
        if _LABEL_COMPANY_EXPLICIT_RE.match(c0):
            v = cells[1].get_text(" ", strip=True)
            if v:
                return v
        if len(cells) >= 3:
            c1 = cells[1].get_text(" ", strip=True)
            if _LABEL_COMPANY_EXPLICIT_RE.match(c1):
                v = cells[2].get_text(" ", strip=True)
                if v:
                    return v

    # Pass 2: Layout A's 2-cell ['Name', 'Foo plc']
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) == 2:
            c0 = cells[0].get_text(" ", strip=True)
            if _LABEL_NAME_RE.match(c0):
                v = cells[1].get_text(" ", strip=True)
                if v:
                    return v

    # Pass 3 (B-022): scan Table 0 cells for a corporate-form suffix.
    tables = soup.find_all("table")
    if tables:
        for tr in tables[0].find_all("tr"):
            for cell in tr.find_all(["th", "td"]):
                txt = cell.get_text(" ", strip=True)
                if txt and _COMPANY_SUFFIX_RE.match(txt):
                    return txt

    return None


def _find_transaction_table(soup) -> tuple:
    """Locate the transaction table by header row pattern.

    Returns `(col_map, data_rows)` where col_map is a dict
    {key: column_index} (with 'date', 'price', 'volume' guaranteed
    present) and data_rows is a list of cell-text lists for every row
    after the header. Returns (None, None) when no qualifying table is
    found.
    """
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for hi, hr in enumerate(rows):
            cells = [c.get_text(" ", strip=True) for c in hr.find_all(["th", "td"])]
            if not cells:
                continue
            col_map: dict = {}
            for i, ct in enumerate(cells):
                if "date" not in col_map and _HDR_DATE_RE.search(ct):
                    col_map["date"] = i
                    continue
                if "name" not in col_map and _HDR_NAME_RE.search(ct):
                    col_map["name"] = i
                    continue
                if "position" not in col_map and _HDR_POSITION_RE.search(ct):
                    col_map["position"] = i
                    continue
                if "price" not in col_map and _HDR_PRICE_RE.search(ct):
                    col_map["price"] = i
                    continue
                if "volume" not in col_map and _HDR_VOLUME_RE.search(ct):
                    col_map["volume"] = i
                    continue
            if {"date", "price", "volume"} <= col_map.keys():
                # D.1 (Sprint 9 Phase B) — reject flattened KV-row
                # pretenders. When the transaction <table> is nested
                # inside a KV-table <td>, BeautifulSoup's flat
                # .find_all("tr") surfaces both the outer KV row AND
                # the inner data rows. The outer row contains every
                # flattened header token (often 15+ cells) and trips
                # the col_map check above. The inner data rows have
                # only 5-6 cells, so `len(cells) <= max_idx` later
                # discards them and the parser falls through to legacy.
                #
                # A real transaction header always has at least one
                # same-width data row immediately following it. Reject
                # the candidate if neither holds:
                #   - at least one of the next 5 rows is within ±1
                #     cell of the header's width, OR
                #   - header itself has ≤12 cells (real headers rarely
                #     exceed 8; 12 is a safety margin).
                following_rows = rows[hi + 1:]
                if len(cells) > 12:
                    continue
                candidate_data_widths = [
                    len(tr.find_all(["th", "td"]))
                    for tr in following_rows[:5]
                ]
                if not candidate_data_widths:
                    continue
                if not any(abs(w - len(cells)) <= 1
                           for w in candidate_data_widths):
                    continue

                data_rows: list = []
                for tr in following_rows:
                    rcells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                    if not rcells or all(not c.strip() for c in rcells):
                        continue
                    # Skip rows that look like sub-headers (no parseable date).
                    data_rows.append(rcells)
                return col_map, data_rows
    return None, None


def _validate_director_cell(name: str | None, company: str | None,
                            *, salvage_newline: bool = False) -> str | None:
    """Return cleaned director name, or None if it looks invalid.

    Rejects newlines (cell-boundary bleed-through from the old regex
    parser), short candidates with corporate suffixes (company names
    mislabelled as director names — B-004), candidates that match the
    company name exactly (B-017 mirror), AND (Sprint 9 D.4)
    narrative-capture candidates where a free-text paragraph from the
    announcement body has ended up in the director cell.

    When ``salvage_newline`` is True (legacy path only), a multi-line
    candidate is salvaged by taking its first non-empty line, then the
    usual validation runs. We additionally require the salvaged result
    to be at least two whitespace-separated tokens to avoid emitting
    label-words (e.g. 'Role', 'Daniel' fragment) as director names.
    Multi-line cells in the table-aware path remain a hard reject
    because table cells should never legitimately contain newlines.

    D.4 narrative-capture checks (Sprint 9 Phase B, 2026-05-25):
    after newline handling and before the corp-suffix check, also reject
    candidates that:
      - contain any token in `_DIRECTOR_NARRATIVE_STOPWORDS`
        ('transaction', 'notification', etc. — case-insensitive substring)
      - exceed 80 characters (no real PDMR name + title approaches this)
      - contain 3+ commas or 2+ full stops (sentence structure rather
        than a name+title)
    Verified Class-5 captures killed by this: AZN, TSCO, BNC, V3TC, BLND.
    """
    if not name:
        return None
    s = name.strip()
    if not s:
        return None
    if "\n" in s or "\r" in s:
        if not salvage_newline:
            return None
        # Take the first non-empty line as the candidate.
        lines = [ln.strip() for ln in s.replace("\r", "\n").split("\n")
                 if ln.strip()]
        if not lines:
            return None
        s = lines[0]
        # Require at least two tokens (rough heuristic for first+last
        # name) — single tokens are almost always label words like
        # 'Role', 'Name', 'Daniel' (truncated capture).
        if len(s.split()) < 2:
            return None
    # Sprint 11 Fix #3 — boilerplate-text rejection.
    # Catches cells where the parser captured a label or descriptive
    # phrase instead of the actual PDMR name (GETB "Person closely
    # associated with Daniel Rabie"; TATE "Trustee of the Kimberly A
    # Nelson Revocable Trust"; bare "PDMR"/"The Company"/"Director").
    # Caller routes None to pending_review.
    if _BOILERPLATE_DIRECTOR_RE.match(s):
        return None
    # Sprint 11 Fix #3 — truncated-extraction rejection.
    # Catches HLN "Bl" (2 chars) / LGEN "Ant" (3 chars) — silent
    # parser failures where the candidate is too short to be a real
    # name. Boundary is strict `< 4`: shortest plausible real PDMR
    # cell is "A Cox"-style initials (5 chars including space) but
    # the corpus also contains "John" / "Anna" / "Liam" (4 chars), so
    # `len < 4` is the conservative cutoff that drops the known bad
    # rows without false-positiving on legitimate short first names.
    if len(s) < 4:
        return None
    # D.4 — reject narrative captures. The director cell should be a
    # name, not a sentence fragment from the filing body.
    s_lower = s.lower()
    for stopword in _DIRECTOR_NARRATIVE_STOPWORDS:
        if stopword in s_lower:
            return None
    if len(s) > 80:
        return None
    if s.count(",") >= 3 or s.count(".") >= 2:
        return None
    # Reject corporate-suffix candidates (Kingfisher plc, National Grid plc).
    if _CORP_SUFFIX_RE.search(s) and len(s) < 40:
        return None
    if company and s.lower() == company.strip().lower():
        return None
    return s.rstrip(".,")


def _validate_role_cell(role: str | None) -> str | None:
    """Sprint 11 Fix #4 — reject prose-bleed roles, normalise case.

    25 known bad rows in the live DB all share one or more of:
      - Start with punctuation (sentence-mid bleed, e.g. ", at the date
        of grant")
      - Length > 80 chars (titles are not sentences)
      - Start with a sentence-fragment word ("ing Officer, who
        purchased..." from capturing the tail of a wrapped word)

    All 25 are `parser_source = 'regex'` failures; the legacy path
    captures free text from the announcement body into the role field.

    False-positive trap (pinned by negative tests in test_parser.py):
    real lowercase-starting roles like "interim Chief Financial Officer"
    (RENX) and "group Senior Executive Vice-President" (BNC) are
    legitimate titles with sloppy source-side casing. We must NOT
    blanket-reject lowercase starts. Instead, title-case the first
    character on emission so the canonical form stored in the DB is
    "Interim Chief Financial Officer".

    Known limitation (documented, accepted at spec time): a long-enough
    bleed that starts with a lowercase NON-punctuation word and stays
    under 80 chars (e.g. "the business to capitalise on opportunities
    in its markets", CKN ×4) WILL be preserved here as
    "The business...". Catching this would require a part-of-speech /
    title-vocabulary rule that risks false-positives on real titles;
    spec accepts the trade-off.
    """
    if not role:
        return None
    r = role.strip()
    if not r:
        return None
    # Rule 1: starts with punctuation → always bleed.
    if r[0] in ",.;:":
        return None
    # Rule 2: too long → not a title.
    if len(r) > 80:
        return None
    # Rule 3: title-case the first char only. Preserves the rest of the
    # string verbatim so multi-word titles like "Chief Financial
    # Officer" are not mangled by a blanket .title() call.
    if r[0].islower():
        r = r[0].upper() + r[1:]
    return r


# Sprint 11 Fix #5 — director name normalisation.
# UK convention: nobiliary / locative particles stay lowercase when they
# appear after the first word ("Marle van der Walt", "John of Albemarle").
# When the particle is the first word, leading-cap is preserved ("Van Helsing").
_LOWERCASE_PARTICLES = {
    "of", "the", "van", "de", "der", "von",
    "la", "le", "du", "da", "di", "del", "della", "den",
}

# Post-nominal letters that must be preserved uppercase regardless of
# input casing ("ALAN JOHNSON CMG" -> "Alan Johnson CMG").
_POST_NOMINALS = {
    "CMG", "CBE", "OBE", "MBE", "KBE", "DBE",
    "DSc", "PhD", "MD", "JP", "QC", "FCA", "FRS", "FRSE", "FRSC",
}

# Module-level cache for the exception map. Loaded lazily on first call.
_DIRECTOR_NAME_EXCEPTIONS_PATH = (
    Path(__file__).parent / "_director_name_exceptions.json"
)
try:
    with open(_DIRECTOR_NAME_EXCEPTIONS_PATH, encoding="utf-8") as _f:
        _DIRECTOR_NAME_EXCEPTIONS = {
            k: v for k, v in json.load(_f).items()
            if not k.startswith("_")
        }
except Exception:  # pragma: no cover -- fail-soft if file missing/malformed
    _DIRECTOR_NAME_EXCEPTIONS = {}


def _normalise_director_name(name, exceptions=None):
    """Sprint 11 Fix #5 — produce canonical Title-Case form of a director name.

    Handles: plain Title-Case, lowercase particles (van, de, etc. at
    non-initial position), hyphenated segments, apostrophe surnames
    (O'Brien), Mc prefix, post-nominals (CMG/OBE/etc. preserved
    uppercase), and a per-name exception map for special cases
    (MacKinnon, Macaulay) where the algorithm would over-capitalise.

    Idempotent: applying twice equals applying once.

    Args:
      name: raw director name string (may be None, empty, or whitespace).
      exceptions: optional override exception map. When None, the
        module-level `_DIRECTOR_NAME_EXCEPTIONS` (loaded from
        `_director_name_exceptions.json`) is used.

    Returns:
      The normalised name, or the original input unchanged when it is
      falsy / whitespace-only (so empty / None / "  " round-trip).
    """
    if not name or not name.strip():
        return name
    s = name.strip()

    # Exception list lookup (case-insensitive on the key).
    exc = exceptions if exceptions is not None else _DIRECTOR_NAME_EXCEPTIONS
    if exc:
        s_lower = s.lower()
        for k, v in exc.items():
            if k.lower() == s_lower:
                return v

    def _normalise_word(w, is_first):
        if not w:
            return w
        # Post-nominal check (CMG, OBE, etc.) — preserve uppercase.
        if w.upper() in _POST_NOMINALS:
            return w.upper()
        # Lowercase particle (only at non-initial position).
        if not is_first and w.lower() in _LOWERCASE_PARTICLES:
            return w.lower()
        # Hyphenated: title-case each segment (segments are not
        # word-initial in the sentence sense, but for hyphenation each
        # segment should be capitalised — "Seymour-Jackson", not
        # "Seymour-jackson").
        if "-" in w:
            return "-".join(_normalise_word(part, False) for part in w.split("-"))
        # Apostrophe: capitalise BEFORE and AFTER (O'Brien, D'Souza).
        if "'" in w:
            parts = w.split("'", 1)
            after = parts[1]
            after_cap = (after[:1].upper() + after[1:].lower()) if after else ""
            return parts[0].capitalize() + "'" + after_cap
        # Mc-prefix: "Mc" + uppercase next + lowercase rest
        # ("mcdonald" -> "McDonald"). Length guard ensures we don't
        # corrupt 2-char names like "Mc" alone.
        if len(w) >= 3 and w[:2].lower() == "mc" and w[2:].isalpha():
            return "Mc" + w[2].upper() + w[3:].lower()
        # Default Title Case (str.capitalize is Unicode-aware in
        # Python 3 — "benoît" -> "Benoît").
        return w.capitalize()

    words = s.split()
    return " ".join(_normalise_word(w, i == 0) for i, w in enumerate(words))


def _validate_company_cell(name: str | None, director_candidates: set) -> str | None:
    """Return cleaned company name, or None if it looks invalid.

    Rejects the MAR Article 19 boilerplate (B-016) and any candidate
    equal (case-insensitively) to a director name extracted from the
    same filing (B-017).
    """
    if not name:
        return None
    s = name.strip().rstrip(".,")
    if not s:
        return None
    if _is_boilerplate_company(s):
        return None
    lowered = s.lower()
    for d in director_candidates:
        if d and lowered == d.strip().lower():
            return None
    return s


# --- Sprint 9: plausibility gate (B-060) -----------------------------------
#
# Phase B (2026-05-25) — gate flipped to REJECT mode for R1-R4 after Gate 2
# approval. R5 stays warn-only (too noisy at 38% FP rate per QA spot-check).
# Allowlists seeded from QA spot-check verified edge cases (HBR, LTI).
# Nil-cost carve-out on R1 prevents legit LTIP/DSBP/RSP awards (value=0
# grants/exercises) from being rejected.
#
# Build plan: docs/specs/sprint-plan-2026-05-25-sprint9-phase-b.md
# QA evidence: docs/specs/sprint-9-phase-a-qa-spot-check.md

# Tickers exempted from R4 (value > £100m) — verified legit institutional
# blocks. HBR: Potomac View £153m placing (PCA of large shareholder).
INSTITUTIONAL_BLOCK_ALLOWLIST: set = {"HBR"}

# Tickers exempted from R3 (price > £200) — verified legit high-priced
# stocks. LTI: Lindsell Train Investment Trust trades £800-1000+.
# Sprint 11 Gate 1 (2026-05-27) — NXT, AZN, GAW confirmed genuine >£200
# trades in some filings; BHP excluded (Yahoo confirms ~£20, so BHP
# price>£200 rows are bugs).
HIGH_PRICED_TRUST_ALLOWLIST: set = {"LTI", "NXT", "AZN", "GAW"}

# Transaction types where value < £1 is non-suspicious (R1 first exemption).
NON_TRADE_TYPES_FOR_PLAUSIBILITY: frozenset = frozenset({"SIP", "DIVIDEND", "GRANT"})

# Transaction types where value = 0 is the legit norm (nil-cost LTIP/DSBP
# vests, option grants/exercises). Additional R1 carve-out beyond
# NON_TRADE_TYPES_FOR_PLAUSIBILITY to preserve recall on real
# nil-cost awards (SBRY, FOXT, INCH — verified in QA spot-check).
# Also used by D.3 emission-path drop: if price=0 and type is NOT in
# this set, the row is a silent price-extraction failure and is dropped
# before the gate ever sees it.
NIL_COST_CARVEOUT_TYPES: frozenset = frozenset({"GRANT", "EXERCISE"})

# D.4 — narrative-capture stopwords for director-cell validation. The
# director cell should be a person's name, not a sentence fragment from
# the announcement narrative. Verified Class-5 captures: AZN, TSCO, BNC,
# V3TC, BLND. Match is substring + case-insensitive.
_DIRECTOR_NARRATIVE_STOPWORDS: frozenset = frozenset({
    "transaction",
    "notification",
    "announcement",
    "regulation",
    "subject",
    "purpose",
    "nature",
    "details",
    "the company",
    "this notification",
    "this announcement",
})

# D.2 — month-word matcher for date-bleed detection in `_parse_price_vol`.
# Used to disambiguate a real "19 shares" volume from a "19 May" date
# fragment that bled into the volume regex window.
_MONTH_WORD_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\b",
    re.IGNORECASE,
)


def _plausibility_check(
    row: dict,
    *,
    block_allowlist: set | None = None,
    trust_allowlist: set | None = None,
) -> tuple:
    """Sprint 9 plausibility gate (Phase B — reject mode for R1-R4).

    Returns ``(ok, reasons)`` where ``ok`` is True when the row passes
    every rule and ``reasons`` is a list of rule IDs that fired.

    Rules (Phase B, approved at Gate 2 on 2026-05-25):
        R1 — value < £1 AND type not in {SIP, DIVIDEND, GRANT}
             EXCEPT nil-cost carve-out: skip when
             type in {GRANT, EXERCISE} AND value == 0
        R2 — shares < 100 AND price < £1 AND type != SIP
        R3 — price > £200 AND ticker not in HIGH_PRICED_TRUST_ALLOWLIST
        R4 — value > £100m AND ticker not in INSTITUTIONAL_BLOCK_ALLOWLIST
        R5 — shares in date-component range (1..31 or 1990..2099)
             AND value < £100  [warn-only — caller emits the row but
             logs it; R5 is not used as a reject reason in Phase B]
        R6 — shares EXACTLY equal to a 4-digit year (1990..2099)
             [HARD REJECT, value-independent — year-as-shares refix
             2026-05-31 Step A. Caller drops the row and the filing
             routes to pending-review.]

    Caller (parse_announcement) rejects a row if reasons contains any
    of R1-R4 or R6. Rows tripping ONLY R5 are logged but still emitted.

    Args:
        row: extracted transaction dict (date, ticker, director, type,
             shares, price, value, ...).
        block_allowlist: optional override for INSTITUTIONAL_BLOCK_ALLOWLIST
             (R4 exception). Defaults to the module constant.
        trust_allowlist: optional override for HIGH_PRICED_TRUST_ALLOWLIST
             (R3 exception). Defaults to the module constant.
    """
    block_allowlist = (
        block_allowlist if block_allowlist is not None
        else INSTITUTIONAL_BLOCK_ALLOWLIST
    )
    trust_allowlist = (
        trust_allowlist if trust_allowlist is not None
        else HIGH_PRICED_TRUST_ALLOWLIST
    )
    reasons: list = []

    shares = int(row.get("shares") or 0)
    price = float(row.get("price") or 0.0)
    value = float(row.get("value") or 0.0)
    tx_type = (row.get("type") or "").upper()
    ticker = row.get("ticker") or ""

    # R1 — sub-pound value on a real trade.
    # Carve-outs:
    #   - SIP / DIVIDEND / GRANT are non-trade row types (price irrelevant)
    #   - Nil-cost (GRANT / EXERCISE) with value == 0 is legitimate
    if value < 1.0 and tx_type not in NON_TRADE_TYPES_FOR_PLAUSIBILITY:
        nil_cost_grant = (
            tx_type in NIL_COST_CARVEOUT_TYPES and value == 0.0
        )
        if not nil_cost_grant:
            reasons.append("R1_sub_pound_value")

    # R2 — tiny share count at sub-pound price on a non-SIP trade.
    if shares < 100 and price < 1.0 and tx_type != "SIP":
        reasons.append("R2_tiny_shares_low_price")

    # R3 — per-share price above £200 (high-priced trusts allowlisted).
    if price > 200.0 and ticker not in trust_allowlist:
        reasons.append("R3_price_too_high")

    # R4 — total value > £100m (institutional blocks allowlisted).
    if value > 100_000_000.0 and ticker not in block_allowlist:
        reasons.append("R4_excessive_value")

    # R5 — shares value looks like a day-of-month or a year, and total
    # value is tiny. Warn-only in Phase B; caller does not reject on
    # R5 alone.
    looks_like_date_component = (
        (1 <= shares <= 31) or (1990 <= shares <= 2099)
    )
    if looks_like_date_component and value < 100.0:
        reasons.append("R5_date_component_in_shares")

    # R6 — share count is EXACTLY a 4-digit year (HARD REJECT).
    # Year-as-shares refix (2026-05-31, Step A): value-independent guard.
    # Any `shares` equal to a year in 1990..2099 is overwhelmingly a
    # date-bleed misread (e.g. EMAN/Dorfman 9581012 stored shares=2026,
    # value≈£678 — the real holding was 127,083 shares). Unlike R5 this is
    # NOT gated on a tiny value and IS a reject reason, so the caller drops
    # the row and routes the filing to pending-review rather than letting a
    # bogus year-as-shares row enter the corpus. The defensible-rare genuine
    # 2,0xx-share holding surfaces in pending-review for manual confirmation.
    if 1990 <= shares <= 2099:
        reasons.append("R6_shares_equals_year")

    return (not reasons), reasons


def _log_suspect_filing(
    row: dict,
    reasons: list,
    url: str,
    rns_id: str | None = None,
) -> None:
    """Append a flagged row to ``.data/_suspect_filings.jsonl``.

    Phase-A logger. JSONL format (one JSON object per line) so each
    call is a single short append -- O(1) per row regardless of how
    many rows are already logged. Read-modify-write of a JSON array
    would be O(n) per call and O(n^2) over a full corpus run, which
    in testing made the parser unusable on full pipeline passes.

    Best-effort: any write error is swallowed rather than propagated
    so a logging failure never blocks parsing.
    """
    try:
        # Lazy import — keeps parse_pdmr importable without db.py for
        # unit tests that monkeypatch the logger entirely.
        import json as _json
        from datetime import datetime, timezone

        from db import DB_DIR

        path = DB_DIR / "_suspect_filings.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "logged_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "rns_id": rns_id,
            "url": url,
            "reasons": list(reasons),
            "row": {k: row.get(k) for k in (
                "fingerprint", "date", "ticker", "company", "director",
                "role", "type", "shares", "price", "value",
            )},
        }
        line = _json.dumps(entry, ensure_ascii=False) + "\n"
        # Plain append — atomic at the OS line level for writes
        # smaller than PIPE_BUF (typically 4096 bytes). Each entry is
        # well under that.
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        # Never let a logging failure break the parser.
        pass


def _parse_price_cell(text: str) -> tuple:
    """Parse a single Price (s) cell into (price_gbp, warnings).

    Mirrors the currency / pence handling of `_parse_price_vol` but on
    one cell rather than the whole document, so cross-field bleeding
    (e.g. an EUR mention in the document footer) can't poison the
    extraction.
    """
    if not text:
        return 0.0, ["could_not_separate_price_volume"]
    warnings: list = []
    upper = text.upper()
    if _ZAR_PREFIX_RE.search(text):
        return 0.0, ["foreign_currency"]
    for marker in _FOREIGN_CURR_MARKERS:
        if marker in upper:
            return 0.0, ["foreign_currency"]
    distinct: set = set()
    last = 0.0
    for m in NUMBER_RE.finditer(text):
        num_str = m.group("num").replace(",", "")
        try:
            val = float(num_str)
        except ValueError:
            continue
        curr = (m.group("curr") or "").lower()
        post = (m.group("post") or "").lower()
        if curr in {"$", "usd", "eur", "€", "chf", "jpy", "zar"} or post in {"usd", "eur", "chf", "jpy", "zar"}:
            continue
        if post in {"p", "pence"}:
            gbp = val / 100.0
        elif curr in {"gbp", "£"}:
            gbp = val
        else:
            gbp = val  # bare number — assume GBP
        distinct.add(round(gbp, 6))
        last = gbp
    if not distinct:
        return 0.0, ["could_not_separate_price_volume"]
    if len(distinct) > 1:
        return 0.0, ["multiple_distinct_prices"]
    return last, warnings


def _parse_volume_cell(text: str) -> tuple:
    """Parse a single Volume (s) cell into (shares, warnings).

    Sprint 11 Fix #1 — call _looks_like_date_bleed() to reject
    year-like and day-like integers that bled in from an adjacent
    date cell. Mirrors the protection on the legacy regex path
    (see _parse_price_vol around line 645).
    """
    if not text:
        return 0, ["could_not_separate_price_volume"]
    saw_candidate = False
    for m in NUMBER_RE.finditer(text):
        num_str = m.group("num").replace(",", "")
        try:
            val = float(num_str)
        except ValueError:
            continue
        if val >= 1 and abs(val - int(val)) < 1e-6:
            saw_candidate = True
            int_val = int(val)
            # C.2 — reject par-value pence notation (e.g. "50p each",
            # "1 4/77 pence"). A real share count never has a pence suffix.
            post = (m.group("post") or "").lower()
            if post in {"p", "pence"}:
                continue
            if _looks_like_date_bleed(int_val, text, 0.0):
                continue
            return int_val, []
    if saw_candidate:
        return 0, ["volume_only_contained_dates"]
    return 0, ["could_not_separate_price_volume"]


def _parse_table_date(cell: str) -> str | None:
    """Parse a date cell using the existing `_EMBEDDED_DATE_RE` / `_try_one_date` helpers."""
    if not cell:
        return None
    for em in _EMBEDDED_DATE_RE.finditer(cell):
        raw = next((g for g in em.groups() if g), None)
        if not raw:
            continue
        iso = _try_one_date(raw)
        if iso:
            return iso
    return None


# --- Aggregate / tranche-sum extraction (year-as-shares refix Step B) -------
#
# The standard MAR Article 19 PDMR template (used by EMAN and many others)
# bundles all per-tranche price/volume rows into a SINGLE nested <table>
# inside the "c) Price(s) and volume(s)" KV cell, and carries a labelled
# "- Aggregated volume" / "- Price" pair in the "d) Aggregated information"
# block. Crucially the tranche table has NO director-name / position column —
# it is "Date | Price | Volume" (9581012) or just "Price | Volume" (9592451).
#
# `_find_transaction_table` can't handle these: 9592451's tranche table lacks
# a Date column (so the {date,price,volume} header requirement fails), and
# even when a Date column is present the missing Name column means every row
# is dropped (could_not_extract_PDMR_name) and the filing falls through to the
# year-bleeding legacy regex path.
#
# This extractor recognises the template by its KV labels, pulls the PDMR name
# from the "a) Name" detail row (NOT a table column), reads the labelled
# Aggregated volume (or sums the per-tranche volumes when it is N/A/blank),
# computes a volume-weighted price, and dates the row to the latest tranche.
# Emits exactly ONE row per PDMR per filing (locked decision #1).

# KV labels used by the aggregate template. Anchored ^...$ so a partial
# in-prose hit can't match.
# B-196 (2026-06-25): some MAR-template issuers label the PDMR-name row
# "Full name of person Dealing" / "Full name of person dealing" instead of
# the bare "Name" (CT Automotive 9632038). Accept that variant so the
# aggregate path can resolve the director from the detail KV block.
_LABEL_PDMR_NAME_RE = re.compile(
    r"^\s*(?:name|full\s+name\s+of\s+(?:the\s+)?person(?:\s+dealing)?)\s*$",
    re.IGNORECASE,
)
_LABEL_POSITION_RE = re.compile(r"^\s*position\s*/?\s*status\s*$", re.IGNORECASE)
# B-196: tolerate the trailing-"s" plural the Gana Media template uses
# ("Price(s) and volumes(s)", rns 9634964) in addition to the canonical
# "Price(s) and volume(s)".
_LABEL_PRICE_VOL_RE = re.compile(
    r"^\s*price\s*\(s\)\s*(?:and|&)\s*volumes?\s*\(s\)\s*$",
    re.IGNORECASE,
)
_LABEL_AGG_VOLUME_RE = re.compile(
    r"^\s*-?\s*aggregated?\s+volume\s*$", re.IGNORECASE
)
_LABEL_AGG_PRICE_RE = re.compile(r"^\s*-?\s*price\s*$", re.IGNORECASE)
_LABEL_TX_DATE_RE = re.compile(
    r"^\s*date\s+of\s+(?:the\s+)?transaction\s*$", re.IGNORECASE
)
# Header recognisers for the nested tranche sub-table.
# B-196 (2026-06-25): the headers also appear as "Price (p)" / "Price(s)"
# and "Volume(s)" / "Volumes(s)" (CT Automotive 9632038, Gana Media
# 9634964). Tolerate an optional unit/plural suffix in parentheses (and the
# stray trailing "s" Gana uses) so these tranche tables are recognised.
_TRANCHE_HDR_DATE_RE = re.compile(r"^\s*date\s*$", re.IGNORECASE)
_TRANCHE_HDR_PRICE_RE = re.compile(
    r"^\s*prices?\s*(?:\([^)]*\))?\s*$", re.IGNORECASE
)
_TRANCHE_HDR_VOLUME_RE = re.compile(
    r"^\s*volumes?\s*(?:\([^)]*\))?\s*$", re.IGNORECASE
)


def _find_aggregate_tranche_table(soup):
    """Locate the nested per-tranche Price/Volume sub-table.

    Returns the BeautifulSoup <table> element whose header row is a
    bare ``Price | Volume`` or ``Date | Price | Volume`` (no director-name
    or position column), or None. This is the sub-table that lives inside
    the ``c) Price(s) and volume(s)`` KV cell of the MAR Article 19 template.
    """
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_cells = [
            c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])
        ]
        if not (2 <= len(header_cells) <= 3):
            continue
        has_price = any(_TRANCHE_HDR_PRICE_RE.match(c) for c in header_cells)
        has_volume = any(_TRANCHE_HDR_VOLUME_RE.match(c) for c in header_cells)
        # Reject pretenders that also carry name/position columns — those
        # belong to the standard transaction-table path.
        has_name = any(
            _HDR_NAME_RE.search(c) or _HDR_POSITION_RE.search(c)
            for c in header_cells
        )
        if has_price and has_volume and not has_name:
            return table, header_cells
    return None, None


# B-196 (2026-06-25): inline "Price: 3.25p Volume: 229,499 ..." cell parser.
# Some MAR-template issuers (Kelso 9630876) write the price/volume pair as
# free text inside the "c) Price(s) and volume(s)" KV cell with no nested
# sub-table. Two labelled sub-strings — "Price: <val>" and "Volume: <val>" —
# let us scope each number to its own label so a stray figure elsewhere in
# the cell can't be mis-read. Returns (price_gbp, shares); either may be 0.
# Price token: a single number with optional currency/pence markers, captured
# up to (but not into) the "Volume" label so the two figures never cross.
_INLINE_PRICE_RE = re.compile(
    r"Price\s*\(?s?\)?\s*[:\-]?\s*"
    r"([£$€]?\s*\d[\d,]*(?:\.\d+)?\s*(?:p|pence|gbp|usd|eur)?)",
    re.IGNORECASE,
)
_INLINE_VOLUME_RE = re.compile(
    r"Volumes?\s*\(?s?\)?\s*[:\-]?\s*(\d[\d,]*)",
    re.IGNORECASE,
)


def _parse_inline_price_volume(cell: str) -> tuple:
    """Parse a free-text 'Price: .. Volume: ..' KV cell into (price_gbp, shares)."""
    if not cell:
        return 0.0, 0
    price_gbp = 0.0
    shares = 0
    mp = _INLINE_PRICE_RE.search(cell)
    if mp:
        price_gbp, _w = _parse_price_cell(mp.group(1))
    mv = _INLINE_VOLUME_RE.search(cell)
    if mv:
        shares, _w = _parse_volume_cell(mv.group(1))
    return price_gbp, shares


def _extract_via_aggregate_table(html: str) -> tuple:
    """Year-as-shares refix Step B — table-aware aggregate / tranche-sum.

    Handles the MAR Article 19 PDMR template where the price/volume data
    lives in a nested sub-table (Price|Volume or Date|Price|Volume, with no
    director column) and the PDMR name is in a separate ``a) Name`` KV row.

    Returns ``(rows, company)`` matching `_extract_via_table`'s shape, where
    ``rows`` is a single-element list (one aggregated row per PDMR per
    filing) or None when this layout is not recognised.

    Aggregation rules (locked decisions, 2026-05-31):
      * shares  = labelled ``- Aggregated volume`` when it parses to an int;
                  else the SUM of the per-tranche Volume cells.
      * price   = labelled aggregated ``- Price`` when present and parseable;
                  else the volume-weighted average (total value / total
                  shares) across tranches.
      * date    = the latest tranche date when the sub-table has a Date
                  column; else the ``e) Date of the transaction`` KV value.
    """
    if not html:
        return None, None
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover -- bs4 is a hard dep for Sprint 3+
        return None, None

    soup = BeautifulSoup(html, "html.parser")

    # This is the MAR Article 19 template if EITHER the "Price(s) and
    # volume(s)" KV label is present OR a bare Price|Volume tranche sub-table
    # is found. B-196 (2026-06-25): some issuers label the price/volume row
    # differently — CT Automotive (9632038) uses "Number of shared acquired
    # or disposed of" — so the KV-label gate alone dropped a real filing to
    # the legacy year-bleeding path. The nested Price|Volume tranche table is
    # itself a reliable signal of this template, so accept either anchor.
    has_pv_label = bool(_find_kv_in_soup(soup, _LABEL_PRICE_VOL_RE))
    tranche_table, header_cells = _find_aggregate_tranche_table(soup)
    if tranche_table is None and not has_pv_label:
        return None, None

    # Column map for the tranche sub-table (only when one was found).
    col: dict = {}
    # B-196: the per-share price unit can live in the column HEADER rather
    # than each cell ("Price (p)" with bare "30.60" cells — CT Automotive).
    # Detect a pence-denominated price header so bare numeric cells are
    # converted pence->pounds instead of being read as whole pounds.
    price_header_is_pence = False
    if header_cells is not None:
        for i, ct in enumerate(header_cells):
            if "date" not in col and _TRANCHE_HDR_DATE_RE.match(ct):
                col["date"] = i
            elif "price" not in col and _TRANCHE_HDR_PRICE_RE.match(ct):
                col["price"] = i
                if re.search(r"\(\s*p(?:ence)?\s*\)", ct, re.IGNORECASE):
                    price_header_is_pence = True
            elif "volume" not in col and _TRANCHE_HDR_VOLUME_RE.match(ct):
                col["volume"] = i

    tranche_volumes: list = []
    tranche_prices: list = []
    tranche_dates: list = []
    if tranche_table is not None and "price" in col and "volume" in col:
        tranche_rows = tranche_table.find_all("tr")[1:]  # skip header
        for tr in tranche_rows:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not cells or all(not c.strip() for c in cells):
                continue
            max_idx = max(col.values())
            if len(cells) <= max_idx:
                continue
            v, _vw = _parse_volume_cell(cells[col["volume"]])
            price_cell = cells[col["price"]]
            # B-196: a bare numeric price under a "(p)" header is pence.
            if price_header_is_pence and not re.search(
                r"[£$€]|\bp\b|pence|gbp|usd|eur", price_cell, re.IGNORECASE
            ):
                price_cell = price_cell.strip() + " pence"
            p, _pw = _parse_price_cell(price_cell)
            if v <= 0:
                continue
            tranche_volumes.append(v)
            tranche_prices.append(p)
            if "date" in col:
                d = _parse_table_date(cells[col["date"]])
                if d:
                    tranche_dates.append(d)

    # B-196: inline price/volume fallback. Some issuers (Kelso 9630876) put
    # the data as free text in the "c) Price(s) and volume(s)" KV cell with
    # NO nested sub-table: "Price: 3.25p Volume: 229,499 Ordinary Shares".
    # When no tranche rows were collected, parse that cell directly.
    if not tranche_volumes and has_pv_label:
        pv_cell = _find_kv_in_soup(soup, _LABEL_PRICE_VOL_RE) or ""
        ip, iv = _parse_inline_price_volume(pv_cell)
        if iv > 0:
            tranche_volumes.append(iv)
            tranche_prices.append(ip)

    if not tranche_volumes:
        return None, None

    # --- shares: labelled aggregate volume, else tranche sum ---------------
    agg_vol_raw = _find_kv_in_soup(soup, _LABEL_AGG_VOLUME_RE)
    shares = 0
    if agg_vol_raw:
        parsed_agg, _w = _parse_volume_cell(agg_vol_raw)
        if parsed_agg > 0:
            shares = parsed_agg
    if shares <= 0:
        # N/A / blank / unparseable aggregate → sum the tranches.
        shares = sum(tranche_volumes)

    # --- price: labelled aggregate price, else volume-weighted average -----
    price = 0.0
    agg_price_raw = _find_kv_in_soup(soup, _LABEL_AGG_PRICE_RE)
    if agg_price_raw:
        parsed_price, _pw = _parse_price_cell(agg_price_raw)
        if parsed_price > 0:
            price = parsed_price
    if price <= 0.0:
        total_value = sum(
            p * v for p, v in zip(tranche_prices, tranche_volumes) if p > 0
        )
        total_vol_with_price = sum(
            v for p, v in zip(tranche_prices, tranche_volumes) if p > 0
        )
        if total_value > 0 and total_vol_with_price > 0:
            price = total_value / total_vol_with_price

    # --- date: latest tranche date, else "e) Date of the transaction" KV ---
    tx_date = max(tranche_dates) if tranche_dates else None
    if not tx_date:
        date_kv = _find_kv_in_soup(soup, _LABEL_TX_DATE_RE)
        if date_kv:
            tx_date = _parse_table_date(date_kv)

    # --- PDMR name + role from detail KV rows (NOT a table column) ---------
    company = _find_company_in_soup(soup)
    # Director name: the "a) Name" row in the PDMR detail block. Because the
    # issuer block ALSO has a "Name" row, _find_kv_in_soup returns the FIRST
    # match in document order — and the PDMR block precedes the issuer block
    # in the MAR template, so the first "Name" is the PDMR. Validate against
    # the resolved company so an accidental issuer-name match is rejected.
    director_raw = _find_kv_in_soup(soup, _LABEL_PDMR_NAME_RE)
    director = _validate_director_cell(director_raw, company)
    if director:
        director = _normalise_director_name(director)
    role = _validate_role_cell(_find_kv_in_soup(soup, _LABEL_POSITION_RE))

    # Nature → type classification.
    nature = _find_kv_in_soup(soup, _LABEL_NATURE_RE) or ""
    tx_type, type_w = _classify_type(nature)

    row = {
        "date": tx_date,
        "director": director,
        "role": role,
        "type": tx_type,
        "nature": nature,
        "price": price,
        "shares": shares,
        "warnings": list(type_w),
    }

    director_candidates = {director} if director else set()
    company = _validate_company_cell(company, director_candidates)
    return [row], company


_SECTION_HEADER_RE = re.compile(
    r"Details\s+of\s+(?:"
    r"PDMR\s*/\s*person\s+closely\s+associated"
    r"|PDMR\s*/\s*PCA\b"
    r"|person\s+closely\s+associated"
    r"|the\s+person\s+discharging\s+managerial\s+responsibilities"
    r")",
    re.IGNORECASE,
)


def _extract_via_sections(html: str) -> tuple:
    """B-023 section-aware extractor for bundled multi-PDMR filings.

    Some MAR Article 19 filings (e.g. AAL 8950385) bundle multiple PDMRs
    by including a SEPARATE 18-row KV detail table per PDMR — each
    containing one PDMR's name, position, transaction date, nature,
    price, and volume — instead of stacking transactions in a single
    table with date/price/volume columns. The standard
    `_extract_via_table` doesn't recognise this layout because no single
    qualifying header exists; the parser used to refuse to fan out via
    the bundled-warning early-return, yielding 0 rows.

    This extractor walks every `Details of PDMR` KV table in document
    order and pulls one row per PDMR from the within-table labelled
    rows ("Name", "Position / status", "Nature of the transaction",
    "Date of the transaction", "Price(s) and volume(s)").

    Returns `(rows, company)` matching `_extract_via_table`'s shape.
    Returns `(None, None)` if fewer than 2 sections are detected — the
    standard table extractor will handle single-PDMR layouts.
    """
    if not html:
        return None, None
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover -- bs4 is a hard dep for Sprint 3+
        return None, None

    soup = BeautifulSoup(html, "html.parser")

    # Identify section tables: outer table whose first row contains
    # "Details of PDMR" (or one of the variants).
    #
    # B-090 (2026-06-03): also capture the immediately-following sibling
    # table for each section, because Layout-B bundled filings put their
    # price/volume data in a separate 2-row table right after the KV table
    # rather than inside it.  A sibling is recognised by: ≤4 rows AND its
    # first row contains both "Price" and "Volume" as cell text.
    all_tables = soup.find_all("table")
    section_entries: list = []   # list of (trs, sibling_trs_or_None)
    for idx, table in enumerate(all_tables):
        trs = table.find_all("tr")
        if not trs:
            continue
        first_row_text = " ".join(
            c.get_text(" ", strip=True) for c in trs[0].find_all(["th", "td"])
        )
        if _SECTION_HEADER_RE.search(first_row_text):
            sibling_trs = None
            if idx + 1 < len(all_tables):
                nxt = all_tables[idx + 1]
                nxt_trs = nxt.find_all("tr")
                if nxt_trs and len(nxt_trs) <= 4:
                    nxt_hdr = " ".join(
                        c.get_text(" ", strip=True)
                        for c in nxt_trs[0].find_all(["th", "td"])
                    )
                    if re.search(r"price", nxt_hdr, re.IGNORECASE):
                        sibling_trs = nxt_trs
            section_entries.append((trs, sibling_trs))

    sections = [e[0] for e in section_entries]   # backward-compat for len check
    if len(sections) < 2:
        # Single section (or none): standard layout. Let the caller's
        # standard table extractor / legacy regex handle it.
        return None, None

    company: str | None = None
    rows: list = []

    for section, sibling_trs in section_entries:
        section_data: dict = {}
        section_list = list(section)   # B-090: need index for look-ahead
        for tr_idx, tr in enumerate(section_list):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) < 2:
                continue
            label = cells[1].strip().lower()
            value = cells[2].strip() if len(cells) >= 3 else ""

            if label == "name" and value and "director" not in section_data:
                section_data["director"] = value
            elif "position" in label and "status" in label and value:
                # Sprint 11 Fix #4 — defensive validation. All 25 known
                # bad role rows came from the legacy regex path, but
                # apply the validator here too so the sections path
                # never emits a prose-bleed role either.
                section_data["role"] = _validate_role_cell(value)
            elif "full name of the entity" in label and value and company is None:
                company = value
            elif "nature of the transaction" in label and value:
                section_data["nature"] = value
            elif ("date of the transaction" in label
                  or "date of transaction" in label) and value:
                # B-090C: SIP filings (e.g. GPE 8857578) label this
                # "Date of transaction" (no "the").
                section_data["date"] = value
            elif "aggregated" in label and "information" in label and value:
                # B-090C: capture the "d) Aggregated information" cell
                # ("Aggregated volume: N  Aggregated total: £X") for use as a
                # multi-tranche-SIP fallback when no single price/volume row
                # resolves (see the post-loop block below).
                section_data["agg_info"] = value
            elif "price(s) and volume(s)" in label or "price(s) and volumes(s)" in label:
                # The Price(s)/Volume(s) sub-table is typically a NESTED
                # <table> inside this row; BeautifulSoup's .find_all flattens
                # nested cells into the outer row, so cells[3:] often holds
                # ['Price(s)', 'Volume(s)', '<price>', '<volume>'] (4 cells).
                if (len(cells) >= 7
                        and "price" in cells[3].lower()
                        and "volume" in cells[4].lower()):
                    section_data["price_cell"] = cells[5]
                    section_data["volume_cell"] = cells[6]
                elif value:
                    # Inline format fallback: "Price(s) Volume(s) GBP 20.44 859"
                    # Pull the price+volume out of the concatenated string.
                    m = re.search(
                        r"(?:GBP|£|USD|\$|EUR|€)?\s*([\d.,]+)\s+(\d[\d,]*)\b",
                        value,
                    )
                    if m:
                        # Preserve currency hint from the cell so
                        # `_parse_price_cell` can handle foreign-currency
                        # rejection consistently.
                        upper = value.upper()
                        prefix = ""
                        for tok in ("GBP", "USD", "EUR", "CHF", "JPY", "ZAR"):
                            if tok in upper:
                                prefix = tok + " "
                                break
                        section_data["price_cell"] = prefix + m.group(1)
                        section_data["volume_cell"] = m.group(2)

                # B-090 Fix-A: if trigger row had no data (Polar Capital /
                # Layout-A pattern), look ahead in the next few rows of this
                # same section table.  The structure is:
                #   trigger row:  ['c)', 'Price(s) and volume(s)', '', ...]
                #   header row:   ['', '', 'Price(s)', 'Volume(s)', '']
                #   data row:     ['', '', '390.5p', '36,905', '']
                if "price_cell" not in section_data:
                    for fwd_tr in section_list[tr_idx + 1: tr_idx + 6]:
                        fwd_cells = [
                            c.get_text(" ", strip=True)
                            for c in fwd_tr.find_all(["th", "td"])
                        ]
                        if len(fwd_cells) < 3:
                            continue
                        c2 = fwd_cells[2].strip()
                        c3 = fwd_cells[3].strip() if len(fwd_cells) > 3 else ""
                        # Skip the sub-header row ("Price(s)", "Volume(s)")
                        if re.search(r"\bprice", c2, re.IGNORECASE) and re.search(
                            r"\bvolume", c3, re.IGNORECASE
                        ):
                            continue
                        # Data row: c2 has a digit (price), c3 has a digit (volume)
                        if re.search(r"\d", c2) and re.search(r"\d", c3):
                            # Preserve any currency prefix
                            upper2 = c2.upper()
                            prefix = ""
                            for tok in ("GBP", "USD", "EUR", "CHF", "JPY", "ZAR"):
                                if tok in upper2:
                                    prefix = tok + " "
                                    break
                            section_data["price_cell"] = prefix + c2
                            section_data["volume_cell"] = c3
                            break

        # B-090 Fix-B: if no price was found in the KV table rows and a
        # sibling price table was captured at build time (Layout-B pattern),
        # try it now.  The sibling has 2+ rows: row[0] is the
        # ['Price(s)', 'Volume(s)'] header; row[1] is the data.
        if "price_cell" not in section_data and sibling_trs:
            for sib_tr in sibling_trs:
                sib_cells = [
                    c.get_text(" ", strip=True) for c in sib_tr.find_all(["th", "td"])
                ]
                if len(sib_cells) < 2:
                    continue
                c0, c1 = sib_cells[0].strip(), sib_cells[1].strip()
                # Skip the header row
                if re.search(r"\bprice", c0, re.IGNORECASE):
                    continue
                # Data row: c0=price, c1=volume
                if c0 or c1:
                    upper0 = c0.upper()
                    prefix = ""
                    for tok in ("GBP", "USD", "EUR", "CHF", "JPY", "ZAR"):
                        if tok in upper0:
                            prefix = tok + " "
                            break
                    section_data["price_cell"] = prefix + c0 if c0 else ""
                    section_data["volume_cell"] = c1
                    break

        # B-090C: multi-tranche SIP fallback. Filings like GPE 8857578 carry
        # named-tranche rows (Partnership shares + nil-cost Matching shares)
        # that the Fix-A/Fix-B look-aheads can't reduce to one price/volume.
        # Use the "d) Aggregated information" totals instead: shares =
        # aggregated volume, value = aggregated total, price = total / volume.
        # value-based signals are unaffected (the nil-cost leg adds £0, so the
        # aggregated total ~= cash actually spent). Additive to the failure
        # path: only runs when no price_cell was resolved above.
        if "price_cell" not in section_data and section_data.get("agg_info"):
            _agg = section_data["agg_info"]
            _mv = re.search(r"aggregated\s+volume[:\s]*([\d,]+)", _agg, re.IGNORECASE)
            _mt = re.search(r"aggregated\s+total[:\s]*£?\s*([\d.,]+)", _agg, re.IGNORECASE)
            if _mv and _mt:
                try:
                    _vol = int(_mv.group(1).replace(",", ""))
                    _tot = float(_mt.group(1).replace(",", ""))
                except ValueError:
                    _vol, _tot = 0, 0.0
                if _vol > 0 and _tot > 0:
                    section_data["volume_cell"] = str(_vol)
                    section_data["price_cell"] = f"GBP {(_tot / _vol):.6f}"

        if not section_data.get("director"):
            continue

        director = _validate_director_cell(section_data["director"], company)
        if not director:
            continue
        # Sprint 11 Fix #5 — normalise director-name casing at emission so
        # the section-aware path emits canonical Title-Case (collapses
        # KATE ROCK / Kate Rock duplicate identities into one cluster_id).
        director = _normalise_director_name(director)

        iso_date = _parse_table_date(section_data.get("date") or "")
        tx_type, type_w = _classify_type(section_data.get("nature") or "")
        price, price_w = _parse_price_cell(section_data.get("price_cell") or "")
        shares, vol_w = _parse_volume_cell(section_data.get("volume_cell") or "")

        rows.append({
            "date": iso_date,
            "director": director,
            "role": section_data.get("role"),
            "type": tx_type,
            "nature": section_data.get("nature") or "",  # Sprint 13: buy_strictness source
            "price": price,
            "shares": shares,
            "warnings": list(price_w) + list(vol_w) + list(type_w),
        })

    if not rows:
        return None, company

    # Cross-row company validation (mirrors `_extract_via_table`).
    director_candidates = {r["director"] for r in rows if r["director"]}
    company = _validate_company_cell(company, director_candidates)

    return rows, company


def _extract_via_table(html: str) -> tuple:
    """Try the table-aware extraction path.

    Returns `(rows, company)` where rows is a list of per-row dicts
    (keys: date, director, role, type, price, shares, warnings) and
    company is the resolved issuer name (may be None). Returns
    (None, None) if no qualifying transaction table is present — the
    caller falls back to the legacy regex extractor.
    """
    if not html:
        return None, None
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover -- bs4 is a hard dep for Sprint 3+
        return None, None

    soup = BeautifulSoup(html, "html.parser")

    # Issuer / company name. Two-pass lookup handles both Investegate
    # layouts without confusing the issuer with the PDMR.
    company = _find_company_in_soup(soup)
    # Nature-of-transaction key-value lookup, used to classify type per row.
    nature = _find_kv_in_soup(soup, _LABEL_NATURE_RE)

    col_map, data_rows = _find_transaction_table(soup)
    if col_map is None or not data_rows:
        return None, company

    rows: list = []
    for cells in data_rows:
        # Require enough cells to cover the columns we identified.
        max_idx = max(col_map.values())
        if len(cells) <= max_idx:
            continue

        date_cell = cells[col_map["date"]]
        price_cell = cells[col_map["price"]]
        volume_cell = cells[col_map["volume"]]
        name_cell = cells[col_map["name"]] if "name" in col_map else ""
        position_cell = cells[col_map["position"]] if "position" in col_map else ""

        iso_date = _parse_table_date(date_cell)
        director = _validate_director_cell(name_cell, company)
        # Sprint 11 Fix #5 — normalise director-name casing at emission
        # so the table-aware path emits canonical Title-Case. Helper
        # safely handles None / empty input (returns input unchanged).
        if director:
            director = _normalise_director_name(director)
        price, price_w = _parse_price_cell(price_cell)
        shares, vol_w = _parse_volume_cell(volume_cell)

        # Type classification — try nature first (most reliable when
        # present), then the full row text as a defensive fallback.
        type_hint = nature or " ".join(cells)
        tx_type, type_w = _classify_type(type_hint)

        # C.3 — nil-cost leg override. Document-level nature sometimes says
        # "disposal" but individual rows are nil-cost exercises or vests.
        # If tx_type resolved to SELL but price is zero and the row/cell
        # text contains nil-cost / vesting markers, reclassify as EXERCISE.
        if tx_type == "SELL":
            row_text_lower = " ".join(cells).lower()
            nil_cost_markers = ("nil-cost", "nil cost", "vesting", "exercise of", "vest ")
            if price == 0.0 and any(tok in row_text_lower for tok in nil_cost_markers):
                tx_type = "EXERCISE"
                type_w = ["nil_cost_override"]

        # Sprint 11 Fix #4 — defensive validation. All 25 known bad
        # role rows came from the legacy regex path, but apply the
        # validator here too so the table path never emits a
        # prose-bleed role either.
        role = _validate_role_cell(
            position_cell.strip().rstrip(".,") if position_cell else None
        )

        # Sprint 11 Fix #2 — duplicate-number-pull guard (defensive
        # application in the table-aware path). When the parser pulls
        # the same number into both `price` and `shares`, it's almost
        # always a nested-table extraction failure where the price and
        # volume cells point to the same source text. Threshold of 1000
        # on both fields preserves real edge cases like GAW 184sh ×
        # £184 (Games Workshop trades around £187 — confirmed real).
        # All 5 known live-DB cases came from the legacy regex path;
        # this guard is defence-in-depth for future-proofing.
        row_warnings = list(price_w) + list(vol_w) + list(type_w)
        if (price is not None and shares is not None
                and float(price) > 1000.0
                and int(shares) > 1000
                and abs(float(price) - float(shares)) < 0.5):
            price, shares = 0.0, 0
            row_warnings.append("duplicate_number_pull")

        rows.append({
            "date": iso_date,
            "director": director,
            "role": role,
            "type": tx_type,
            "nature": type_hint or "",  # Sprint 13: buy_strictness source
            "price": price,
            "shares": shares,
            "warnings": row_warnings,
        })

    # Cross-row company validation: B-017 says some filings put a
    # director's name in the company field. If we have row-level director
    # candidates AND the company string matches any of them, drop it.
    director_candidates = {r["director"] for r in rows if r["director"]}
    company = _validate_company_cell(company, director_candidates)

    return rows, company


# --- Public entry -----------------------------------------------------------

# --- B-156: resulting-holding extraction (Sprint 61, 2026-06-10) ------------
#
# Document-level extraction of the post-transaction total holding
# ("resulting_shares"). Two families:
#
#   N1 -- narrative sentences anchored on "Following the/this/these/the above
#         transaction(s) ...", e.g.
#           "Following this transaction Rob Thomas is beneficially interested
#            in 5,629 shares" (rns 8855732)
#           "Following this transaction, Philip Broadley has an interest in
#            the Company of 53,415 common shares" (8861668)
#           "Following this transaction, Mark Stejbach has an interest in
#            14,924 shares in the Company" (8863449)
#           "Following this transaction the total holding of MS YVONNE
#            STILLHART is 13,718 Shares" (8863696)
#   T2 -- classic narrative tables with a header cell matching
#         "resulting ... beneficial (interest|holding)", (name, value) data
#         rows, e.g. Wynnstay 8858155 and Ondo 8856771. Percentage columns
#         ("Percentage resulting beneficial holding", "% of ...") are skipped.
#
# B-166 (Sprint 62, 2026-06-11) widened the N1 family and added two
# anchorless families. Live rate was 2.9% of BUYs; a 160-filing diagnostic
# found 34% of NULL cached filings state the figure in wordings the
# original extractor missed. Widening (real cached filings cited):
#
#   * Anchor nouns beyond "transaction(s)": purchase(s) (9079891),
#     "share purchase" (9237980), acquisition(s) (8999734 "the above
#     acquisition of shares"), trade(s) (9340385), transfer(s) /
#     "SIPP transfer" (9506876, 8978060), dealing(s).
#   * Predicates (still anchor-gated): "is interested in" without
#     "beneficially" (9469145, 9467814); "holds a (total) (beneficial)
#     interest in/of N" (9285232, 9275706); "X's total interest in the
#     Company is now N" (8931559, 8932498); "X's beneficial holding is
#     now/stands at/remains at N" (9508570, 9560193, 8955970, 9605499,
#     8978060); "holds a total of N" (8886038); "the total holdings of
#     X is N" (9237980).
#   * Family 3 (anchorless): "This transaction increases Mr X's total
#     holding to N" / "bringing/increasing his/her total holding to N"
#     (9537141, 9549363, 9519058). Pronoun variants carry name=None.
#   * Family 4 (anchorless): "Her/His resulting shareholding in the
#     Company is N" (9502995, 9553126). Pronoun variants carry name=None.
#
# NOT implemented (precision risk, per DIR-97): Family 5 (narrative-led
# table), Family 6 (joint/spouse holdings), %-only statements. Anchorless
# candidates flow through the SAME attribution rule and guards as anchored
# ones; a name=None candidate can only ever attach via the single-candidate
# + single-row rule, never via surname match.
#
# Attribution rule (per plan): single candidate + single emitted row ->
# attach; otherwise require a surname-token match between the candidate name
# and the row's director; otherwise None. Guards: integer >= 1; year-like
# values (1990-2099) rejected; on BUY rows resulting_shares must be >= shares
# (else 'resulting_lt_shares' warning + None). The figure is NEVER sourced
# from the MAR "Aggregated volume" KV row or issued-share-capital statements
# -- the anchors/headers above cannot match those. A missing figure is normal
# (the MAR Article 19 template has no resulting-holding field) and never
# rejects or warns on the row itself. The fingerprint is unchanged.

# Share-count number: 1,234,567 or plain digits. No decimals, no %.
_RES_NUM = r"(?P<num>\d{1,3}(?:,\d{3})+|\d+)"
# Person name: 1-6 capitalised words ("Rob Thomas", "MS YVONNE STILLHART").
# Case-SENSITIVE by design -- keyword parts of each form use (?i:...) scoped
# flags instead of a global IGNORECASE so the name capture stays anchored to
# capitalised words.
_RES_NAME = r"(?P<name>[A-Z][\w'.\-]*(?:\s+[A-Z][\w'.\-]*){0,5})"

_RES_ANCHOR_RE = re.compile(
    r"following\s+(?:the\s+above|the|this|these)?\s*"
    r"(?:transactions?"
    r"|(?:share\s+)?purchases?"
    r"|acquisitions?"
    r"|trades?"
    r"|(?:sipp\s+)?transfers?"
    r"|dealings?)",
    re.IGNORECASE,
)

# A stated-holding number that is NOT part of a decimal / percentage figure
# ("holding is 3.2%" must never yield 3). Used by the forms that have no
# trailing "shares" token to bound the capture.
_RES_NUM_END = _RES_NUM + r"(?!(?:[.,]\d|\s*(?:%|per\s?cent)))"

# Narrative forms, tried in order inside a bounded window after each anchor.
_RES_NARRATIVE_FORMS = (
    # F1: "<name> is (now) (beneficially) interested in <n> shares"
    #     (B-166: "beneficially" optional -- 9469145, 9467814)
    re.compile(
        _RES_NAME
        + r"\s+(?i:is\s+(?:now\s+)?(?:beneficially\s+)?interested\s+in)\s+"
        + _RES_NUM + r"\s*(?i:(?:ordinary|common)?\s*shares)",
    ),
    # F2: "<name> (now) has/holds a/an (total) (beneficial) interest
    #      (in the Company) of/in <n> (common) shares"
    #     (B-166: holds- and now-/total- variants -- 9340385, 9285232,
    #      9275706, 9079891)
    re.compile(
        _RES_NAME
        + r"\s+(?i:(?:now\s+)?(?:has|holds)\s+(?:an?\s+)?(?:total\s+)?"
        + r"(?:beneficial\s+)?interest"
        + r"(?:\s+in\s+the\s+Company)?\s+(?:of|in))\s+"
        + _RES_NUM + r"\s*(?i:(?:ordinary|common)?\s*shares)",
    ),
    # F3: "the total holding(s) of <name> is <n> Shares"
    #     (B-166: plural "holdings" -- 9237980)
    re.compile(
        r"(?i:the\s+total\s+(?:beneficial\s+)?(?:share)?holdings?\s+of)\s+"
        + _RES_NAME + r"\s+(?i:is|are|will\s+be|amounts\s+to)\s+"
        + _RES_NUM + r"\s*(?i:(?:ordinary|common)?\s*shares)",
    ),
    # F4: "<name> (now) (beneficially) holds (a total of) <n> shares"
    #     (B-166: "beneficially holds" + "holds a total of" -- 8886038)
    re.compile(
        _RES_NAME
        + r"\s+(?i:(?:now\s+)?(?:beneficially\s+)?holds"
        + r"\s+(?:a\s+total\s+of\s+)?)"
        + _RES_NUM + r"\s*(?i:(?:ordinary|common)?\s*shares)",
    ),
    # F5: "<name>'s total/beneficial holding/interest (of Shares /
    #      in the Company) is/stands at/remains at (now) <n>"
    #     (B-166: beneficial-only qualifier, "of Shares", "in the
    #      Company", "stands at", "remains at", trailing "now" --
    #      9506876, 9508570, 9560193, 8955970, 9605499, 8931559,
    #      8932498, 8978060)
    re.compile(
        _RES_NAME
        + r"(?i:'s\s+(?:total\s+beneficial|beneficial\s+total|total"
        + r"|beneficial)\s+(?:(?:share)?holdings?|interest)"
        + r"(?:\s+of\s+shares)?(?:\s+in\s+the\s+Company)?"
        + r"\s+(?:is|will\s+be|amounts\s+to|stands\s+at|remains\s+at)"
        + r"\s+(?:now\s+)?)" + _RES_NUM_END,
    ),
)

# B-166 families 3/4: anchorless statements (no "Following ..." lead-in).
# Searched over the WHOLE whitespace-collapsed text; every match passes
# the same forbidden-context prefix check and value guards as the anchored
# family, then flows through the unchanged attribution rule. Pronoun forms
# carry name=None, so they can only attach via single-candidate+single-row.
_RES_ANCHORLESS_FORMS = (
    # A1: "This transaction increases <name>'s total holding (in the
    #      Company) to <n>" (9537141, 9549363)
    re.compile(
        r"(?i:this\s+transaction\s+increases\s+)"
        + _RES_NAME
        + r"(?i:'s\s+(?:total|beneficial)\s+(?:share)?holdings?"
        + r"(?:\s+in\s+the\s+Company)?\s+to)\s+" + _RES_NUM_END,
    ),
    # A2: pronoun twin of A1 -> candidate name None
    re.compile(
        r"(?i)this\s+transaction\s+increases\s+(?:his|her|their)\s+"
        r"(?:total|beneficial)\s+(?:share)?holdings?"
        r"(?:\s+in\s+the\s+company)?\s+to\s+" + _RES_NUM_END,
    ),
    # A3: "bringing/increasing his/her total holding to <n>" (9519058)
    #     -> candidate name None
    re.compile(
        r"(?i)(?:bringing|increasing|taking)\s+(?:his|her|their)\s+"
        r"(?:total|beneficial)\s+(?:share)?holdings?"
        r"(?:\s+in\s+the\s+company)?\s+to\s+" + _RES_NUM_END,
    ),
    # A4: "His/Her resulting shareholding in the Company is <n>"
    #     (9502995, 9553126) -> candidate name None
    re.compile(
        r"(?i)(?:his|her|their)\s+resulting\s+(?:share)?holding\s+"
        r"in\s+the\s+company\s+is\s+" + _RES_NUM_END,
    ),
    # A4b: "<name>'s resulting shareholding in the Company is <n>"
    re.compile(
        _RES_NAME
        + r"(?i:'s\s+resulting\s+(?:share)?holding\s+in\s+the\s+company"
        + r"\s+is)\s+" + _RES_NUM_END,
    ),
)

# T2 header cell: "Resulting beneficial interest in Ordinary Shares",
# "Total resulting beneficial holding". The percentage twin ("Percentage
# resulting beneficial holding") is excluded by the caller.
_RES_TABLE_HDR_RE = re.compile(
    r"resulting\s+(?:total\s+)?beneficial\s+(?:interest|holding)",
    re.IGNORECASE,
)

# Forbidden source context: the captured number must not be the MAR
# "Aggregated volume" or an issued-share-capital figure.
_RES_FORBIDDEN_RE = re.compile(
    r"aggregated\s+volume|issued\s+share\s+capital", re.IGNORECASE,
)

# Honorific/title tokens ignored during surname matching.
_RES_TITLE_TOKENS = {
    "mr", "mrs", "ms", "miss", "dr", "sir", "dame", "lord", "lady",
    "prof", "professor", "the", "hon",
}

_RES_WINDOW_CHARS = 300   # narrative window length after each anchor


def _res_parse_int(raw: str) -> int | None:
    """'5,629' -> 5629 with the B-156 guards (>=1, not a year value)."""
    cleaned = (raw or "").replace(",", "").strip()
    if not cleaned.isdigit():
        return None
    val = int(cleaned)
    if val < 1:
        return None
    if 1990 <= val <= 2099 and "," not in (raw or ""):
        # Bare 4-digit value in the year range: overwhelmingly a date bleed
        # (the year-as-shares bug family), never a stated holding.
        return None
    return val


def _res_clean_name(name: str | None) -> str | None:
    """Strip footnote markers / trailing punctuation from a candidate name."""
    if not name:
        return None
    cleaned = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", name).strip(" .,;:")
    return cleaned or None


# B-166 precision guard: subjects that are corporate entities, not people.
# "Following the above transfer of treasury stock, the Company holds
# 4,772,867 ordinary shares as treasury shares" (rns 8857922) must never
# become a resulting-holding candidate -- it is the issuer's treasury
# count. A candidate whose name tokens are ALL corporate words is dropped.
_RES_CORPORATE_TOKENS = {
    "company", "group", "plc", "limited", "ltd", "board", "trust",
    "trustee", "trustees", "treasury", "issuer", "firm", "fund", "ebt",
    "sip", "sipp", "ssas", "scheme", "plan",
}


def _res_subject_is_corporate(name: str | None) -> bool:
    """True when the captured subject is an entity, not a person."""
    if not name:
        return False
    tokens = [t for t in re.split(r"[^\w]+", name.lower())
              if t and t not in _RES_TITLE_TOKENS]
    if not tokens:
        return False
    # B-166 QA fix (2026-06-11): entity names often carry non-corporate
    # qualifier tokens ("BOG Group Employee Trust", rns 9336969) which let
    # the all-tokens rule through. The HEAD NOUN (last token) is decisive:
    # a person's surname is never one of these words, so rejecting on the
    # head noun can only cost a missed enrichment, never a wrong attach.
    if tokens[-1] in _RES_CORPORATE_TOKENS:
        return True
    return all(t in _RES_CORPORATE_TOKENS for t in tokens)


def _res_surname_match(candidate_name: str | None, director: str | None) -> bool:
    """True when the candidate's surname token appears in the director name."""
    if not candidate_name or not director:
        return False
    def _tokens(s):
        return [t for t in re.split(r"[^\w]+", s.lower())
                if len(t) >= 2 and t not in _RES_TITLE_TOKENS]
    cand = _tokens(candidate_name)
    dirt = _tokens(director)
    if not cand or not dirt:
        return False
    # Surname = last non-title token. Accept either direction (filings
    # sometimes order names differently between narrative and KV table).
    return cand[-1] in dirt or dirt[-1] in cand


def _res_narrative_candidates(text: str) -> list:
    """Family N1: [(name|None, value), ...] from narrative sentences."""
    out: list = []
    if not text or "following" not in text.lower():
        return out
    for anchor in _RES_ANCHOR_RE.finditer(text):
        end = anchor.end() + _RES_WINDOW_CHARS
        # B-166: never clip the window mid-number -- a boundary landing
        # inside "10,069,157" used to leave num="1" (seen on 9605499).
        while end < len(text) and (text[end].isdigit() or text[end] == ","):
            end += 1
        window = text[anchor.end(): end]
        # Collapse whitespace (incl. newlines / nbsp) so forms span lines.
        window = re.sub(r"[\s ]+", " ", window)
        for form in _RES_NARRATIVE_FORMS:
            m = form.search(window)
            if m is None:
                continue
            # Guard: number must not sit in a forbidden context (aggregated
            # volume / issued share capital appearing BEFORE the number).
            prefix = window[max(0, m.start("num") - 60): m.start("num")]
            if _RES_FORBIDDEN_RE.search(prefix):
                continue
            val = _res_parse_int(m.group("num"))
            if val is None:
                continue
            name = _res_clean_name(m.groupdict().get("name"))
            if _res_subject_is_corporate(name):
                continue   # issuer/treasury statement, not a person's holding
            out.append((name, val))
            break   # one candidate per anchor window
    return out


def _res_anchorless_candidates(text: str) -> list:
    """B-166 families 3/4: [(name|None, value), ...] -- no anchor required."""
    out: list = []
    if not text:
        return out
    if "holding" not in text.lower():
        return out
    flat = re.sub(r"[\s ]+", " ", text)
    for form in _RES_ANCHORLESS_FORMS:
        for m in form.finditer(flat):
            # Same forbidden-context guard as the anchored family.
            prefix = flat[max(0, m.start("num") - 60): m.start("num")]
            if _RES_FORBIDDEN_RE.search(prefix):
                continue
            val = _res_parse_int(m.group("num"))
            if val is None:
                continue
            name = _res_clean_name(m.groupdict().get("name"))
            if _res_subject_is_corporate(name):
                continue   # issuer/treasury statement, not a person's holding
            out.append((name, val))
    return out


def _res_table_candidates(html: str) -> list:
    """Family T2: [(name, value), ...] from classic resulting-holding tables."""
    out: list = []
    if not html or not re.search(r"resulting", html, re.IGNORECASE):
        return out
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover -- bs4 is a hard dep for Sprint 3+
        return out
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        hdr_idx = None
        col_from_end = None
        # Header is one of the first few rows; the resulting-holding column
        # is located by its offset FROM THE END of the header row, because
        # some layouts (Wynnstay 8858155) omit the leading name-column header
        # so data rows have one more cell than the header row.
        for ri, tr in enumerate(rows[:4]):
            cells = [c.get_text(" ", strip=True)
                     for c in tr.find_all(["td", "th"])]
            for ci, ct in enumerate(cells):
                low = ct.lower()
                if (_RES_TABLE_HDR_RE.search(ct)
                        and "%" not in ct
                        and "percentage" not in low
                        and "per cent" not in low
                        and not _RES_FORBIDDEN_RE.search(ct)):
                    hdr_idx = ri
                    col_from_end = len(cells) - ci
                    break
            if hdr_idx is not None:
                break
        if hdr_idx is None:
            continue
        for tr in rows[hdr_idx + 1:]:
            cells = [c.get_text(" ", strip=True)
                     for c in tr.find_all(["td", "th"])]
            if not cells or len(cells) < col_from_end:
                continue
            raw_val = cells[len(cells) - col_from_end]
            if "%" in raw_val or "." in raw_val:
                continue   # percentage column / decimal -- never a holding
            val = _res_parse_int(raw_val)
            if val is None:
                continue
            name = _res_clean_name(cells[0])
            if not name or not re.search(r"[A-Za-z]", name):
                continue
            out.append((name, val))
    return out


def _extract_resulting_holdings(html: str, text: str) -> list:
    """Document-level candidate list: [(name|None, resulting_shares), ...].

    Narrative (N1) candidates first, then anchorless (B-166 families 3/4),
    then table (T2) candidates, de-duplicated on (name-lower, value).
    """
    candidates: list = []
    seen: set = set()
    for name, val in (_res_narrative_candidates(text)
                      + _res_anchorless_candidates(text)
                      + _res_table_candidates(html)):
        key = ((name or "").lower(), val)
        if key in seen:
            continue
        seen.add(key)
        candidates.append((name, val))
    return candidates


def _attach_resulting_shares(rows: list, html: str, text: str,
                             warnings: list) -> None:
    """Attach 'resulting_shares' (int|None) to every emitted row in place.

    Run once per filing, on whichever emission path produced rows. Filings
    that don't state the figure get None on every row -- never a warning-gate
    rejection. The fingerprint is computed before this runs and is untouched.
    """
    for row in rows:
        row["resulting_shares"] = None
    if not rows:
        return
    candidates = _extract_resulting_holdings(html, text)
    if not candidates:
        return

    def _guarded_attach(row, val):
        # BUY guard: a stated resulting holding below the purchased share
        # count is internally inconsistent -- log and refuse to attach.
        if row.get("type") == "BUY" and val < int(row.get("shares") or 0):
            warnings.append("resulting_lt_shares")
            return
        row["resulting_shares"] = val

    if len(candidates) == 1 and len(rows) == 1:
        _guarded_attach(rows[0], candidates[0][1])
        return
    # Multi-candidate and/or multi-row: require an unambiguous surname match.
    for row in rows:
        matches = [val for (name, val) in candidates
                   if _res_surname_match(name, row.get("director"))]
        if len(set(matches)) == 1:
            _guarded_attach(row, matches[0])
        # else: no match or ambiguous -- stays None.


def parse_announcement(
    html: str,
    url: str,
    rns_id: str,
    announced_at: str,
    headline: str | None = None,
    ticker_hint: str | None = None,
) -> tuple:
    """Parse one PDMR filing's HTML into one or more extracted dicts.

    Sprint 3: try the BeautifulSoup table-aware path first; fall back
    to the legacy regex extractor when the standard two-table layout
    isn't present (foreign issuers, SIP variants, malformed HTML).

    Returns `(extracted_list, warnings_list, parser_source)` where
    `parser_source = 'regex'`.
    """
    parser_source = "regex"
    warnings: list = []

    text = html_to_text(html)

    # Ticker resolution — shared by both paths.
    ticker = _extract_ticker(text, headline=headline, url=url, html=html) or ticker_hint

    # ---- Path A2: section-aware extractor for bundled multi-PDMR filings ----
    # B-023 (2026-05-22): some bundled filings (e.g. AAL 8950385) have a
    # separate 18-row KV detail table per PDMR rather than one transaction
    # table with N rows. The section extractor walks each detail table and
    # emits one row per PDMR. Tried BEFORE Path A so a multi-PDMR filing
    # never falls through to the bundled-warning early-return that used to
    # zero-out these rows.
    section_rows, section_company = _extract_via_sections(html)
    if section_rows:
        out: list = []
        skipped_warnings: list = []
        for r in section_rows:
            r_w: list = list(r.get("warnings") or [])
            tx_date = r.get("date")
            director = r.get("director")
            tx_type = r.get("type")
            shares = int(r.get("shares") or 0)
            price = float(r.get("price") or 0.0)

            if not tx_date:
                r_w.append("could_not_parse_tx_date")
            if not ticker:
                r_w.append("could_not_extract_ticker")
            if not director:
                r_w.append("could_not_extract_PDMR_name")
            if not section_company:
                r_w.append("could_not_extract_company")
            if shares == 0 and tx_type not in {"GRANT", None}:
                r_w.append("zero_shares_non_grant")
            # D.3 (Sprint 9 Phase B) — silent price-extraction drop.
            # If price=0 on a real trade type (not GRANT/EXERCISE — those
            # are legit nil-cost), the price label or regex silently
            # failed. Drop the row rather than emit a value=0 misread
            # that would trip R1 in the gate (~457-row Class-1 bucket).
            if (price == 0.0
                    and tx_type is not None
                    and tx_type not in NIL_COST_CARVEOUT_TYPES):
                r_w.append("zero_price_non_grant")
                skipped_warnings.extend(r_w)
                continue
            if "foreign_currency" in r_w:
                warnings.append("foreign_currency")
                continue

            required_missing = not (tx_date and ticker and director and tx_type and shares)
            if required_missing:
                r_w.append("required_fields_missing")
                skipped_warnings.extend(r_w)
                continue

            value = round(price * shares, 2) if (price and shares) else 0.0
            fingerprint = _fingerprint(tx_date, ticker, director, tx_type, shares)
            extracted_row = {
                "fingerprint": fingerprint,
                "date": tx_date,
                "ticker": ticker,
                "company": section_company or "",
                "director": director,
                "role": r.get("role"),
                "type": tx_type,
                "shares": shares,
                "price": price,
                "value": value,
                "context": None,
                "url": url,
                "announced_at": announced_at,
                # Sprint 13: classify buy_strictness from the per-PDMR nature text.
                "buy_strictness": _classify_buy_strictness(r.get("nature") or ""),
            }
            # Sprint 9 Phase B — plausibility gate (reject mode R1-R4,
            # log-only for R5).
            ok, plaus_reasons = _plausibility_check(extracted_row)
            if not ok:
                _log_suspect_filing(extracted_row, plaus_reasons, url, rns_id)
                reject_reasons = [
                    rsn for rsn in plaus_reasons
                    if rsn != "R5_date_component_in_shares"
                ]
                if reject_reasons:
                    r_w.append(
                        "plausibility_rejected:" + ",".join(reject_reasons)
                    )
                    skipped_warnings.extend(r_w)
                    continue
                # R5 alone — emit but log warning.
                r_w.append("plausibility_flagged:R5_date_component_in_shares")
            out.append(extracted_row)
            warnings.extend(r_w)

        if out:
            # B-156: attach resulting_shares (None when not stated).
            _attach_resulting_shares(out, html, text, warnings)
            warnings.extend(skipped_warnings)
            return out, warnings, parser_source

    # ---- Path A1: aggregate / tranche-sum (year-as-shares refix Step B) ----
    # The MAR Article 19 template bundles per-tranche price/volume rows into a
    # nested sub-table with NO director-name column, with the PDMR name in a
    # separate "a) Name" KV row. _extract_via_table can't fan these out (it
    # drops every row for missing name and falls through to the legacy
    # year-bleeding regex). This path emits ONE aggregated row per filing:
    # summed/labelled volume, VWAP or labelled price, latest tranche date.
    agg_rows, agg_company = _extract_via_aggregate_table(html)
    if agg_rows and any(r.get("date") for r in agg_rows):
        out: list = []
        skipped_warnings: list = []
        for r in agg_rows:
            r_w: list = list(r.get("warnings") or [])
            tx_date = r.get("date")
            director = r.get("director")
            tx_type = r.get("type")
            shares = int(r.get("shares") or 0)
            price = float(r.get("price") or 0.0)

            if not tx_date:
                r_w.append("could_not_parse_tx_date")
            if not ticker:
                r_w.append("could_not_extract_ticker")
            if not director:
                r_w.append("could_not_extract_PDMR_name")
            if not agg_company:
                r_w.append("could_not_extract_company")
            if shares == 0 and tx_type not in {"GRANT", None}:
                r_w.append("zero_shares_non_grant")
            if (price == 0.0
                    and tx_type is not None
                    and tx_type not in NIL_COST_CARVEOUT_TYPES):
                r_w.append("zero_price_non_grant")
                skipped_warnings.extend(r_w)
                continue

            required_missing = not (tx_date and ticker and director and tx_type and shares)
            if required_missing:
                r_w.append("required_fields_missing")
                skipped_warnings.extend(r_w)
                continue

            value = round(price * shares, 2) if (price and shares) else 0.0
            fingerprint = _fingerprint(tx_date, ticker, director, tx_type, shares)
            extracted_row = {
                "fingerprint": fingerprint,
                "date": tx_date,
                "ticker": ticker,
                "company": agg_company or "",
                "director": director,
                "role": r.get("role"),
                "type": tx_type,
                "shares": shares,
                "price": price,
                "value": value,
                "context": None,
                "url": url,
                "announced_at": announced_at,
                "buy_strictness": _classify_buy_strictness(r.get("nature") or ""),
            }
            ok, plaus_reasons = _plausibility_check(extracted_row)
            if not ok:
                _log_suspect_filing(extracted_row, plaus_reasons, url, rns_id)
                reject_reasons = [
                    rsn for rsn in plaus_reasons
                    if rsn != "R5_date_component_in_shares"
                ]
                if reject_reasons:
                    r_w.append(
                        "plausibility_rejected:" + ",".join(reject_reasons)
                    )
                    skipped_warnings.extend(r_w)
                    continue
                r_w.append("plausibility_flagged:R5_date_component_in_shares")
            out.append(extracted_row)
            warnings.extend(r_w)

        if out:
            # B-156: attach resulting_shares (None when not stated).
            _attach_resulting_shares(out, html, text, warnings)
            warnings.extend(skipped_warnings)
            return out, warnings, parser_source
        # Aggregate row failed validation/gate — fall through; the legacy
        # paths below will surface the appropriate warnings / pending route.
        warnings.extend(skipped_warnings)

    # ---- Path A: table-aware (Sprint 3 B-001 / B-004 / B-016 / B-017) ----
    table_rows, table_company = _extract_via_table(html)
    if table_rows is not None and any(r.get("date") for r in table_rows):
        out: list = []
        skipped_warnings: list = []
        for r in table_rows:
            r_w: list = list(r.get("warnings") or [])
            tx_date = r.get("date")
            director = r.get("director")
            tx_type = r.get("type")
            shares = int(r.get("shares") or 0)
            price = float(r.get("price") or 0.0)

            if not tx_date:
                r_w.append("could_not_parse_tx_date")
            if not ticker:
                r_w.append("could_not_extract_ticker")
            if not director:
                r_w.append("could_not_extract_PDMR_name")
            if not table_company:
                r_w.append("could_not_extract_company")
            if not tx_type:
                # _classify_type already logged 'could_not_classify_type'
                pass
            if shares == 0 and tx_type not in {"GRANT", None}:
                r_w.append("zero_shares_non_grant")
            # D.3 (Sprint 9 Phase B) — silent price-extraction drop.
            # Same rule as the section path: price=0 on a real trade
            # type (not GRANT/EXERCISE) is a parser bug, not a real row.
            if (price == 0.0
                    and tx_type is not None
                    and tx_type not in NIL_COST_CARVEOUT_TYPES):
                r_w.append("zero_price_non_grant")
                skipped_warnings.extend(r_w)
                continue
            if "foreign_currency" in r_w:
                # Mirror legacy behaviour: foreign-currency rows are dropped.
                warnings.append("foreign_currency")
                continue

            required_missing = not (tx_date and ticker and director and tx_type and shares)
            if required_missing:
                r_w.append("required_fields_missing")
                skipped_warnings.extend(r_w)
                continue

            value = round(price * shares, 2) if (price and shares) else 0.0
            fingerprint = _fingerprint(tx_date, ticker, director, tx_type, shares)
            extracted_row = {
                "fingerprint": fingerprint,
                "date": tx_date,
                "ticker": ticker,
                "company": table_company or "",
                "director": director,
                "role": r.get("role"),
                "type": tx_type,
                "shares": shares,
                "price": price,
                "value": value,
                "context": None,
                "url": url,
                "announced_at": announced_at,
                # Sprint 13: classify buy_strictness from the document nature text.
                "buy_strictness": _classify_buy_strictness(r.get("nature") or ""),
            }
            # Sprint 9 Phase B — plausibility gate (reject mode R1-R4,
            # log-only for R5).
            ok, plaus_reasons = _plausibility_check(extracted_row)
            if not ok:
                _log_suspect_filing(extracted_row, plaus_reasons, url, rns_id)
                reject_reasons = [
                    rsn for rsn in plaus_reasons
                    if rsn != "R5_date_component_in_shares"
                ]
                if reject_reasons:
                    r_w.append(
                        "plausibility_rejected:" + ",".join(reject_reasons)
                    )
                    skipped_warnings.extend(r_w)
                    continue
                # R5 alone — emit but log warning.
                r_w.append("plausibility_flagged:R5_date_component_in_shares")
            out.append(extracted_row)
            warnings.extend(r_w)

        if out:
            # Successful table extraction — short-circuit. Any skipped-row
            # warnings are surfaced alongside the good rows so the caller
            # can log a "filing had N usable rows and M unparseable ones"
            # diagnostic if it cares.
            # B-156: attach resulting_shares (None when not stated).
            _attach_resulting_shares(out, html, text, warnings)
            warnings.extend(skipped_warnings)
            return out, warnings, parser_source

        # Every row from the table failed validation. Fall through to the
        # legacy path — it might recover a single row by reading flat
        # text (e.g. a SIP filing with a non-standard table header that
        # the legacy regex can still find via "Date of transaction").
        warnings.extend(skipped_warnings)

    # ---- Bundled-PDMR gate before legacy regex --------------------------
    # B-023 (2026-05-22): if we land here, both the section extractor and
    # the standard table extractor failed to fan out. If the filing still
    # *looks* like a bundle (multiple PDMR/PCA section headers), don't
    # let the legacy regex extract a single-PDMR row that mis-attributes
    # the transaction to whichever PDMR's data happens to match first.
    # Refuse to fan out, surface a warning, leave it for review.
    #
    # B-092 (2026-06-03): same-person multi-transaction guard.
    # Some PRN filings (e.g. ZigUp 9555699) contain TWO section tables for
    # the SAME director (e.g. one vesting + one EBT transfer).
    # _extract_via_sections found only one unique PDMR but both rows failed
    # type classification, landing us here.  This is NOT a multi-PDMR bundle
    # — firing the bundled gate mis-diagnoses the filing.  When section_rows
    # resolved to a single unique director, skip the bundled gate and let the
    # legacy regex make one more attempt.
    bundled_w = _bundled_name_warning(text)
    if bundled_w:
        if section_rows is not None:
            # Deduplicate by stripping any trailing parenthesised suffix
            # e.g. "Rachel Coulson (pdmr)" → "rachel coulson"
            unique_pdmrs = {
                (r.get("director") or "").lower().split("(")[0].strip()
                for r in section_rows
                if r.get("director")
            }
            if len(unique_pdmrs) != 1:
                # Genuinely multiple PDMRs — refuse to extract.
                return [], [bundled_w], parser_source
            # else: fall through — same-person multi-tx, let legacy try.
        elif _BUNDLED_PDMR_RE.search(text):
            # Strong bundle signal (Schroders-style numbered name list /
            # "Notification N of M") with no per-PDMR section tables — refuse,
            # because the legacy regex would mis-attribute the transaction to a
            # single director.
            return [], [bundled_w], parser_source
        # else (B-121 Mode-3): the bundled warning fired ONLY because a
        # "Details of PDMR / PCA" header was text-duplicated (a copy-paste error,
        # e.g. BOWL 8967255 — section 2 mis-pasted instead of "Reason for the
        # notification") while _extract_via_sections found <2 real section
        # tables and _BUNDLED_PDMR_RE doesn't match. This is a genuine
        # SINGLE-PDMR filing — fall through to the legacy extractor to recover it.

    # ---- Path B: legacy regex (fallback) ---------------------------------
    if not ticker:
        warnings.append("could_not_extract_ticker")

    tx_date = parse_iso_date(text)
    if not tx_date:
        warnings.append("could_not_parse_tx_date")

    # B-016/B-017 lift: if the table-aware path resolved a company name
    # from the issuer KV row (even though it couldn't extract any
    # transaction rows), prefer that over the flat-text regex. The
    # table-aware company has already been validated against the
    # MAR Article 19 boilerplate and against in-document director
    # names, so it's strictly more trustworthy than the legacy regex.
    company = table_company if table_company else None
    if not company:
        company = _extract_company(text, headline)

    director, role = _extract_director(text)

    # B-004 defence in the legacy path: ``_extract_director`` uses a
    # flat-text regex that can capture across cell boundaries, leaving
    # values like 'Kingfisher plc\nb' or 'Edward James Norman Wardle\nb'
    # in the director field. Apply the same cell-boundary validation we
    # use in the table-aware path, but with ``salvage_newline=True`` so
    # that a real name accidentally glued to the next cell's label
    # ("Edward James Norman Wardle\nb") is salvaged as the first line
    # rather than dropped entirely. Truly broken captures (single-token
    # fragments, company suffixes) are still rejected.
    if director:
        director = _validate_director_cell(director, company,
                                           salvage_newline=True)
        # Sprint 11 Fix #5 — normalise director-name casing at the
        # legacy-path emission boundary. Run AFTER _validate_director_cell
        # so the helper only sees survivor names (the validator may
        # return None, which the helper passes through unchanged).
        if director:
            director = _normalise_director_name(director)

    # B-017 defence (defence-in-depth for the rare case where neither
    # path resolved a clean company): if the candidate company equals
    # the legacy-extracted director, drop it -- a non-empty wrong
    # value is worse than an empty one because it pollutes the
    # dashboard and the clusters.
    if company and director and company.strip().lower() == director.strip().lower():
        company = None

    # Additional B-016 guard: even if we landed here without a
    # table_company, never emit the boilerplate as the resolved company.
    if company and _is_boilerplate_company(company):
        company = None

    if not company:
        warnings.append("could_not_extract_company")
    if not director:
        warnings.append("could_not_extract_PDMR_name")

    # Phase 3 (2026-06-02): classify SCOPED transaction text, never the whole
    # page. Whole-page text let a stray "disposal" in the Investegate news
    # ticker flip a buy into a sell (JMAT/GEN/UTL/CAD on 1-2 Jun).
    #
    # Fall back order, taking the FIRST input that yields a real type:
    #   (1) the scoped 'Nature of the transaction' cell, then
    #   (2) a tightly-bounded transaction block anchored on the tx-detail
    #       label (handles column-stacked layouts where the nature label and
    #       its value land on non-adjacent lines, so the scoped-cell capture
    #       grabbed the wrong line — e.g. the next column header).
    # Neither ever sees page chrome (sidebar / news-ticker / footer).
    tx_type, type_w = _classify_type(_scoped_nature_text(text))
    if tx_type is None:
        block = _bounded_tx_block(text)
        if block is not None:
            tx_type, type_w = _classify_type(block)
    warnings.extend(type_w)

    price_gbp, shares, pv_w = _parse_price_vol(text)
    warnings.extend(pv_w)

    # Sprint 11 Fix #2 — duplicate-number-pull guard (PRIMARY fix —
    # all 5 known bad rows in the live DB came from this legacy
    # regex path with parser_source='regex'). When the parser pulls
    # the same number into both `price` and `shares`, it's almost
    # always a nested-table extraction failure where the price and
    # volume cells point to the same source text. Threshold of 1000
    # on both fields preserves real edge cases like GAW 184sh × £184
    # (Games Workshop trades around £187 — confirmed real).
    if (price_gbp > 1000.0 and shares > 1000
            and abs(float(price_gbp) - float(shares)) < 0.5):
        price_gbp, shares = 0.0, 0
        warnings.append("duplicate_number_pull")

    if shares == 0 and tx_type not in {"GRANT", None}:
        warnings.append("zero_shares_non_grant")

    # C.1 — foreign-currency filing: never emit rows regardless of type.
    # The nil-cost EXERCISE/GRANT carve-out in D.3 must not bypass this
    # check — a USD-priced exercise written with price=0, value=0 is just
    # as wrong as a USD-priced buy. Route to pending for FX handling.
    if "foreign_currency" in warnings:
        return [], warnings, parser_source

    # D.3 (Sprint 9 Phase B) — silent price-extraction drop.
    # Legacy path: price=0 on a real trade type (not GRANT/EXERCISE) is
    # almost always the _PRICE_LABEL_RE matcher failing on a free-text
    # disclosure. Drop rather than emit value=0 misread.
    if (price_gbp == 0.0
            and tx_type is not None
            and tx_type not in NIL_COST_CARVEOUT_TYPES):
        warnings.append("zero_price_non_grant")
        return [], warnings, parser_source

    # B-023 touch 2026-05-22 — force FUSE flush after section-extractor add.
    required_missing = not (tx_date and ticker and director and tx_type and shares)
    if required_missing:
        warnings.append("required_fields_missing")
        return [], warnings, parser_source

    fingerprint = _fingerprint(tx_date, ticker, director, tx_type, shares)
    value = round(price_gbp * shares, 2) if (price_gbp and shares) else 0.0

    extracted = {
        "fingerprint": fingerprint,
        "date": tx_date,
        "ticker": ticker,
        "company": company or "",
        "director": director,
        "role": role,
        "type": tx_type,
        "shares": shares,
        "price": price_gbp,
        "value": value,
        "context": None,
        "url": url,
        "announced_at": announced_at,
        # Sprint 13: classify buy_strictness from full filing text (legacy path).
        "buy_strictness": _classify_buy_strictness(text),
    }

    # Sprint 9 Phase B — plausibility gate (reject mode R1-R4, log-only
    # for R5).
    ok, plaus_reasons = _plausibility_check(extracted)
    if not ok:
        _log_suspect_filing(extracted, plaus_reasons, url, rns_id)
        reject_reasons = [
            rsn for rsn in plaus_reasons
            if rsn != "R5_date_component_in_shares"
        ]
        if reject_reasons:
            warnings.append(
                "plausibility_rejected:" + ",".join(reject_reasons)
            )
            return [], warnings, parser_source
        # R5 alone — emit but log warning.
        warnings.append("plausibility_flagged:R5_date_component_in_shares")

    # B-156: attach resulting_shares (None when not stated).
    legacy_out = [extracted]
    _attach_resulting_shares(legacy_out, html, text, warnings)
    return legacy_out, warnings, parser_source


if __name__ == "__main__":  # CLI for ad-hoc parses
    # Sprint 11 Fix #5 — touch comment to force FUSE flush after build.
    import argparse
    import json
    from pathlib import Path

    ap = argparse.ArgumentParser()
    ap.add_argument("--rns-id", required=True)
    ap.add_argument("--html-path", required=True)
    ap.add_argument("--url", default="")
    ap.add_argument("--announced-at", default="")
    args = ap.parse_args()
    html = Path(args.html_path).read_text(encoding="utf-8", errors="replace")
    extracted, warnings, source = parse_announcement(
        html, args.url, args.rns_id, args.announced_at
    )
    print(json.dumps({
        "parser_source": source,
        "extracted": extracted,
        "warnings": warnings,
    }, indent=2))
