# Stage 4 — Signal engine + backtest

**Status:** Plan v1.0 — 2026-05-14. Awaiting Rupert sign-off before implementation.
**Owner:** Rupert
**Target ship:** One focused session (~6–8 hours of build), plus ~5 minutes of operational wall clock for the first end-to-end backtest run.
**Source:** `docs/specs/05-phase-3-signal-engine.md` (canonical signal taxonomy, CAR method, walk-forward discipline, cost model), `docs/specs/01-cluster-detector.md` (cluster connected-component definition), `docs/specs/stage-01-plan.md` (locked DB schema — `signals`, `backtest_runs`, `paper_trades` tables already exist), `docs/specs/stage-02-plan.md` (transactions populated), `docs/specs/stage-03-plan.md` (prices + `tickers_meta` populated).
**Author:** Planning pass, 2026-05-14.

---

## Goal

Turn the 2,383 PDMR transactions (1,387 BUYs across 141 tickers) and 38,095 price rows into a set of measurable buy-signal firings, each with a forward-looking cumulative abnormal return (CAR) profile vs the FTSE All-Share benchmark, net of UK trading costs (50bps spread + 0.5% stamp duty on non-AIM buys). Every firing is evaluated walk-forward so a synthetic trader at the moment the filing went public could not see a single price or filing it had not yet seen — the lookahead-bias test is the non-negotiable gate. The output is two things: the `signals` table populated with one row per (signal_id, signal_version, fingerprint) firing, and a `.data/_backtest_results.csv` artefact carrying the CAR profile per firing so Stage 5's dashboard can render it.

After Stage 4, every BUY in the transactions table has been evaluated against the 7-signal taxonomy (T0/T1/T2/T3/T4/S1/F1), every firing has a CAR profile at the windows that are available, and `test_p3_lookahead.py` proves the engine refuses to peek at the future. No costs heuristics, no cohort cuts, no dashboard — those are downstream.

---

## Files to create

| Path (absolute Windows) | Purpose |
|---|---|
| `C:\Dev\DirectorsDealings\.scripts\signals\__init__.py` | Registry. Maps `signal_id` -> `(module, SIGNAL_VERSION, evaluate_fn)` for each of the 7 signals. Exposes `iter_signals()` and `get_signal(signal_id)`. Single source of truth for what signals exist. |
| `C:\Dev\DirectorsDealings\.scripts\signals\t0_cluster_opportunistic_v1.py` | Combo signal. Fires when a transaction is already firing S1 AND (T1 OR T2). Reads from the as-of view of `signals` rather than recomputing — see decision D-T0-OVERLAP. |
| `C:\Dev\DirectorsDealings\.scripts\signals\t1_ceo_cfo_buy_v1.py` | CEO/CFO opportunistic buy. `type=BUY` AND role matches CEO/CFO regex AND `value >= 100_000`. |
| `C:\Dev\DirectorsDealings\.scripts\signals\t2_exec_buy_v1.py` | Other-exec opportunistic buy. `type=BUY` AND role matches exec regex AND `value >= 25_000` AND NOT already T1. |
| `C:\Dev\DirectorsDealings\.scripts\signals\t3_ned_buy_v1.py` | NED opportunistic buy. `type=BUY` AND role matches NED regex AND `value >= 10_000` AND NOT already T1/T2. |
| `C:\Dev\DirectorsDealings\.scripts\signals\t4_other_buy_v1.py` | Catch-all discretionary buy. `type=BUY` AND `value >= 1_000` AND NOT already T1/T2/T3. |
| `C:\Dev\DirectorsDealings\.scripts\signals\s1_cluster_buy_v1.py` | Cluster buy. Reads `transactions.cluster_id` (populated by `detect_clusters.py`); fires if the row has a non-NULL cluster_id whose connected-component visible-as-of `as_of` has >=2 distinct directors. |
| `C:\Dev\DirectorsDealings\.scripts\signals\f1_first_time_buy_v1.py` | First-time buy. `type=BUY` AND no prior `BUY` row for `(director, ticker)` with earlier `announced_at`. |
| `C:\Dev\DirectorsDealings\.scripts\signals\roles.py` | Role-classifier. Exports `classify_role(role: str) -> Literal['CEO_CFO','EXEC','NED','OTHER']`. Regex precedence ladder. Used by T1/T2/T3 and tested independently. |
| `C:\Dev\DirectorsDealings\.scripts\detect_clusters.py` | Connected-component cluster detector per spec 01. Reads `transactions` (BUYs only), writes `cluster_id` back to `transactions`. Idempotent. Walk-forward aware: accepts `as_of` so a cluster is only valid if all its constituent BUYs were announced on or before `as_of`. |
| `C:\Dev\DirectorsDealings\.scripts\eval_signals.py` | Orchestrator CLI. Walks `transactions` in `announced_at` order, calls each signal's `evaluate()`, writes results to `signals`. Pre-step: idempotently runs `detect_clusters.detect(conn, as_of)` first. Flags: `--from`, `--to`, `--signal SIGNAL_ID`, `--as-of YYYY-MM-DD`, `--rebuild`, `--verbose`. |
| `C:\Dev\DirectorsDealings\.scripts\backtest.py` | Event-study harness. For each row in `signals`, looks up T+1 entry close, T+21/T+90/T+252 exit closes, computes raw + benchmark returns, CARs, net-of-cost CARs. Writes `.data\_backtest_results.csv`. CLI: `--signal SIGNAL_ID`, `--signal-version`, `--from`, `--to`, `--output`, `--run-id`, `--verbose`. Records a row in `backtest_runs`. |
| `C:\Dev\DirectorsDealings\.scripts\test_p3_lookahead.py` | The non-negotiable lookahead-bias gate. Synthetic store with prices for D and D+5. Asserts that as-of D, no signal evaluator sees the D+5 row, and that the backtest cannot use a price strictly after `as_of`. Standalone — does not touch the live DB. |
| `C:\Dev\DirectorsDealings\.scripts\test_stage_04.py` | Main test suite, >=20 cases. Each signal's fire-and-don't-fire logic, role classifier precedence, cluster connected-component edge cases, CAR computation with known inputs, cost-application correctness, T+252 trading-day fallback, walk-forward enforcement. Self-cleaning via temp-DB monkey-patch. |

Runtime-created artefact (gitignored, kept under `.data/`):

