"""Stage 4 - signal evaluation orchestrator.

Walks every BUY transaction in `transactions` in `announced_at` order
and evaluates each signal in `signals.SIGNALS`. Writes results into
the `signals` table via INSERT OR IGNORE.

Locked Stage 4 decisions implemented:

  * Auto-invoke `backfill_prices` (D-INSUFFICIENT-HISTORY): for every
    ticker in the universe with fewer than 30 trading days in `prices`
    OR missing entirely, we call backfill_prices.run for just that
    ticker before the main loop. Skips that still fail are recorded
    in `.data/_backtest_skips.json`. This step is fire-and-forget;
    if it can't reach Yahoo (offline tests, etc.) the orchestrator
    keeps going. We never let auto-invoke crash the main loop.

  * detect_clusters.detect(conn, as_of) is run BEFORE the main loop so
    that S1 evaluators see populated `transactions.cluster_id` columns.

  * Tier dedup (T1 > T2 > T3 > T4) happens here, not in the evaluators.

  * Two-pass evaluation: first pass writes T1/T2/T3/T4/S1/F1; orchestrator
    commits; second pass evaluates T0 against the now-populated
    `signals` table.

  * Idempotency: INSERT OR REPLACE on the natural PK (signal_id,
    signal_version, fingerprint). Re-running refreshes existing rows
    in place and produces zero net new rows after the first run.

Stage 5 addition: read `.data/signal_status.json` at the start of every
evaluate_all() pass and drop any deprecated signal_ids from the eval
order. Existing fired rows are preserved -- only new evaluations are
suppressed. The file is written by the dashboard's deprecate button
(POST /api/deprecate).

CLI::

    python eval_signals.py [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                           [--signal SIGNAL_ID] [--rebuild]
                           [--no-backfill] [--verbose]

  --rebuild           DELETE FROM signals first (or DELETE WHERE signal_id=?)
  --no-backfill       Skip the auto-invoke price-backfill pre-step.
                      Useful in tests / offline runs.
  --signal SIGNAL_ID  Only evaluate that one signal.
  --from / --to       Filter transactions by announced_at range.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db  # noqa: E402
import db_health  # noqa: E402
import detect_clusters  # noqa: E402
import signals as signals_pkg  # noqa: E402
import sizing  # noqa: E402  (B-115 spec 07 conviction position sizing)
from sizing import position_size  # noqa: E402
from role_normalize import is_corporate_actor, is_related_party  # noqa: E402 (B-136)


SKIPS_PATH = db.DB_DIR / "_backtest_skips.json"
SIGNAL_STATUS_PATH = db.DB_DIR / "signal_status.json"
MIN_HISTORY_DAYS = 30

# B-100 Phase B: signals eligible for paper-trade tracking.
# Excludes b2_crowded_cluster_kill (kill/short signal) and
# t0_cluster_combo (composite — not a standalone directional buy).
_PAPER_BUY_SIGNALS = frozenset({
    "t1a_ceo_founder_buy", "t1b_cfo_buy", "t2_exec_buy",
    "t3_ned_buy", "t4_other_buy", "t5_pca_buy",
    "t6_company_sec_buy", "t7_chair_buy",
    "s1_cluster_buy", "f1_first_time_buy", "b1_lone_conviction_buy",
})
_PAPER_MAX_NOTIONAL = sizing.CAP_GBP        # £5,000 (spec 07 D2; was 50,000)
_PAPER_SIZING_DEFAULT = sizing.DEFAULT_SIZING  # "log" (spec 07 D1)


def _open_paper_trade(conn, result: dict, tx, max_notional: float = _PAPER_MAX_NOTIONAL,
                      sizing: str = _PAPER_SIZING_DEFAULT, verbose: bool = False) -> None:
    """B-100 Phase B: open a paper_trade row when a buy signal fires.

    Idempotent via INSERT OR IGNORE on a stable trade_id built from
    (signal_id, fingerprint). Entry price is the first close in `prices`
    on or after the fired_at date for the ticker. Status is 'open' when
    the entry price is found, 'planned' otherwise (price will be patched
    by a later close_paper_trades.py run).
    """
    # sqlite3.Row does not support .get() — normalise to dict once.
    if not isinstance(tx, dict):
        tx = dict(tx)
    sid = result.get("signal_id") or ""
    if sid not in _PAPER_BUY_SIGNALS:
        return
    fingerprint = result.get("fingerprint") or tx.get("fingerprint") or ""
    if not fingerprint:
        return
    ticker = tx.get("ticker") or ""
    fired_at = (result.get("fired_at") or "")[:10]
    if not fired_at or not ticker:
        return

    # Stable, deterministic trade_id (no randomness; safe for --rebuild).
    trade_id = f"pt_{sid}_{fingerprint}"
    # B-115 / spec 07: conviction-weighted sizing from the director's £ value.
    # `position_size` clamps to [floor, cap]; missing value sizes at the floor.
    notional = position_size(float(tx.get("value") or 0),
                             sizing=sizing, cap=max_notional)

    # Look up T+1 close: first available close on or after fired_at.
    entry_row = conn.execute(
        "SELECT date, close FROM prices WHERE ticker = ? AND date >= ? "
        "ORDER BY date ASC LIMIT 1",
        (ticker, fired_at),
    ).fetchone()
    if entry_row:
        entry_date = entry_row["date"]
        entry_close = float(entry_row["close"])
        shares = notional / entry_close if entry_close > 0 else None
        status = "open"
    else:
        entry_date = None
        entry_close = None
        shares = None
        status = "planned"

    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    # B-179: INSERT OR IGNORE (SQLite) <-> ON CONFLICT DO NOTHING (Postgres),
    # both keyed on the PK trade_id. DO NOTHING == IGNORE (no update on conflict).
    pt_insert = (
        "INSERT INTO paper_trades "
        if db.backend() == "postgres" else
        "INSERT OR IGNORE INTO paper_trades "
    )
    pt_tail = (
        " ON CONFLICT (trade_id) DO NOTHING"
        if db.backend() == "postgres" else ""
    )
    try:
        conn.execute(
            pt_insert +
            "(trade_id, signal_id, signal_version, fingerprint, sizing_scheme, "
            " notional_gbp, entry_date, entry_close, shares, status, "
            " opened_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)" + pt_tail,
            (trade_id, sid, result.get("signal_version", "1.0.0"), fingerprint,
             sizing, round(notional, 2),
             entry_date, entry_close, shares, status, now_iso, now_iso),
        )
        if verbose:
            print(f"  paper_trade {status}: {trade_id} @ {entry_close} ({ticker})")
    except Exception as exc:  # noqa: BLE001
        if verbose:
            print(f"  paper_trade skip ({trade_id}): {exc}")

# Short -> long signal_id map. Stage 5 dashboard writes short ids
# (t0, t1a, t1b, t2, t3, t4, t5, t6, t7, s1, f1) into signal_status.json;
# evaluator dispatch uses long.
#
# B-025 Phase B: "t1" no longer exists. Use "t1a" or "t1b".
_SHORT_TO_LONG = {
    "t0":  "t0_cluster_combo",
    "t1a": "t1a_ceo_founder_buy",
    "t1b": "t1b_cfo_buy",
    "t2":  "t2_exec_buy",
    "t3":  "t3_ned_buy",
    "t4":  "t4_other_buy",
    "t5":  "t5_pca_buy",
    "t6":  "t6_company_sec_buy",
    "t7":  "t7_chair_buy",
    "s1":  "s1_cluster_buy",
    "f1":  "f1_first_time_buy",
    "b1":  "b1_lone_conviction_buy",
    "b2":  "b2_crowded_cluster_kill",
}


def _load_deprecated_signal_ids() -> set[str]:
    """Read .data/signal_status.json and return the set of deprecated
    signal_ids (long form). Accepts both short and long ids in the file.
    Stage 5 contract.
    """
    if not SIGNAL_STATUS_PATH.exists():
        return set()
    try:
        payload = json.loads(SIGNAL_STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return set()
    out: set[str] = set()
    for sid in payload.get("deprecated") or []:
        sid = (sid or "").strip().lower()
        if not sid:
            continue
        out.add(sid)
        if sid in _SHORT_TO_LONG:
            out.add(_SHORT_TO_LONG[sid])
    return out


def _identify_thin_tickers(conn) -> list[str]:
    """Return tickers with < MIN_HISTORY_DAYS rows in `prices`.

    Includes tickers entirely missing from `prices`. We filter to
    tickers that actually appear in `transactions` so we don't try
    to backfill phantom symbols. Excludes benchmark series (^...).
    """
    # B-011 / Sprint 10 Phase 1: exclude IT/CEF/VCT/REIT tickers so we
    # don't trigger auto-backfill Yahoo calls for issuers we'll never
    # use in signals. LEFT JOIN with COALESCE keeps tickers missing
    # from tickers_meta in the candidate set.
    rows = conn.execute(
        "SELECT DISTINCT t.ticker AS ticker, COUNT(p.date) AS n "
        "FROM transactions t "
        "LEFT JOIN prices p ON p.ticker = t.ticker "
        "LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker "
        "WHERE t.ticker IS NOT NULL "
        "  AND t.ticker NOT LIKE '^%' "
        "  AND COALESCE(tm.is_excluded_issuer, 0) != 1 "
        "GROUP BY t.ticker "
        # Postgres (unlike SQLite) does not allow a SELECT alias in HAVING —
        # repeat the aggregate. COUNT(p.date) works on both backends. (B-180)
        "HAVING COUNT(p.date) < ?",
        (MIN_HISTORY_DAYS,),
    ).fetchall()
    return [r["ticker"] for r in rows]


def _try_auto_backfill(thin_tickers: list[str], verbose: bool) -> list[dict]:
    """Call backfill_prices.run for each thin ticker. Returns skip records.

    Best-effort: any exception is logged into the skips list and the
    main loop continues. Network outages, delisted tickers, and
    unsupported currencies all fall through here.
    """
    if not thin_tickers:
        return []
    try:
        import backfill_prices  # noqa: E402, local import to keep startup cheap
    except Exception as exc:  # noqa: BLE001
        return [{"ticker": t, "reason": f"backfill_prices unavailable: {exc}"}
                for t in thin_tickers]

    today_iso = date.today().isoformat()
    from_iso = (date.today().replace(year=date.today().year - 1)
                .isoformat())
    skips: list[dict] = []
    for tkr in thin_tickers:
        try:
            if verbose:
                print(f"  auto-backfill: {tkr}")
            result = backfill_prices.run(
                date_from=from_iso, date_to=today_iso,
                only_ticker=tkr, dry_run=False, rate_limit=0.0,
                resume=False, verbose=False,
            )
            if result.get("ok", 0) == 0 and result.get("rows_inserted", 0) == 0:
                skips.append({"ticker": tkr,
                              "reason": "auto-backfill returned no rows"})
        except Exception as exc:  # noqa: BLE001
            skips.append({"ticker": tkr, "reason": f"{type(exc).__name__}: {exc}"})
    return skips


def _universe_rows(conn, date_from, date_to):
    """Return the universe of transactions to evaluate, ordered by announced_at.

    B-011 / Sprint 10 Phase 1: excludes rows whose ticker is flagged
    `is_excluded_issuer = 1` in `tickers_meta` (Investment Trusts,
    CEFs, VCTs, REITs). The filter uses `COALESCE` so tickers absent
    from `tickers_meta` are treated as not-excluded. The JOIN to
    tickers_meta is already present for the `benchmark_symbol`
    check, so this adds zero query cost.
    """
    rows = conn.execute(
        "SELECT t.* "
        "FROM transactions t "
        "JOIN tickers_meta tm ON tm.ticker = t.ticker "
        "WHERE t.announced_at IS NOT NULL "
        "  AND tm.benchmark_symbol IS NOT NULL "
        "  AND COALESCE(tm.is_excluded_issuer, 0) != 1 "
        "  AND (t.type != 'BUY' "
        "       OR COALESCE(t.buy_strictness, 'STRICT_BUY') = 'STRICT_BUY') "
        "  AND COALESCE(t.price_audit, 'ok') NOT IN ('unresolved', 'no_market') "
        "  AND (CAST(? AS TEXT) IS NULL OR t.announced_at >= ?) "
        "  AND (CAST(? AS TEXT) IS NULL OR t.announced_at <= ?) "
        "ORDER BY t.announced_at ASC, t.fingerprint ASC",
        (date_from, date_from, date_to, date_to),
    ).fetchall()
    # B-136: exclude ARMS-LENGTH corporate holders (funds, asset managers,
    # investment companies) from the scoring universe. Corporate PCAs and
    # family trusts are KEPT — reportable related-party dealings (Rupert
    # 2026-06-06). Actor-level resolution: if an actor is tagged a related
    # party on ANY row, keep ALL their rows (the source role text is
    # inconsistent across an actor's filings). Rows remain STORED; this only
    # removes them from signal evaluation (and, via firings, from CAR/cohorts).
    related = {r["director"] for r in rows
               if is_related_party(r["role_normalized"], r["role"], r["director"])}
    return [r for r in rows
            if not (is_corporate_actor(r["director"])
                    and r["director"] not in related)]


def _upsert(conn, result: dict) -> None:
    # B-179: INSERT OR REPLACE (SQLite) <-> ON CONFLICT DO UPDATE (Postgres).
    # Full-row insert on PK (signal_id, signal_version, fingerprint); the
    # DO UPDATE refreshes the only non-PK columns (fired_at/confidence/metadata)
    # so behaviour matches the SQLite delete+reinsert exactly.
    if db.backend() == "postgres":
        sql = (
            "INSERT INTO signals "
            "(signal_id, signal_version, fingerprint, fired_at, confidence, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (signal_id, signal_version, fingerprint) DO UPDATE SET "
            "fired_at = excluded.fired_at, confidence = excluded.confidence, "
            "metadata = excluded.metadata"
        )
    else:
        sql = (
            "INSERT OR REPLACE INTO signals "
            "(signal_id, signal_version, fingerprint, fired_at, confidence, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
    conn.execute(
        sql,
        (result["signal_id"], result["signal_version"], result["fingerprint"],
         result["fired_at"], result.get("confidence"), result.get("metadata")),
    )


def _apply_tier_dedup(firings: dict) -> dict:
    """Strip lower-tier T-firings when a higher-tier T fires on same tx.

    Operates only on T1/T2/T3/T4. S1/F1/T0 are independent.
    """
    rank = signals_pkg.TIER_RANK
    t_present = [sid for sid in firings if sid in rank]
    if len(t_present) <= 1:
        return firings
    best = min(t_present, key=lambda s: rank[s])
    out = {sid: r for sid, r in firings.items()
           if sid not in rank or sid == best}
    return out


def evaluate_all(conn, date_from=None, date_to=None,
                 only_signal: str | None = None,
                 verbose: bool = False,
                 skip_cluster_detect: bool = False,
                 sizing: str = _PAPER_SIZING_DEFAULT,
                 max_notional: float = _PAPER_MAX_NOTIONAL) -> dict:
    """Run the full evaluator pipeline. Returns a per-signal summary dict.

    Pre-step: unless `skip_cluster_detect=True`, runs
    detect_clusters.detect(conn, today) to populate `transactions.cluster_id`
    so S1 evaluators see a consistent baseline. The S1 evaluator then
    re-validates the distinct-director count per-tx as-of `tx.announced_at`,
    which is the actual walk-forward gate.
    """
    if not skip_cluster_detect:
        detect_clusters.detect(conn, date.today().isoformat(), verbose=verbose)

    rows = _universe_rows(conn, date_from, date_to)
    if verbose:
        print(f"universe: {len(rows)} transactions")

    eval_order = signals_pkg.EVAL_ORDER
    if only_signal:
        if only_signal not in eval_order:
            raise SystemExit(f"unknown --signal: {only_signal}")
        eval_order = [only_signal]

    # Stage 5: read .data/signal_status.json and drop any deprecated signals
    # from the eval order. Existing fired rows are preserved.
    deprecated = _load_deprecated_signal_ids()
    if deprecated:
        before = list(eval_order)
        eval_order = [sid for sid in eval_order if sid not in deprecated]
        if verbose and len(eval_order) != len(before):
            skipped = [sid for sid in before if sid in deprecated]
            print(f"skipping deprecated signals: {skipped}")

    counts = {sid: 0 for sid in signals_pkg.EVAL_ORDER}

    # --- First pass: everything except T0. ---
    pre_t0 = [s for s in eval_order if s not in signals_pkg.DEPENDENT_SIGNALS]

    # B2 in-memory kill window: {ticker -> "YYYY-MM-DD"}.
    # When B2 fires on a ticker, all non-B2 signals are suppressed on that
    # ticker until this date (walk-forward safe — rows are sorted by
    # announced_at ASC so the dict is built in chronological order).
    _b2_kill_until: dict = {}
    _B2_SID = "b2_crowded_cluster_kill"
    _B2_SUPPRESSION_DAYS = 60

    for tx in rows:
        as_of = tx["announced_at"]
        ticker = tx["ticker"]

        # Is this ticker currently under a B2 suppression window?
        under_kill = bool(
            ticker
            and as_of
            and ticker in _b2_kill_until
            and as_of <= _b2_kill_until[ticker]
        )

        firings: dict = {}
        for sid in pre_t0:
            # Non-B2 signals are skipped during a B2 kill window.
            if under_kill and sid != _B2_SID:
                continue
            mod = signals_pkg.REGISTRY[sid]
            r = mod.evaluate(tx, conn, as_of=as_of)
            if r is not None:
                firings[sid] = r

        firings = _apply_tier_dedup(firings)
        for sid, r in firings.items():
            # Use the transaction's own date as the signal timestamp so that
            # backfilled historical signals appear at the correct point in time
            # on the performance chart. Fall back to now() only if both fields
            # are absent (should not happen in practice).
            r["fired_at"] = tx["announced_at"] or tx["date"] or db.iso_now()
            _upsert(conn, r)
            counts[sid] += 1
            # B-100 Phase B: open a paper_trade row for each buy signal fire.
            _open_paper_trade(conn, r, tx, max_notional=max_notional,
                              sizing=sizing, verbose=verbose)

            # When B2 fires, extend the kill window for this ticker.
            if sid == _B2_SID and as_of and ticker:
                try:
                    kill_end = (
                        datetime.strptime(as_of[:10], "%Y-%m-%d")
                        + timedelta(days=_B2_SUPPRESSION_DAYS)
                    ).strftime("%Y-%m-%d")
                    if ticker not in _b2_kill_until or kill_end > _b2_kill_until[ticker]:
                        _b2_kill_until[ticker] = kill_end
                except ValueError:
                    pass

    conn.commit()

    # --- Second pass: T0 (depends on `signals` rows from pass 1). ---
    for sid in signals_pkg.DEPENDENT_SIGNALS:
        if sid not in eval_order:
            continue
        mod = signals_pkg.REGISTRY[sid]
        for tx in rows:
            as_of = tx["announced_at"]
            r = mod.evaluate(tx, conn, as_of=as_of)
            if r is not None:
                r["fired_at"] = tx["announced_at"] or tx["date"] or db.iso_now()
                _upsert(conn, r)
                counts[sid] += 1
    conn.commit()

    # Distinct-ticker and distinct-director rollups for the summary line.
    distinct_tickers = conn.execute(
        "SELECT COUNT(DISTINCT t.ticker) AS n "
        "FROM signals s JOIN transactions t ON t.fingerprint = s.fingerprint"
    ).fetchone()["n"]
    distinct_directors = conn.execute(
        "SELECT COUNT(DISTINCT t.director) AS n "
        "FROM signals s JOIN transactions t ON t.fingerprint = s.fingerprint"
    ).fetchone()["n"]

    return {
        "by_signal": counts,
        "distinct_tickers": distinct_tickers,
        "distinct_directors": distinct_directors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Stage 4 signals.")
    parser.add_argument("--from", dest="date_from", default=None)
    parser.add_argument("--to", dest="date_to", default=None)
    parser.add_argument("--signal", dest="only_signal", default=None)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--no-backfill", action="store_true",
                        help="Skip auto-invoke price backfill pre-step.")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--sizing", choices=list(sizing.SCHEMES),
                        default=_PAPER_SIZING_DEFAULT,
                        help="Paper-trade position sizing scheme (spec 07 D1).")
    parser.add_argument("--max-notional", dest="max_notional", type=float,
                        default=_PAPER_MAX_NOTIONAL,
                        help="Per-position GBP cap (spec 07 D2).")
    args = parser.parse_args(argv)

    # Code-review fix C-1 (2026-05-20): take a fresh .bak BEFORE touching
    # the signals table. --rebuild does `DELETE FROM signals` which is
    # destructive; a FUSE blip or crash mid-rebuild would leave the table
    # half-empty. Pre-snapshot defends against this. Also verifies the DB
    # is healthy before we open it for writing.
    # B-179: the integrity_check + .bak dance is a SQLite/FUSE corruption
    # defence. On Postgres there is no local .db file (db_health would always
    # report False) and the managed server is the source of truth, so skip it.
    if db.backend() == "sqlite":
        if not db_health.check(db.DB_PATH):
            print("[eval_signals] FATAL: pre-run PRAGMA integrity_check failed. "
                  "Run start.bat to restore from .bak before retrying.")
            return 2
        if args.rebuild:
            if not db_health.backup():
                print("[eval_signals] FATAL: failed to take pre-rebuild .bak. "
                      "Refusing to proceed (destructive write).")
                return 3

    # B-033: open the connection INSIDE the try so a connect() failure
    # (corrupt schema, disk full) doesn't leak a half-initialised handle.
    # Pre-init to None so the finally is safe even on connect() raise.
    conn = None
    try:
        conn = db.connect()
        if args.rebuild:
            if args.only_signal:
                conn.execute("DELETE FROM signals WHERE signal_id = ?",
                             (args.only_signal,))
            else:
                conn.execute("DELETE FROM signals")
            conn.commit()

        # Auto-invoke backfill_prices for thin tickers (D-INSUFFICIENT-HISTORY).
        if not args.no_backfill:
            thin = _identify_thin_tickers(conn)
            if args.verbose:
                print(f"thin tickers: {len(thin)}")
            skips = _try_auto_backfill(thin, args.verbose)
            if skips:
                SKIPS_PATH.parent.mkdir(parents=True, exist_ok=True)
                existing = []
                if SKIPS_PATH.exists():
                    try:
                        existing = json.loads(SKIPS_PATH.read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        existing = []
                merged = (existing or []) + skips
                # B-030 + B-038 (2026-05-21): atomic write via the
                # canonical db.atomic_write_json helper. A mid-write
                # crash used to leave SKIPS_PATH truncated; the next
                # pipeline run couldn't parse it.
                db.atomic_write_json(SKIPS_PATH, merged)

        # Refresh cluster_ids visible as-of today.
        detect_clusters.detect(conn, date.today().isoformat(),
                               verbose=args.verbose)

        summary = evaluate_all(conn, date_from=args.date_from,
                               date_to=args.date_to,
                               only_signal=args.only_signal,
                               verbose=args.verbose,
                               sizing=args.sizing,
                               max_notional=args.max_notional)
        print("signals fired by type:")
        for sid, n in summary["by_signal"].items():
            print(f"  {sid:25s} {n}")
        print(f"distinct_tickers={summary['distinct_tickers']}  "
              f"distinct_directors={summary['distinct_directors']}")
    finally:
        if conn is not None:
            conn.close()

    # Code-review fix C-1 (2026-05-20): seal a fresh .bak after the
    # signals table has been (re)written. Belt-and-braces: pairs with
    # the pre-rebuild snapshot above and keeps the .bak fresh against
    # the latest signal eval state.
    if not db_health.check(db.DB_PATH):
        print("[eval_signals] WARNING: post-run integrity_check failed. "
              "The pre-run .bak is still valid — restore via start.bat.")
        return 4
    db_health.seal()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
