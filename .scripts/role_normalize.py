"""Map the raw `role` free-text into one of 14 canonical buckets.

Public surface:
    BUCKETS          -- The 14 canonical signal buckets plus Parser fragment.
    normalize_role() -- Pure function: raw role string -> canonical bucket.

Design:
    - Deterministic. Pure function. No I/O.
    - Most-specific keyword first. Precedence order matters.
    - PCA before any exec title ("PCA of CEO" must be PCA, not CEO).
    - Founder before any other exec title.
    - Divisional / Regional Exec before CEO/CFO/Other Chief (so regional
      CEOs don't leak into the highest-conviction T1 cohort).
    - Non-Exec Chair before Chair before NED before generic Director.

The mapper is intentionally conservative: when in doubt, route to
"Other / unclassified" rather than to a load-bearing bucket.

Spec: docs/specs/role-normalization-pass.md.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Canonical buckets
# ---------------------------------------------------------------------------
# These strings are the source of truth. Downstream code (chips, signal
# engine, performance tiles) must match exactly. Do not rename without
# coordinating with the cosmetic chip code (role_chip / roleChipCls) and
# the eventual Phase B cut-over of signals/roles.py.

CEO = "CEO"
CFO = "CFO"
OTHER_CHIEF = "Other Chief"
CHAIR_EXEC = "Chair (executive)"
CHAIR_NON_EXEC = "Non-Exec Chair"
NED = "NED"
EXEC_DIRECTOR = "Executive Director"
DIVISIONAL = "Divisional / Regional Exec"
FOUNDER = "Founder"
PRESIDENT_VP = "President / VP"
COMPANY_SECRETARY = "Company Secretary / General Counsel"
PCA = "PCA"
PDMR_ONLY = "PDMR-only"
OTHER = "Other / unclassified"
PARSER_FRAGMENT = "Parser fragment"

# Order is documentation; not used by the mapper.
BUCKETS: tuple[str, ...] = (
    CEO, CFO, OTHER_CHIEF, CHAIR_EXEC, CHAIR_NON_EXEC, NED,
    EXEC_DIRECTOR, DIVISIONAL, FOUNDER, PRESIDENT_VP,
    COMPANY_SECRETARY, PCA, PDMR_ONLY, OTHER, PARSER_FRAGMENT,
)

# ---------------------------------------------------------------------------
# Abbreviation / shorthand lookup (exact match, case-insensitive, post-strip)
# ---------------------------------------------------------------------------
# Manual-add entries often use 2–3 letter shorthands that the full pattern
# rules below can't recognise (they require substantive keywords). Checked
# before any pattern rule; fragments still take precedence (see normalize_role).
_ABBREV_EXACT: dict[str, str] = {
    "ned":    NED,
    "sid":    NED,             # Senior Independent Director
    "ceo":    CEO,
    "cfo":    CFO,
    "fd":     CFO,             # Finance Director
    "md":     EXEC_DIRECTOR,   # Managing Director
    "coo":    OTHER_CHIEF,
    "cto":    OTHER_CHIEF,
    "cmo":    OTHER_CHIEF,
    "cosec":  COMPANY_SECRETARY,
    "co sec": COMPANY_SECRETARY,
    "gc":     COMPANY_SECRETARY,  # General Counsel
}


# ---------------------------------------------------------------------------
# Corporate-actor detection (B-136 — scoring scope: individuals only)
# ---------------------------------------------------------------------------
# Rupert's policy (2026-06-06): the analysis scores only STRAIGHTFORWARD buys
# and sells by INDIVIDUAL directors / individual PCAs — not corporate holders
# (companies, trusts, nominees, funds, family offices). Corporate-actor rows
# are still STORED, but excluded from SCORING (signal firing, active clusters,
# CAR/performance, return columns).
#
# This is a name heuristic, deliberately a SUPERSET of the parser's narrow
# `_CORP_SUFFIX_RE` (which only catches hard legal suffixes <40 chars and so
# leaks trusts/nominees/funds and long names). It is conservative-but-broad;
# review the flagged set with `_diag_corporate_actors.py` before a rebuild so
# a real individual with a corporate-sounding surname isn't wrongly excluded.
_CORP_ACTOR_RE = re.compile(
    r"\b("
    r"plc|ltd|limited|llp|llc|inc|incorporated|corp|corporation|"
    r"gmbh|ag|n\.?v\.?|s\.?a\.?|s\.?p\.?a\.?|s\.?à\.?r\.?l\.?|pte|bv|"
    r"holdings?|investments?|capital|partners|nominees?|trustees?|trust|"
    r"ventures?|securities|equities|asset\s+management|"
    r"pension|foundation|fund|funds|"
    r"&\s*co|&\s*company"
    r")\b",
    re.IGNORECASE,
)


# Custody / nominee carve-out: an INDIVIDUAL whose holding is parked in a
# broker-nominee account ("Mark Robson Shares held in accounts held with
# Hargreaves Lansdown (Nominees) Limited", "John Morgan Held In Chase Nominees
# Limited"). The leading actor is a real person — the corporate words are
# custody boilerplate the parser failed to strip — so these must NOT be
# excluded from scoring. (A "Name on behalf of <Company> Limited" is the
# opposite case — an agent buying FOR a company — and stays flagged.)
_CUSTODY_INDIVIDUAL_RE = re.compile(
    r"\bheld\s+(?:in|with)\b|\baccounts?\s+held\b", re.IGNORECASE)


def is_corporate_actor(name: str | None) -> bool:
    """Heuristic: True when an actor NAME denotes a corporate entity (company,
    trust, nominee, fund, family office) rather than an individual.

    Used to EXCLUDE corporate holders from scoring (B-136) — signals, active
    clusters, CAR/performance, return columns. Parsed rows remain STORED; this
    only gates scoring at read time. Pure / no I/O. Conservative-but-broad;
    audit the flagged set with `_diag_corporate_actors.py`.

    Carve-out: an individual whose shares are held via a broker nominee
    ("<Person> ... held in/with ... Nominees Limited") is NOT corporate.
    """
    if not name:
        return False
    if _CUSTODY_INDIVIDUAL_RE.search(name):
        return False
    return bool(_CORP_ACTOR_RE.search(name))


# Related-party signals — these are KEPT in scoring even when corporate
# (Rupert 2026-06-06: "do not exclude family trusts or corporate PCAs").
# A PCA (person closely associated) — whether an individual, a family trust,
# or a company controlled by/associated with the PDMR — is a reportable
# related-party dealing and a legitimate conviction signal. Only ARMS-LENGTH
# corporate holders (funds, asset managers, investment companies that are NOT
# a PCA of any director) are excluded.
_PCA_ROLE_RE = re.compile(
    r"\bpca\b|closely\s+associated|\btrustee", re.IGNORECASE)
_FAMILY_TRUST_RE = re.compile(
    r"family\s+trust|grandchildren|\bsettlement\b|will\s+trust", re.IGNORECASE)


def is_related_party(role_normalized: str | None = None,
                     role: str | None = None,
                     name: str | None = None) -> bool:
    """True when the actor is a PCA / closely-associated party (incl. corporate
    PCAs and family trusts) — KEPT in scoring. Checks the canonical PCA bucket,
    then the raw role text (PCA / closely associated / trustee), then a
    family-trust name pattern as a backstop when the role tag is missing.
    """
    if (role_normalized or "").strip().upper() == PCA.upper():
        return True
    if role and _PCA_ROLE_RE.search(role):
        return True
    if name and _FAMILY_TRUST_RE.search(name):
        return True
    return False


def exclude_corporate_from_scoring(name: str | None,
                                   role_normalized: str | None = None,
                                   role: str | None = None) -> bool:
    """True when an actor should be EXCLUDED from scoring (B-136): a CORPORATE
    entity that is NOT a related party. Arms-length corporate holders (funds,
    asset managers, investment companies) are excluded; corporate PCAs and
    family trusts are KEPT. This is the predicate the signal engine / clusters
    use — not the bare `is_corporate_actor`.
    """
    if not is_corporate_actor(name):
        return False
    return not is_related_party(role_normalized, role, name)


# ---------------------------------------------------------------------------
# Parser-fragment detection (data-quality flag)
# ---------------------------------------------------------------------------
# Strings that obviously aren't roles — PDF table headers, sentence
# fragments leaked from the parser, column names. These are surfaced for
# Sprint 3 reparse, not silently buried in "Other".

_FRAGMENT_SIGNALS = (
    "number of shares",
    "number of ordinary",
    "number of partnership",
    "number of share awards",
    "number of vested",
    "number of ltip",
    "number of awards",
    "no. of shares",
    "no. of share",
    "price paid",
    "shares purchased",
    "date of",
    "ltip grant",
    "maximum number",
    "nature of the transaction",
    "existing interest",
    "details of awards",
    "at the date of grant",
    "as per 1(a)",
)


def _is_parser_fragment(raw: str) -> bool:
    """Return True if `raw` looks like a PDF parser artefact, not a role."""
    s = raw.strip()
    if not s:
        return False  # blank goes to OTHER, not PARSER_FRAGMENT
    sl = s.lower()

    # Table-row leak (literal pipe characters from a markdown-style table)
    if "|" in s:
        return True

    # Starts with punctuation / parentheses / asterisks
    if s.startswith(("(", "*", ",")):
        return True
    if sl in ("1a)", "(1a)"):
        return True

    # Known fragment substrings
    if any(sig in sl for sig in _FRAGMENT_SIGNALS):
        return True

    # Long string with no role keyword anywhere → truncated description
    role_keyword = re.compile(
        r"\b(director|officer|chief|chair|ceo|cfo|coo|pdmr|pca|president|"
        r"founder|secretary|counsel|vp|md|partner|manager|head\s|treasurer|"
        r"controller|trustee)\b",
        re.IGNORECASE,
    )
    if len(s) > 80 and not role_keyword.search(s):
        return True

    # Starts with lowercase letter (mid-sentence leak), excluding known
    # legitimate lowercase starts.
    if s[0].islower() and not sl.startswith((
        "group ",
        "non-",
        "non ",
        "non- ",
        "within ",
        "interim ",
        "this ",
        "i) ",
        "or status",
        "ing ",
        "ed ",
        "of ",
        "tho",
    )):
        return True

    return False


# ---------------------------------------------------------------------------
# PCA detection (highest precedence after parser fragments)
# ---------------------------------------------------------------------------
_PCA_SIGNALS = (
    "pca ", "pca/", "pca-", "(pca", "pca to", "pca of", "pca with",
    "pca,", "pca.", " pca", "/pca", "- pca",
    "closely associated", "closely assoc",
    "spouse of", "spouse)", "spouse,", "wife of", "daughter",
    "family trust", "jolly foundation",
    "connected person", "connected to",
)


def _is_pca(sl: str) -> bool:
    """Return True if the lowercased role string indicates a PCA."""
    if sl == "pca":
        return True
    if sl.startswith("pca"):
        return True
    if any(sig in sl for sig in _PCA_SIGNALS):
        return True
    return False


# ---------------------------------------------------------------------------
# Divisional / Regional detection
# ---------------------------------------------------------------------------
# A title is divisional when it carries explicit geography or a business-
# unit qualifier. The marker is usually "[title], [region]" or "[title]
# [region]" or specific business-unit names.

_REGION_WORDS = (
    "north america", "europe", "apac", "asia pacific", "asia",
    "uk & ireland", "uk and ireland", "continental", "americas",
    "latin america", "south east", "australia", "romania", "poland",
    "israel", "central europe", "iberia", "emea", "cee", "great britain",
    "savills uk", "rolls-royce", "cellulose", "argos",
    "consumer lending", "investments & strategy", "vodacom",
    "investor relations", "flexible packaging", "higher education",
    "partnerships & regeneration", "private bank", "wealth management",
    "us consumer", "banking and expansion", "food", "iron ore", "copper",
    "recruitment ireland", "recruitment gb",
    "aircraft", "high net worth", "azerbaijan",
    "simulation", "avon protection", "advanced wound", "nutrition",
    "english language", "industrial & commercial", "thermal products",
    "energean israel", "civil aerospace", "defence",
    "bytes software", "tooru", "gilini", "regional director",
    "country manager", "of dp aircraft", "regional ceo",
    "growth markets",
)


def _is_divisional(s: str) -> bool:
    """Return True if the role string carries divisional / regional scope.

    Care is taken to NOT match group-level titles ("Group CEO" stays CEO).
    """
    sl = s.lower()
    if any(rw in sl for rw in _REGION_WORDS):
        return True
    # Pattern: "CEO, [non-corporate-function]" or "President - [region]"
    # Excludes "CEO, Director", "President and Founder", etc.
    if "," in sl:
        head, tail = sl.split(",", 1)
        head = head.strip()
        tail = tail.strip()
        if head in ("ceo", "president") and tail and not any(
            g in tail for g in (
                "director", "pdmr", "chair", "founder", "executive",
                "designate", "interim", "group", "deputy",
            )
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Main mapper
# ---------------------------------------------------------------------------

def normalize_role(raw: str | None) -> str:
    """Map a raw `role` string to one of the canonical buckets.

    Never raises. Returns OTHER for None or empty input.

    Precedence order (most-specific first):
        1. Parser fragment   (data-quality flag)
        2. PCA               (must beat all exec titles)
        3. Founder           (must beat CEO / President / Exec Director)
        4. Divisional/Regional Exec (must beat CEO / CFO / Other Chief)
        4a. NED-Chair        (Rupert Q4, broadened: ANY non-exec or
                              independent chair role — "Chair", "Chairman",
                              "Chair of the Board" — buckets with NEDs.
                              The legacy CHAIR_NON_EXEC bucket is now
                              unreachable from normalize_role.)
        5. NED               (must beat generic Director)
        6. CEO               (must beat Chair-exec — "Chief Executive
                              Officer and Chairman" routes to CEO)
        7. CFO
        8. Other Chief
        9. Company Secretary / General Counsel
        10. Executive Director (incl. MD + operational-function directors)
        11. Chair (executive) — runs AFTER CEO/CFO/Other Chief/Exec
                                Director so any combination that names
                                a more-specific exec title wins. Catches
                                bare "Chair" / "Executive Chair".
        12. President / VP
        13. PDMR-only
        14. Bare "Director" -> NED (UK convention)
        15. Other / unclassified

    Precedence-bug history (2026-05-21):
      * Bug A — Chair-executive rule used to fire before CEO, so
        "Chief Executive Officer and Chairman" returned Chair instead
        of CEO. Fixed by moving Chair-executive to after the Exec
        Director rules.
      * Bug B — Non-Exec Chair rule used to fire before NED, so
        "Non-Executive Chairman" returned CHAIR_NON_EXEC instead of
        NED. Fixed by adding rule 4a above the Non-Exec Chair rule.
      * Q4 broadening (2026-05-21, same day) — rule 4a originally
        narrow to "chairman"; widened to all chair forms so
        "Non-Executive Chair" and "Independent Non-Executive Chairman"
        also route to NED. Old rule 5 (CHAIR_NON_EXEC) deleted as
        dead code.
    See test_classify_role.py test_02/test_13/test_21 and
    test_role_normalize.py TestChairNonExec for the canonical contracts.
    """
    if raw is None:
        return OTHER
    s = raw.strip()
    if not s:
        return OTHER

    sl = s.lower()

    # 1. Exact abbreviation / shorthand match — checked BEFORE fragment
    # detection so that known shorthands like "cosec" (lowercase) aren't
    # caught by the starts-with-lowercase fragment heuristic.
    if sl in _ABBREV_EXACT:
        return _ABBREV_EXACT[sl]

    # 2. Parser fragment
    if _is_parser_fragment(s):
        return PARSER_FRAGMENT

    # 3. PCA (highest precedence after fragments)
    if _is_pca(sl):
        return PCA

    # 3. Founder (anything containing "founder")
    if "founder" in sl:
        return FOUNDER

    # 4. Divisional / Regional Exec (before CEO/CFO/Other Chief)
    if _is_divisional(s):
        return DIVISIONAL

    has_chair = ("chair" in sl) or ("chairman" in sl)
    has_non_exec = (
        "non-exec" in sl or "non exec" in sl
        or "non-executive" in sl or "non executive" in sl
        or "non- executive" in sl
    )

    # 4a. NED-Chair / NED-Chairman (Rupert Q4, broadened 2026-05-21).
    # Any chair role that's non-executive (or explicitly independent,
    # which implies non-exec in UK governance) buckets with NEDs, not
    # Chairs — Rupert's locked decision is that these directors govern
    # like NEDs in substance regardless of the "Chair" / "Chairman" /
    # "Chair of the Board" wording. The test_13 / test_21 comment in
    # test_classify_role.py captures the narrow form; the TestChairNonExec
    # class in test_role_normalize.py captures the broad form.
    #
    # Rule 5 (which previously returned CHAIR_NON_EXEC for the same
    # condition) was deleted on 2026-05-21 because it's now dead code.
    # The CHAIR_NON_EXEC constant is retained for backward compatibility
    # but normalize_role no longer returns it.
    if has_chair and (has_non_exec or "independent" in sl):
        return NED

    # 6. NED — Non-Executive Director (before generic Director)
    if has_non_exec and "director" in sl:
        return NED
    if "senior independent" in sl:
        return NED
    if "supervisory board" in sl:
        return NED
    if "associate employee director" in sl:
        return NED

    # Chair (executive) used to sit here. Moved to after rule 12a so a
    # title that mentions BOTH "Chairman" and a more-specific exec
    # title (CEO, CFO, Other Chief, Executive Director) routes to the
    # more-specific bucket. See Bug A in the precedence-history note
    # at the top of this function.

    # 7. CEO / Chief Executive
    # Must exclude other "Chief" titles like Chief Investment Officer,
    # Chief Operating Officer, Chief Financial Officer, etc.
    if re.search(r"\bceo\b", sl) or "chief executive" in sl:
        if not any(other in sl for other in (
            "chief executive of private bank",  # divisional-shaped
        )):
            return CEO
    if (
        sl == "chief executive"
        or sl.startswith("chief executive ")
        or " chief executive " in (" " + sl + " ")
    ):
        return CEO

    # 9. CFO / Finance Director
    if (
        re.search(r"\bcfo\b", sl)
        or "chief financial" in sl
        or "chief finance" in sl
        or "finance director" in sl
        or "financial director" in sl
        or "finance and operations director" in sl
    ):
        return CFO

    # 10. Other Chief (COO/CTO/CMO/CRO/CHRO/CCO/etc.)
    if "chief" in sl or re.search(r"\bcoo\b", sl):
        return OTHER_CHIEF

    # 11. Company Secretary / General Counsel
    if (
        "company secretary" in sl
        or "general counsel" in sl
        or "group legal" in sl
    ):
        return COMPANY_SECRETARY

    # 12. Executive Director (incl. MD and operational-function directors)
    if (
        "executive director" in sl
        or "managing director" in sl
        or sl == "executive"
        or sl.startswith("md ")
        or "group managing" in sl
        or "deputy chief executive" in sl
    ):
        return EXEC_DIRECTOR
    # 12a. Operational-function director titles. In UK plc governance,
    # "<Function> Director" with an operational qualifier is almost
    # always an executive board director, not a NED. This rule must
    # come BEFORE the bare-Director → NED fallback (rule #15).
    _OPERATIONAL_DIRECTOR_PATTERNS = (
        "business development director",
        "business transformation director",
        "commercial director",
        "corporate finance director",
        "corporate affairs director",
        "operations director",
        "commercial and operations director",
        "technical director",
        "marketing director",
        "sales director",
        "people director",
        "hr director",
        "human resources director",
        "it director",
        "technology director",
        "customer director",
        "market development director",
        "director, business development",
        "director, corporate",
        "director of corporate development",
        "director of land",
        "director of group",
        "director of external affairs",
        "director of investment",
        "director of artemis",
        "director, digital",
        "director, legal",
        "director of dp aircraft",
    )
    if any(pat in sl for pat in _OPERATIONAL_DIRECTOR_PATTERNS):
        return EXEC_DIRECTOR

    # 12b. Chair (executive) — moved here from its original rule-7 spot
    # so a title that names BOTH "Chairman" and a more-specific exec
    # title (CEO, CFO, Other Chief, Exec Director, MD) routes to the
    # more-specific bucket. Without this move, "Chief Executive Officer
    # and Chairman" returned CHAIR_EXEC instead of CEO. See Bug A in
    # the precedence-history note at the top of this function.
    if has_chair:
        return CHAIR_EXEC

    # 13. President / VP (US-style)
    if (
        "president" in sl
        or "vice president" in sl
        or re.search(r"\bvp[\s,]", sl)
        or sl.startswith("vp ")
        or "svp" in sl
        or "evp" in sl
        or "senior executive vice" in sl
    ):
        return PRESIDENT_VP

    # 14. PDMR-only (no other title disclosed)
    if "pdmr" in sl:
        # Bare PDMR with no other qualifier
        return PDMR_ONLY

    # 15. Bare "Director" or "Director (no qualifier)" → NED (UK convention)
    if "director" in sl:
        return NED

    # 16. Catch-all
    return OTHER