| Path | Created by | Purpose |
|---|---|---|
| `C:\Dev\DirectorsDealings\.data\_backtest_results.csv` | `backtest.py` | One row per (signal_id, signal_version, fingerprint) with entry/exit prices, raw returns, benchmark returns, CARs (gross + net of cost), `windows_available` bitmask, ticker, role, value. Re-written atomically on each `backtest.py` run. |
| `C:\Dev\DirectorsDealings\.data\_backtest_progress.json` | `backtest.py` (optional) | Per-run progress for resumability if Rupert kills mid-run. v1 plan: only written if `--resume` flag is passed; otherwise backtest runs in-memory and rewrites the CSV atomically. |

Stdlib only. No new third-party packages.

---

## Decision points (with options + recommendation)

### D-WINDOW — Backtest window: include partial-window firings?

A firing on 2026-04-20 with today being 2026-05-14 has only T+1 available (T+21 not yet observable, T+90/T+252 way off). Two options:

- **(a) Skip any firing missing all four forward closes (the strict-window approach).** Clean averages, low-N.
- **(b) Include every firing with at least T+1 available; carry a `windows_available` bitmask column so the dashboard can filter.** Higher-N for short windows, transparent.

**Recommendation: (b).** A firing with only T+1 is still useful in Stage 5's "new firings" panel and the Stage 4 CSV is the input. `windows_available` is a string like `t1,t21` or `t1,t21,t90,t252`. Stage 5 filters when computing pooled means.

### D-RESULTS-STORE — `signals_returns` table vs `.data\_backtest_results.csv`?

Two options:

- **(a) New `signals_returns` table** keyed `(signal_id, signal_version, fingerprint, run_id)`. Joinable to `signals` in SQL.
- **(b) CSV at `.data\_backtest_results.csv`.** Atomically rewritten each run.

**Recommendation: (b) CSV for v1.** The artefact is inspectable in Excel, diffable in git history (it's small — ~5k firings × ~30 columns = ~150 KB), trivial to share. Stage 5's dashboard can stream it. No schema change. Stage 1 schema is frozen and adding a new table would require a forward migration. The `backtest_runs` table is the system-of-record for "this run happened"; the CSV is the system-of-record for "these are the numbers". If Stage 6+ needs SQL joinability we promote to a table then (one-shot loader). Recommend coexist with a `run_id` column in the CSV.

### D-COSTS-MODEL — How to surface 50bps spread + 0.5% UK stamp duty?

- **(a) Single `net_return` column with cost deducted from raw return.** Clean.
- **(b) Show both `raw_return_*` and `net_*` columns side-by-side, with a `cost_bps` column for transparency.**

**Recommendation: (b).** Round-trip: 50bps on every trade (entry + exit = ~50bps total for v1, conservative, per spec) plus 50bps stamp on non-AIM BUYs at entry only (stamp is one-sided in the UK, paid on the purchase). Total `cost_bps`:
- AIM ticker (`is_aim=1`): 50bps (spread only).
- Non-AIM ticker (`is_aim=0`): 100bps (50 spread + 50 stamp).

`net_car_t1 = car_t1 - (cost_bps / 10_000)` and analogously for T+21/T+90/T+252. Identical cost across windows — costs are paid once, returns accrue over time, so `net_car_t252 - car_t252 = -cost_bps/10000` is the same minus as for T+1. The CSV carries: `raw_return_t1, ..., raw_return_t252, benchmark_return_t1, ..., benchmark_return_t252, car_t1, ..., car_t252, cost_bps, net_car_t1, ..., net_car_t252`. The dashboard picks which to render.

Spec 05 says "50bps round-trip spread + 0.5% UK stamp duty on buys". 50bps round-trip means 25bps each leg, but the spread-cost convention in event studies is to charge it on entry (the half-spread paid to cross at entry is one's exposure for the trade's life, exits get the symmetric half). The plan applies 50bps as a one-shot cost on the trade. Open question if Rupert wants to split into 25bps + 25bps (no material P&L difference, just labelling).

### D-ROLE-CLASSIFICATION — How to bucket `transactions.role` into T1/T2/T3 tiers?

The role field is free text. Examples observed in the codebase / spec: `"Group Chief Executive"`, `"Chief Financial Officer"`, `"Non-Executive Director"`, `"Senior Independent Director"`, `"Chairman"`, `"Executive Director"`, `"Chief Operating Officer"`, `"Group General Counsel"`.

**Recommendation: explicit precedence ladder in `.scripts/signals/roles.py`. Match case-insensitively in this order:**

1. `r"\b(chief\s+executive|chief\s+financial|c\.?e\.?o\.?|c\.?f\.?o\.?)\b"` -> `CEO_CFO`
2. `r"\b(non[- ]?exec(?:utive)?(?:\s+director)?|ned)\b"` -> `NED` (must check before "director" alone, NEDs contain the substring "Director")
3. `r"\b(chair(?:man|woman|person)?|chief\s+\w+\s+officer|executive\s+director|managing\s+director|group\s+(?:general\s+counsel|chief))\b"` -> `EXEC`
4. else -> `OTHER`

T1 fires if `classify_role(role) == 'CEO_CFO'`. T2 fires if `classify_role(role) == 'EXEC'` AND NOT already T1. T3 fires if `classify_role(role) == 'NED'` AND NOT already T1/T2. T4 is the catch-all over BUYs >= £1,000 not firing T1/T2/T3.

Test cases enumerate at least the eight role variants above, plus pathological "Director and Company Secretary" / "Group Chief Risk Officer" (-> EXEC because of `chief\s+\w+\s+officer`) / "Senior Independent Director" (-> EXEC via `executive\s+director` would miss; needs explicit rule — see test 11 in the table below for the SID case which becomes a "no match" -> T4 firing). Open question Q-SID below — Rupert may want SID/independent directors slotted into NED tier; left as T4 for v1 conservatism.

### D-FX — `prices.close IS NULL` rows

Stage 3 leaves `prices.close` as `NULL` only for rows that hit `unsupported_currency` (USD/EUR depositary lines). The universe filter must drop these.

**Recommendation: confirm. Universe SQL: `WHERE tickers_meta.benchmark_symbol IS NOT NULL`** is the canonical filter. Stage 3 sets `benchmark_symbol = NULL` for both delisted and unsupported-currency tickers, so this one filter handles both. The `prices` table itself has `close NOT NULL`, so this is consistent: a price row exists only if it has a numeric close.

### D-EX-DIV — Yahoo `adjclose` handling

Stage 3 fetches `indicators.adjclose[0].adjclose[]`. Yahoo's adjusted-close already incorporates dividends and splits.

**Recommendation: confirm. No special handling.** The `prices.close` column for stocks contains the split- and dividend-adjusted close.

### D-INSUFFICIENT-HISTORY — Tickers with <30 trading days pre-announce

A firing where the ticker has fewer than 30 trading days of price history before `announced_at` cannot be confidently evaluated (no baseline volatility, no idiosyncratic stats).

**Recommendation: skip these firings and log them.** `backtest.py` writes a `_backtest_skips.json` with `[{fingerprint, reason, ticker, announced_at}]` so they are visible. The `signals` table row still exists (the signal fired); only the CSV row is omitted. Stage 5 can re-render skipped firings as "insufficient history" in the dashboard.

### D-CLUSTER-PERSISTENCE — Where does `detect_clusters` run?

The `cluster_id` column lives on `transactions`. Two options:

- **(a) Standalone CLI run independently before `eval_signals.py`.**
- **(b) Integrate into `eval_signals.py` as an idempotent pre-step.**

**Recommendation: (b).** `eval_signals.py` calls `detect_clusters.detect(conn, as_of)` first. Idempotent: rebuilds cluster_ids only for transactions visible as-of `as_of`. The S1 evaluator then reads `transactions.cluster_id`. `detect_clusters.py` ALSO exposes a CLI `python .scripts\detect_clusters.py --as-of YYYY-MM-DD` so it can be run standalone if Rupert wants to inspect cluster output without running the full signal pipeline. Either way, calling it twice is safe.

### D-T0-OVERLAP — Does T0 replace or coexist with T1/T2 firings?

T0 fires when a transaction is part of a cluster AND fires T1 or T2 in the cluster's 30-day window.

**Recommendation: coexist.** T1 and T2 still fire their own rows; T0 also fires its own row. Net effect: a single transaction can have rows for T0 + T1 + S1 + F1 simultaneously. Downstream dedup is trivial (`SELECT DISTINCT fingerprint FROM signals WHERE signal_id IN (...)`). Coexistence preserves the per-signal CAR profile, which is needed for Stage 4's "kill or keep" decision in Phase 3.2. Reasoning: if we collapse T1 into T0, we lose the apples-to-apples comparison "does T0 outperform T1 on a CAR basis?".

T0's `evaluate()` order matters: it must run AFTER T1/T2/S1 have been written, since it inspects the `signals` table to see what else has fired for this fingerprint. The orchestrator runs the 7 signals in this fixed order: `[t1, t2, t3, t4, s1, f1, t0]`. T0 is computed last because it depends on the others. Test case 13 below covers the ordering.

### D-WINDOW-EXIT-FALLBACK — Non-trading-day exits

If `announced_at + 252 calendar days` lands on a Saturday, Christmas, or any non-trading day where `prices` has no row, use the next trading day's close.

**Recommendation: confirm.** Implemented via "find the first `date >= entry_date + N` in `prices WHERE ticker=?`". Same rule for T+1, T+21, T+90, T+252. The "+N" is in **trading days**, not calendar days — i.e. the harness counts trading-day offsets in the `prices` table for the ticker. See pseudocode below.

For T+1 specifically: T+1 is the first trading day strictly after `announced_at`. If the filing was announced after-hours, T+1 is the next trading day (Yahoo's same-day close).

### D-NEW-COLUMNS — `_backtest_results.csv` columns

```
run_id, signal_id, signal_version, fingerprint, fired_at, announced_at, entry_date,
entry_close, t21_date, t21_close, t90_date, t90_close, t252_date, t252_close,
raw_return_t1, raw_return_t21, raw_return_t90, raw_return_t252,
benchmark_symbol, benchmark_return_t1, benchmark_return_t21, benchmark_return_t90, benchmark_return_t252,
car_t1, car_t21, car_t90, car_t252,
cost_bps, net_car_t1, net_car_t21, net_car_t90, net_car_t252,
windows_available, is_aim, ticker, role, value_gbp
```

**Recommendation: confirm above. 33 columns.** All compact. Easy to filter. The dashboard reads it directly.

### D-WALK-FORWARD-STRICT — Default `as_of`

- **`eval_signals.py` in production mode:** `as_of = today` (date the script runs) by default.
- **`eval_signals.py` in backtest mode:** when called with `--from / --to`, walks `as_of = announced_at` for each transaction in order. Each firing is evaluated as if the trader could see exactly what was public at the moment the filing went live.
- **`backtest.py`:** does NOT need `as_of` — it operates on the already-written `signals` table. But it must verify that the entry/exit prices it looks up have a `fetched_at <= run_started_at`. (In practice this is always true because `prices` is append-only.)

**Recommendation: confirm. Implement as described.**

### Q-SID — Should "Senior Independent Director" map to NED or stay T4?

Open. SID is structurally a non-executive but the role string doesn't contain "non-exec". For v1 the regex doesn't match SID -> they fall to T4. Two options: (a) leave at T4 conservatism; (b) extend the NED regex to include `\bsenior\s+independent\s+director\b`. **Recommendation: leave at T4 for v1.** Easy to flip in Phase 3.2 if cohort analysis shows SIDs cluster with NEDs.

### Q-T0-VERSION — When T0 incorporates outputs from T1/T2/S1, do its `signal_version` and version bumps need to chain?

If T1 bumps from `1.0.0` to `1.1.0`, does T0 need to bump too? **Recommendation: T0 records the versions of T1/T2/S1 it observed in its `metadata` JSON column.** T0 itself bumps independently. Avoids cascading version bumps.

### Q-CLUSTER-90D-ACTIVE — Spec 01 uses a 90-day "active" filter for the dashboard panel

Does S1 fire on every cluster row (regardless of activity) or only active clusters? **Recommendation: S1 fires on every cluster row.** The "active" filter is a Stage 5 dashboard concern, not a signal-firing concern. Historical (closed) clusters still have valid forward-return data and we want them in the backtest.

---

## Per-file structure (pseudocode level)

### `.scripts\signals\__init__.py`

```text
from . import (
    t1_ceo_cfo_buy_v1, t2_exec_buy_v1, t3_ned_buy_v1, t4_other_buy_v1,
    s1_cluster_buy_v1, f1_first_time_buy_v1, t0_cluster_opportunistic_v1,
)

REGISTRY = {
    "t1_ceo_cfo_buy": t1_ceo_cfo_buy_v1,
    "t2_exec_buy":    t2_exec_buy_v1,
    "t3_ned_buy":     t3_ned_buy_v1,
    "t4_other_buy":   t4_other_buy_v1,
    "s1_cluster_buy": s1_cluster_buy_v1,
    "f1_first_time_buy": f1_first_time_buy_v1,
    "t0_cluster_opportunistic": t0_cluster_opportunistic_v1,
}

EVAL_ORDER = ["t1_ceo_cfo_buy", "t2_exec_buy", "t3_ned_buy", "t4_other_buy",
              "s1_cluster_buy", "f1_first_time_buy", "t0_cluster_opportunistic"]

def iter_signals():  # yields (signal_id, module) in EVAL_ORDER
    for sid in EVAL_ORDER: yield sid, REGISTRY[sid]

def get_signal(signal_id):  # returns the module
    return REGISTRY[signal_id]
```

### Each `.scripts\signals\<signal>_v1.py`

```text
SIGNAL_ID = "t1_ceo_cfo_buy"   # matches REGISTRY key
SIGNAL_VERSION = "1.0.0"

def evaluate(tx: sqlite3.Row, conn: sqlite3.Connection, as_of: str) -> dict | None:
    """
    Walk-forward gate: the function MUST treat `as_of` as the upper bound.
    No SELECT may return a row with announced_at > as_of or date > as_of.

    Returns a dict ready for INSERT INTO signals (or None to skip):
        {
            "signal_id":      SIGNAL_ID,
            "signal_version": SIGNAL_VERSION,
            "fingerprint":    tx["fingerprint"],
            "fired_at":       db.iso_now(),
            "confidence":     "high" | "med" | "low" | None,
            "metadata":       json.dumps({...}),   # optional per-signal context
        }
    """
    # Per-signal logic. Examples:
    # T1:  if tx["type"] == "BUY" and classify_role(tx["role"]) == "CEO_CFO" and (tx["value"] or 0) >= 100_000: fire
    # T2:  if tx["type"] == "BUY" and classify_role(tx["role"]) == "EXEC"    and (tx["value"] or 0) >= 25_000:  fire (orchestrator handles "not already T1")
    # T3:  if tx["type"] == "BUY" and classify_role(tx["role"]) == "NED"     and (tx["value"] or 0) >= 10_000:  fire
    # T4:  if tx["type"] == "BUY" and (tx["value"] or 0) >= 1_000 and classify_role(tx["role"]) == "OTHER":     fire
    # S1:  if tx["cluster_id"] is not None and cluster has >=2 distinct directors visible as-of as_of: fire
    # F1:  if tx["type"] == "BUY" and no prior BUY row exists for (director, ticker) with announced_at < tx["announced_at"]: fire
    # T0:  if conn shows S1 fired for this fingerprint AND (T1 or T2) fired for this fingerprint: fire
```

T2/T3/T4 share a "not already in higher tier" rule. Two implementation options:
- (a) The evaluators stay tier-blind; the orchestrator handles tier dedup after collecting all firings.
- (b) Each evaluator queries the `signals` table to check if higher tiers already fired for this fingerprint in the current run.

**Pick (a).** Cleaner: each evaluator is a pure function. The orchestrator collects T1/T2/T3/T4 candidates and drops any T2 where T1 also fired (and similarly down the chain). This keeps each `evaluate()` independent and testable.

S1 reads `tx["cluster_id"]` and verifies the cluster is non-empty as-of `as_of`. The cluster_id column is populated by `detect_clusters.detect()`, called from the orchestrator pre-step.

F1's lookup query:
```sql
SELECT 1 FROM transactions
WHERE director = ? AND ticker = ? AND type = 'BUY'
  AND announced_at < ? AND announced_at <= ?     -- both bounds walk-forward
LIMIT 1
```
The first `announced_at < tx_announced_at` is the "prior buy" test. The second `<= as_of` is the walk-forward guard. Both must be present.

T0 sits at the end of EVAL_ORDER. Its `evaluate()`:
```sql
SELECT signal_id FROM signals
WHERE fingerprint = ? AND signal_id IN ('t1_ceo_cfo_buy','t2_exec_buy','s1_cluster_buy')
  AND fired_at <= ?   -- walk-forward guard
```
If the row count includes `s1_cluster_buy` AND (any of `t1_ceo_cfo_buy` OR `t2_exec_buy`), fire T0.

### `.scripts\signals\roles.py`

```text
import re

_CEO_CFO_RE = re.compile(r"\b(chief\s+executive|chief\s+financial|c\.?e\.?o\.?|c\.?f\.?o\.?)\b", re.I)
_NED_RE     = re.compile(r"\b(non[- ]?exec(?:utive)?(?:\s+director)?|\bned\b)\b", re.I)
_EXEC_RE    = re.compile(r"\b(chair(?:man|woman|person)?|chief\s+\w+\s+officer|executive\s+director|managing\s+director|group\s+(?:general\s+counsel|chief))\b", re.I)

def classify_role(role: str | None) -> str:
    if not role: return "OTHER"
    if _CEO_CFO_RE.search(role): return "CEO_CFO"
    if _NED_RE.search(role):     return "NED"           # NED check must come before EXEC because NEDs include "Director"
    if _EXEC_RE.search(role):    return "EXEC"
    return "OTHER"
```

### `.scripts\detect_clusters.py`

```text
def detect(conn, as_of: str) -> int:
    """
    Connected-component cluster detector. Spec 01.
    1. SELECT fingerprint, ticker, director, date, announced_at FROM transactions
       WHERE type='BUY' AND announced_at IS NOT NULL AND announced_at <= as_of
       ORDER BY ticker, date.
    2. For each ticker, run union-find:
       - Sort BUYs by date ascending.
       - For each pair, if |date_i - date_j| <= 30 calendar days AND director_i != director_j, union(i, j).
    3. A cluster is a component with >=2 distinct directors.
    4. Cluster_id format: "{ticker}-{first_buy_date}" (per spec 01).
    5. UPDATE transactions SET cluster_id = ? WHERE fingerprint = ?
       for every transaction in any cluster. Reset cluster_id = NULL for
       any transaction NOT in a cluster (in case it was previously
       part of one and got dropped — keeps idempotency clean).
    Returns: number of clusters found.
    """

def main():  # CLI
    # argparse: --as-of YYYY-MM-DD (default today), --verbose
    # opens conn via db.connect(), calls detect(conn, as_of), prints summary
```

Idempotent: re-running produces the same cluster_id column state for the same `as_of`. Walk-forward: `announced_at <= as_of` is enforced in step 1.

### `.scripts\eval_signals.py`

```text
def main():
    args = argparse.parse(--from, --to, --signal, --as-of, --rebuild, --verbose)
    as_of = args.as_of or today_iso()

    conn = db.connect()

    if args.rebuild:
        conn.execute("DELETE FROM signals WHERE signal_id = ?" if args.signal else "DELETE FROM signals")
        conn.commit()

    # Pre-step: refresh clusters as-of as_of (idempotent).
    detect_clusters.detect(conn, as_of)

    # Pull universe: every BUY (or every transaction — only buys fire signals
    # but we keep this generic) with announced_at <= as_of and ticker in
    # tickers_meta with non-NULL benchmark_symbol.
    rows = conn.execute("""
        SELECT t.*
        FROM transactions t
        JOIN tickers_meta tm ON tm.ticker = t.ticker
        WHERE t.announced_at IS NOT NULL
          AND t.announced_at <= ?
          AND (? IS NULL OR t.announced_at >= ?)
          AND (? IS NULL OR t.announced_at <= ?)
          AND tm.benchmark_symbol IS NOT NULL
        ORDER BY t.announced_at, t.fingerprint
    """, (as_of, args.from_, args.from_, args.to, args.to)).fetchall()

    signals_to_run = [args.signal] if args.signal else EVAL_ORDER

    # First pass: T1..T4, S1, F1 (independent evaluators).
    pre_t0 = [s for s in signals_to_run if s != "t0_cluster_opportunistic"]
    for tx in rows:
        firings = {}
        for sid in pre_t0:
            module = REGISTRY[sid]
            result = module.evaluate(tx, conn, as_of=tx["announced_at"])
            if result: firings[sid] = result

        # Tier dedup: keep only the highest-tier T-row.
        for higher, lower in [("t1_ceo_cfo_buy","t2_exec_buy"),
                              ("t1_ceo_cfo_buy","t3_ned_buy"),
                              ("t1_ceo_cfo_buy","t4_other_buy"),
                              ("t2_exec_buy","t3_ned_buy"),
                              ("t2_exec_buy","t4_other_buy"),
                              ("t3_ned_buy","t4_other_buy")]:
            if higher in firings and lower in firings:
                del firings[lower]

        for sid, result in firings.items():
            _upsert_signal(conn, result)

    conn.commit()  # T1..F1 visible before T0 runs

    # Second pass: T0 (depends on what's already in `signals`).
    if "t0_cluster_opportunistic" in signals_to_run:
        for tx in rows:
            result = REGISTRY["t0_cluster_opportunistic"].evaluate(tx, conn, as_of=tx["announced_at"])
            if result: _upsert_signal(conn, result)
        conn.commit()

    print summary: per-signal firing counts.

def _upsert_signal(conn, result):
    conn.execute("""
        INSERT INTO signals (signal_id, signal_version, fingerprint, fired_at, confidence, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(signal_id, signal_version, fingerprint) DO UPDATE
            SET fired_at = excluded.fired_at,
                confidence = excluded.confidence,
                metadata = excluded.metadata
    """, (result["signal_id"], result["signal_version"], result["fingerprint"],
          result["fired_at"], result.get("confidence"), result.get("metadata")))
```

Idempotency: `INSERT ... ON CONFLICT DO UPDATE` on the natural PK `(signal_id, signal_version, fingerprint)`. Re-running produces the same set of rows.

### `.scripts\backtest.py`

```text
def main():
    args = argparse.parse(--signal, --signal-version, --from, --to, --output, --run-id, --verbose)
    conn = db.connect()
    output_path = Path(args.output or DEFAULT_OUTPUT)  # .data\_backtest_results.csv

    run_id = args.run_id or f"bt_{db.iso_now().replace(':','').replace('-','')[:15]}"
    conn.execute("""
        INSERT INTO backtest_runs (run_id, started_at, signal_id, signal_version,
                                   metadata, universe, period_start, period_end)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (run_id, db.iso_now(), args.signal, args.signal_version, json.dumps({...}),
          "transactions ticker JOIN tickers_meta WHERE benchmark_symbol IS NOT NULL",
          args.from_, args.to))
    conn.commit()

    rows = conn.execute("""
        SELECT s.*, t.ticker, t.announced_at, t.role, t.value AS value_gbp,
               tm.benchmark_symbol, tm.is_aim
        FROM signals s
        JOIN transactions t ON t.fingerprint = s.fingerprint
        JOIN tickers_meta tm ON tm.ticker = t.ticker
        WHERE (? IS NULL OR s.signal_id = ?)
          AND (? IS NULL OR s.signal_version = ?)
          AND (? IS NULL OR t.announced_at >= ?)
          AND (? IS NULL OR t.announced_at <= ?)
          AND tm.benchmark_symbol IS NOT NULL
    """, (args.signal, args.signal, args.signal_version, args.signal_version,
          args.from_, args.from_, args.to, args.to)).fetchall()

    skips = []
    with output_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)   # see D-NEW-COLUMNS

        for r in rows:
            ticker = r["ticker"]
            announced_at = r["announced_at"]
            benchmark = r["benchmark_symbol"]
            is_aim = r["is_aim"]

            # Trading-day lookup helpers
            ticker_dates = _trading_dates(conn, ticker)         # cache per-ticker
            bench_dates  = _trading_dates(conn, benchmark)

            entry_idx = _first_index_at_or_after(ticker_dates, announced_at, offset=1)
            if entry_idx is None:
                skips.append({fingerprint, "no entry — no trading day after announced_at"}); continue
            entry_date = ticker_dates[entry_idx]
            entry_close = _close_on(conn, ticker, entry_date)

            # Insufficient-history check (D-INSUFFICIENT-HISTORY)
            n_priors = _count_trading_days_before(conn, ticker, announced_at)
            if n_priors < 30:
                skips.append({fingerprint, "insufficient history"}); continue

            t21_close = _close_on(conn, ticker, _date_at_offset(ticker_dates, entry_idx, 21))
            t90_close = _close_on(conn, ticker, _date_at_offset(ticker_dates, entry_idx, 90))
            t252_close = _close_on(conn, ticker, _date_at_offset(ticker_dates, entry_idx, 252))

            bench_entry = _close_on_or_before(conn, benchmark, entry_date)
            bench_t21   = _close_on(conn, benchmark, _date_at_offset(bench_dates, ..., 21))
            ... similar for t90, t252

            raw_t1   = (entry_close / _close_on(conn, ticker, announced_at_or_prior) - 1) if has_announce_close else None
            raw_t21  = (t21_close  / entry_close - 1) if t21_close else None
            raw_t90  = ...
            raw_t252 = ...

            bench_raw_t1 = analogously vs benchmark
            ...

            car_t1   = raw_t1 - bench_raw_t1 if both not None else None
            ...

            cost_bps = 50 if is_aim else 100
            net_car_t1 = car_t1 - cost_bps/10_000 if car_t1 is not None else None
            ...

            windows = ",".join(w for w, v in [("t1", raw_t1), ("t21", raw_t21),
                                              ("t90", raw_t90), ("t252", raw_t252)] if v is not None)

            w.writerow([run_id, r["signal_id"], r["signal_version"], r["fingerprint"],
                        r["fired_at"], announced_at, entry_date, entry_close,
                        ..., windows, is_aim, ticker, r["role"], r["value_gbp"]])

    if skips:
        (DATA_DIR / "_backtest_skips.json").write_text(json.dumps(skips, indent=2))

    conn.execute("UPDATE backtest_runs SET finished_at = ? WHERE run_id = ?",
                 (db.iso_now(), run_id))
    conn.commit()
    print summary: rows written, rows skipped, per-signal counts.
```

Helper notes:
- `_trading_dates(conn, ticker)` returns the sorted list of `date` strings for that ticker. Cached in-memory per ticker for the run.
- `_first_index_at_or_after(dates, target, offset)` does `bisect_right(dates, target) + (offset - 1)` so T+1 is the FIRST date strictly after `announced_at`.
- `_date_at_offset(dates, entry_idx, k)` is `dates[entry_idx + k]` if in range, else `None` -> that window unavailable.
- Trading-day offsets are measured in the ticker's own price series. For T+21/T+90/T+252 in the benchmark series, use the same trading-day offset in the benchmark's date list (close enough — LSE main session, very small mismatch).

### `.scripts\test_p3_lookahead.py`

See "Lookahead-bias test design" section below.

### `.scripts\test_stage_04.py`

>=20 cases; table in next section.

---

## Smoke-test cases (>=20)

| # | What it sets up | What it asserts |
|---|---|---|
| 1 | `classify_role("Group Chief Executive")` | Returns `"CEO_CFO"` |
| 2 | `classify_role("Chief Financial Officer")` | Returns `"CEO_CFO"` |
| 3 | `classify_role("CFO")` | Returns `"CEO_CFO"` (acronym path) |
| 4 | `classify_role("Non-Executive Director")` | Returns `"NED"` |
| 5 | `classify_role("Non Executive Director")` | Returns `"NED"` (space variant) |
| 6 | `classify_role("Chairman")` | Returns `"EXEC"` |
| 7 | `classify_role("Senior Independent Director")` | Returns `"OTHER"` (Q-SID conservative default; pinned for v1) |
| 8 | `classify_role("Executive Director")` | Returns `"EXEC"` |
| 9 | `classify_role("Chief Operating Officer")` | Returns `"EXEC"` (`chief \w+ officer` rule) |
| 10 | `classify_role(None)` | Returns `"OTHER"` |
| 11 | T1 fires on `(BUY, "Group CEO", value=150_000)` | Returns non-None |
| 12 | T1 does NOT fire on `(BUY, "Group CEO", value=80_000)` | Returns None (below £100k threshold) |
| 13 | T1 does NOT fire on `(SELL, "Group CEO", value=150_000)` | Returns None |
| 14 | T2 fires on `(BUY, "Chairman", value=30_000)` after T1 not firing | T1 None, T2 not None |
| 15 | T2 does NOT fire on `(BUY, "Group CEO", value=30_000)` after T1 fires | Orchestrator dedup: T2 dropped (T1 already in firings) |
| 16 | T3 fires on `(BUY, "Non-Executive Director", value=15_000)` | Returns non-None |
| 17 | T4 fires on `(BUY, role=NULL, value=5_000)` | Returns non-None (catch-all path) |
| 18 | F1 fires when no prior BUY for `(director, ticker)` | Returns non-None |
| 19 | F1 does NOT fire when a prior BUY exists at earlier `announced_at` | Returns None |
| 20 | F1 does NOT see a "later" BUY: synthetic with announced_at = D, a phantom BUY at D+5 | F1 fires as-of D (walk-forward — D+5 is invisible) |
| 21 | `detect_clusters.detect(conn, as_of)` on 3 BUYs at the same ticker by 3 directors, dates D, D+25, D+50 | Single cluster of 3, transitive (D and D+50 connected via D+25) |
| 22 | `detect_clusters.detect()` on 2 BUYs by the SAME director | Zero clusters (distinct-director requirement) |
| 23 | `detect_clusters.detect()` on 2 BUYs 35 days apart | Zero clusters (>30 day gap) |
| 24 | S1 fires when `tx.cluster_id IS NOT NULL` | Returns non-None |
| 25 | S1 does NOT fire when `tx.cluster_id IS NULL` | Returns None |
| 26 | T0 fires on a transaction that already has S1 + T1 in `signals` | Returns non-None |
| 27 | T0 does NOT fire on a transaction with only S1 | Returns None |
| 28 | T0 does NOT fire on a transaction with only T1 (no S1) | Returns None |
| 29 | Full orchestrator end-to-end on 5 synthetic BUYs | Correct row count per signal in `signals` table |
| 30 | Re-run orchestrator with same data | No new rows; `INSERT ... ON CONFLICT DO UPDATE` semantics |
| 31 | `backtest.py` on a 1-firing fixture with known prices | CAR matches hand-computed value to 4dp |
| 32 | Cost application: AIM ticker | `cost_bps = 50`, `net_car = car - 0.005` |
| 33 | Cost application: non-AIM ticker | `cost_bps = 100`, `net_car = car - 0.01` |
| 34 | T+21 falls on a non-trading day | Exit price is the next trading day's close |
| 35 | Ticker with <30 trading days of history | Skipped; entry in `_backtest_skips.json` |
| 36 | Benchmark series has fewer dates than the ticker | Benchmark return computed using closest <= entry_date |
| 37 | `_backtest_results.csv` row count = `signals` count − skipped | Counted assertion |

(>=20 required; 37 listed — pad room for refinement during build.)

---

## Lookahead-bias test design (the gate)

`test_p3_lookahead.py` is the single non-negotiable test of Stage 4. It runs against a synthetic SQLite store created in a tempdir; no live DB touched.

**Setup.**

1. Create a temp SQLite DB; apply `db_schema.sql`.
2. Seed `tickers_meta` with a single ticker `"XYZ"`, `benchmark_symbol = "^FTAS"`, `is_aim = 0`.
3. Seed `prices`:
   - `("XYZ", "2026-01-01" .. "2026-01-31", close = 100 + i)` — 31 close prices.
   - `("^FTAS", same range, close = 1000 + i)` — benchmark.
   - Also seed `("XYZ", "2026-02-05", close = 999)` — the **trap** future row.
4. Seed `transactions`:
   - A BUY by `"CEO Alice"` at ticker `XYZ`, `value = 200_000`, `announced_at = "2026-01-31"`.
   - A second BUY by the same director at the same ticker, `announced_at = "2026-02-05"` — the future row.
5. Run signal evaluation with `as_of = "2026-01-31"`.

**Assertions.**

A. The first BUY fires T1 (CEO, >£100k).
B. F1 fires for the first BUY (no prior BUY visible as-of 2026-01-31).
C. The second BUY does NOT appear in `signals` at all (its `announced_at > as_of`).
D. **The trap test:** insert into `prices` a row with `date = "2026-02-05"` and `close = 999`. Re-run `eval_signals.py` with `as_of = "2026-01-31"`. Inspect every `SELECT` issued (via `sqlite3.set_trace_callback` or a wrapper that captures every executed SQL string). Assert that no executed SQL produced a row with `date > "2026-01-31"`. The test wraps the conn's `execute()` and inspects every result row's date column where present.
E. Re-run `backtest.py` for the first firing. Assert that for the T+1 lookup, the entry date is the first trading day in `prices` strictly after 2026-01-31. The entry close is 999 ONLY if 2026-02-05 was a trading day and a valid future window. (In normal backtest mode, as `as_of` walks forward, future prices ARE available — backtest is about "looking back at history", not "predicting from the past". The lookahead test specifically catches the case where `eval_signals.py` peeks past its own `as_of`.)
F. The cluster detector: seed two distinct directors with BUYs at D and D+45. Detect with `as_of = D+10`. Assert that the cluster is NOT formed because the second director's BUY (D+45) is invisible. Then re-detect with `as_of = D+60`. Assert that the cluster IS now formed.

**Method of catching peeks.** Wrap the SQLite connection in a thin shim that intercepts every `execute()`, stores the SQL string, runs it, and after the run scans the row results for any date column where the value > `as_of`. If found: `assert False, f"signal evaluator peeked at {row_value} > as_of {as_of}: SQL: {sql}"`. Each signal module is tested individually.

**Failure semantics.** Any peek = exit code 1 from `test_p3_lookahead.py`. Stage 4 is not done until this test passes for every signal in `REGISTRY` and for `detect_clusters.detect()`.

This test is owned by the lookahead concern alone. The signal logic tests live in `test_stage_04.py`.

---

## Edge cases

- **Windows paths** via `pathlib.Path` everywhere. The CSV is written with `encoding="utf-8"` and `newline=""` to avoid CRLF doubling.
- **Tickers with NULL `benchmark_symbol`** (delisted or unsupported_currency) are dropped from the universe at the orchestrator's join. They appear in neither `signals` nor `_backtest_results.csv`.
- **Tickers with <30 trading days of pre-announce history** are evaluated by `eval_signals.py` (signals still fire) but skipped by `backtest.py` and recorded in `_backtest_skips.json`. (We don't want to lose the firing row, only the unreliable CAR computation.)
- **Missing T+252 window** (e.g. firing from 2026-04 has only T+1 and T+21 available as of 2026-05-14) -> backtest writes a CSV row with NULL in those columns; `windows_available = "t1,t21"`.
- **T+1 entry on the same day as `announced_at`** (filing announced before market open) -> entry_date is announced_at itself only if `announced_at` matches a trading day; otherwise next trading day. The plan uses "first trading day strictly after `announced_at`" -> simpler and conservative.
- **NULL `announced_at`** on a transaction (Stage 2 leaves this NULL for filings that couldn't be parsed cleanly) -> row is dropped from the universe; not evaluated.
- **Role variants.** "GROUP CEO" vs "Group CEO" — regex is case-insensitive. "Chief Executive Officer" matches `chief\s+executive`. "C.E.O." matches `c\.?e\.?o\.?`.
- **Cluster ties at the boundary.** Two BUYs exactly 30 days apart -> spec 01 says "within 30 days", inclusive. Confirm with a test (case 23 inverted): 30 days exactly = cluster forms.
- **Cluster `cluster_id` format.** `"{ticker}-{first_buy_date}"` per spec 01. The "first" buy is the earliest BUY by `date` (not `announced_at`) within the cluster, per spec 01.
- **Detect_clusters re-run.** Idempotent. If a prior run created `cluster_id = "XYZ-2025-12-18"` for a transaction, and the next run produces the same component, the cluster_id is unchanged. If the component split (e.g. a transaction was reclassified to non-BUY), the prior cluster_id is wiped to NULL.
- **The 3-tier dedup chain.** Done in-orchestrator AFTER all four T-evaluators have run for the same transaction. Avoids ordering bugs.
- **Re-running with `--rebuild --signal t1_ceo_cfo_buy`** deletes only `signal_id = 't1_ceo_cfo_buy'` rows, not the whole table.
- **`signal_version` bumps.** Old rows coexist with new rows. Stage 5's dashboard filters by latest version per signal_id.
- **Concurrent runs.** Not supported in v1. SQLite + Python single-thread is the assumption.
- **GBp price values.** Stage 3 already normalised `prices.close` to GBP. Stage 4 takes price values as-is.
- **`prices.close` for benchmark `^FTAS`.** Index level, not a stock price. Same lookup semantics. The CAR uses the percentage change in the index level vs the same percentage change for the ticker.
- **Costs labelling.** The plan applies 50bps spread one-shot (D-COSTS-MODEL); if Rupert wants 25bps + 25bps split, change is one line in `backtest.py`.

---

## Rollback

Stage 4 touches no existing file. To undo:

1. Delete the `.scripts/signals/` directory.
2. Delete `.scripts/detect_clusters.py`, `.scripts/eval_signals.py`, `.scripts/backtest.py`, `.scripts/test_p3_lookahead.py`, `.scripts/test_stage_04.py`.
3. Delete `.data/_backtest_results.csv` and `.data/_backtest_skips.json` if they exist.
4. SQL: `DELETE FROM signals; DELETE FROM backtest_runs; UPDATE transactions SET cluster_id = NULL, first_time_buy = 0;` — wipes Stage 4 side-effects on the existing schema.

Stages 1–3 files untouched.

---

## Acceptance criteria

Stage 4 is **done** when all of the following are true:

1. `python .scripts\eval_signals.py --rebuild` exits 0 and writes >=1 row to `signals` for each of the 7 signal_ids (assuming at least one of each is in the data — given 1,387 BUYs, T4 is guaranteed; T1/T2/T3/S1/F1/T0 expected but not guaranteed in such a small dataset).
2. `python .scripts\backtest.py` exits 0 and writes `.data\_backtest_results.csv` with a header row + N data rows where N == COUNT(*) FROM signals − skipped firings.
3. `python .scripts\test_p3_lookahead.py` exits 0 and prints `PASS` for every signal in `REGISTRY` plus `detect_clusters`.
4. `python .scripts\test_stage_04.py` exits 0 and prints `37 passed, 0 failed` (or whatever the final count is).
5. Re-running `eval_signals.py` produces zero net new rows in `signals` (only `fired_at` updated via UPSERT). Idempotency verified.
6. Re-running `backtest.py` produces a CSV that diffs to zero data-row delta with the prior run (atomic rewrite, same content).
7. No third-party package added; `requirements.txt` unchanged.
8. Schema unchanged. `meta.schema_version` remains `"2"` after Stage 4.
9. Stage 1, 2, 3 smoke tests still pass.
10. Only the 14 Stage-4 files in the table at the top of this plan exist (`.scripts/signals/*.py`, `.scripts/detect_clusters.py`, `.scripts/eval_signals.py`, `.scripts/backtest.py`, `.scripts/test_p3_lookahead.py`, `.scripts/test_stage_04.py`).

---

## Effort estimate

### Build effort (one focused session, ~6–8 hours, ~140k Sonnet tokens)

| Phase | Time | Tokens (Sonnet) |
|---|---|---|
| `.scripts/signals/roles.py` + tests | 30 min | ~6k |
| 7 signal modules (T0/T1/T2/T3/T4/S1/F1) | 90 min | ~25k |
| `.scripts/signals/__init__.py` registry | 15 min | ~3k |
| `.scripts/detect_clusters.py` (union-find) | 45 min | ~12k |
| `.scripts/eval_signals.py` orchestrator | 60 min | ~18k |
| `.scripts/backtest.py` event-study harness | 90 min | ~22k |
| `.scripts/test_p3_lookahead.py` (the gate) | 60 min | ~18k |
| `.scripts/test_stage_04.py` (~37 cases) | 75 min | ~25k |
| End-to-end run + debug on Rupert's data | 30 min | ~8k |
| Spot-check 5 CAR rows hand-tally vs CSV | 20 min | ~3k |
| Verify acceptance criteria 1–10 | 15 min | ~2k |
| **Build total** | **~7 hours** | **~140k** |

### Operational effort

| Phase | Time | Cost |
|---|---|---|
| First `eval_signals.py --rebuild` (2,383 txn) | <30s wall clock | $0 |
| First `backtest.py` (one row per signal firing — likely ~3–5k rows) | <60s wall clock | $0 |
| `test_p3_lookahead.py` | <5s | $0 |
| `test_stage_04.py` | <10s | $0 |
| **Operational total** | **~2 minutes wall clock** | **$0** |

---

## Out of scope for Stage 4

- **Cohort cuts** (sector, seniority, market cap, value bucket, regime). Phase 3.1 work; deferred to Stage 5+.
- **Sell signals.** Spec 05 defers sells to v2.
- **Dashboard / HTML rendering** of backtest results. Stage 5.
- **Paper-trade row creation.** Stage 5 (per `07-conviction-sizing.md`).
- **Conviction-weighted sizing.** Stage 5.
- **FX rates for non-GBP filings.** Those rows are NULL-currency in Stage 2's parser, filtered out of universe.
- **Per-director attribution** ("who's been in winning clusters"). Stage 5+.
- **Backtest Sharpe / hit-rate aggregates.** Computed in Stage 5's dashboard from the CSV (the CSV is the input layer).
- **`signals_returns` table.** v1 uses CSV (D-RESULTS-STORE). Promote to a table if a later stage needs SQL joinability.
- **Concurrent / async backtest.** Single-threaded by design.
- **Real-time / streaming evaluation.** `eval_signals.py` is a batch script run after every backfill / daily scrape.
- **Integration with `update.py`.** Wire-up belongs to Stage 5 if desired.
- **LLM-assisted signal definitions** (anomaly-detect signal). Out of scope.
- **Pre-2025-04-13 prices.** Stage 3 window limits earliest firings to that date for T+252 reachability.

---

## Open questions for Rupert

1. **D-RESULTS-STORE.** Confirm `.data\_backtest_results.csv` (recommended) vs a new `signals_returns` SQLite table. Recommendation is CSV.
2. **D-COSTS-MODEL.** Spread cost 50bps applied as a one-shot debit, or split 25bps entry + 25bps exit? Material to labelling only; recommendation is one-shot.
3. **Q-SID.** Senior Independent Director — keep at T4 (current recommendation) or extend the NED regex to include SID?
4. **Q-T0-VERSION.** Confirm T0's `signal_version` does not chain when T1/T2/S1 versions bump. T0 records observed sub-signal versions in `metadata`.
5. **Q-CLUSTER-90D-ACTIVE.** Confirm S1 fires on every cluster row (active or historical), with the 90-day "active" filter being a Stage 5 concern only.
6. **D-INSUFFICIENT-HISTORY.** Confirm <30 trading days of pre-announce history -> skip in backtest, log to `_backtest_skips.json`, but keep the signals-table row.
7. **Confidence column.** The `signals.confidence` column is free text. Should v1 populate it (e.g. T1 = "high", T4 = "low") or leave NULL? Recommendation: populate per-signal, see signal modules.
8. **`signals.metadata`.** Recommended payload (JSON string): `{"value_gbp": 150000, "role": "Group CEO", "role_class": "CEO_CFO", "ticker": "XYZ"}`. Confirm or trim.
9. **T+1 fallback.** If the ticker has NO trading day after `announced_at` in `prices` (e.g. announced 2026-05-13, prices end on 2026-05-13), skip the firing in `backtest.py`. Confirm.
10. **`run_id` format.** Recommendation: `bt_{utc_compact_iso}` e.g. `bt_20260514T120300Z`. Confirm or override.
11. **Tier dedup.** Confirm tier dedup is done by the orchestrator, not inside the evaluators. Keeps evaluators pure.
12. **Stamp-duty rate.** Spec says 0.5%. UK stamp duty on share purchases is 0.5% of consideration, so 50bps. Confirm. (Stamp duty reserve tax / SDRT is a separate ~0.5% on electronic settlements but effectively the same charge for retail buys.)
