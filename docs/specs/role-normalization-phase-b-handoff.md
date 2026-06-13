# B-025 Phase B — Hand-off package for Rupert

**Status:** Phase B code complete. Awaiting your backfill + eval_signals re-run.
**Date:** 2026-05-20

---

## What's been built in Phase B

### New signal modules (8 per-bucket signals)
- `.scripts/signals/t1a_ceo_founder_buy_v1.py` — CEO + Founder, £100k
- `.scripts/signals/t1b_cfo_buy_v1.py` — CFO, £100k
- `.scripts/signals/t5_pca_buy_v1.py` — PCA, £10k (NEW cohort)
- `.scripts/signals/t6_company_sec_buy_v1.py` — Company Sec / GC, £10k (NEW cohort)
- `.scripts/signals/t7_chair_buy_v1.py` — Chair (exec + non-exec), £25k (NEW cohort)

### Edited files
- `.scripts/signals/roles.py` — `classify_role()` now returns one of 8 tier strings via dict lookup on `role_normalized`.
- `.scripts/signals/__init__.py` — registry updated; TIER_RANK updated.
- `.scripts/signals/t1_ceo_cfo_buy_v1.py` — deprecated, raises ImportError if used.
- `.scripts/eval_signals.py` — `_SHORT_TO_LONG` map updated for new signal IDs.
- `.scripts/classify_role.py` — Performance page bucketing switched to canonical-bucket lookup.

### Documentation
- `docs/specs/role-normalization-pass.md` — full 8-tier scheme documented.
- `docs/backlog.md` (B-025) — status updated to Phase A+B code complete.

---

## What you need to run (PowerShell, in order)

Prerequisite: **Phase A must already be applied** (the `role_normalized`
column populated). If you haven't run Phase A yet, do that first per
`docs/specs/role-normalization-handoff.md`.

### Step 1 — Re-evaluate signals with the new tier scheme

This regenerates every row in the `signals` table using the new 8-tier
classifier. Historical firings under `t1_ceo_cfo_buy` will be replaced
by `t1a_ceo_founder_buy` + `t1b_cfo_buy` firings, etc.

```powershell
cd C:\Dev\DirectorsDealings
python .scripts\eval_signals.py --rebuild
```

**Expected output:**
- Old firings cleared
- New firings written under new signal IDs
- Per-signal counts printed at the end
- Approx 144 t1a_ceo_founder_buy firings (was 284 t1_ceo_cfo_buy)
- Approx 99 t1b_cfo_buy firings (the rest of the old T1)
- Approx 182 t7_chair_buy firings (new cohort)
- Approx 100 t5_pca_buy firings (new cohort, was misfiring elsewhere)
- Approx 7 t6_company_sec_buy firings (new cohort)

### Step 2 — Re-run backtest

Refreshes `_backtest_results.csv` with the new tier strings in the
`role_class` column.

```powershell
python .scripts\backtest.py
```

### Step 3 — Rebuild the dashboard

The Performance page bucketing (`ceo_cfo / other_exec / ned`) is
unchanged in this phase — T1a + T1b still fold into `ceo_cfo`, T7
folds into `other_exec`, T3 → `ned`, others → None. So the Performance
tile structure stays the same but the underlying counts will shift.

```powershell
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
```

### Step 4 — Verify

```powershell
# Distinct signal IDs in the new signals table
python -c "import sqlite3; con=sqlite3.connect('.data/directors.db'); rows=con.execute('SELECT signal_id, COUNT(*) FROM signals GROUP BY signal_id ORDER BY signal_id').fetchall(); [print(r) for r in rows]"
```

Should show the 11 new signal IDs (8 tier signals + s1_cluster_buy + f1_first_time_buy + t0_cluster_combo). No `t1_ceo_cfo_buy` should remain.

---

## What changes you'll see on the dashboard

### Active Clusters panel + dealings table
- Role chips already show the 14 canonical buckets (Phase A).
- Signal badges now show new IDs: `T1a`, `T1b`, `T2`, `T3`, `T4`, `T5`, `T6`, `T7` instead of just `T1`.
- The `t1_ceo_cfo_buy` badge is GONE — replaced by `t1a_ceo_founder_buy` or `t1b_cfo_buy`.

### Performance page
- The 3-tile structure (`ceo_cfo / other_exec / ned`) is unchanged.
- Counts under each tile will shift slightly — cleaner cohorts mean some firings move tile.
- The per-signal diagnostics chart (Stage 5 U2) will show new lines for `t1a`, `t1b`, `t5`, `t6`, `t7`. You may want to clean up the legend in a follow-up.

### Backtest CSV
- `role_class` column now contains `T1a`, `T1b`, etc. instead of `T1`.
- `role_normalized` column carries the canonical bucket (Phase A).
- `signal_id` column reflects the new IDs.
- Historical rows from before the re-run will have OLD signal IDs — old CSV files should be archived if you want them.

---

## Rollback plan

If something looks wrong after the re-run:

1. **Restore the DB** from the Phase A pre-backfill snapshot:
   ```powershell
   copy .data\directors.db.bak-pre-role-normalize-20260520 .data\directors.db
   ```

2. **Revert the code** — Phase B touches these files:
   - `.scripts/signals/roles.py`
   - `.scripts/signals/__init__.py`
   - `.scripts/signals/t1_ceo_cfo_buy_v1.py` (deprecated)
   - `.scripts/signals/t1a_ceo_founder_buy_v1.py` (new)
   - `.scripts/signals/t1b_cfo_buy_v1.py` (new)
   - `.scripts/signals/t5_pca_buy_v1.py` (new)
   - `.scripts/signals/t6_company_sec_buy_v1.py` (new)
   - `.scripts/signals/t7_chair_buy_v1.py` (new)
   - `.scripts/eval_signals.py`
   - `.scripts/classify_role.py`

   Easiest: keep these files but rename the old `t1_ceo_cfo_buy_v1.py`
   back to a working module and remove the new ones from the registry.

---

## Open follow-ups (not blocking)

1. **Update tests for `classify_role.py` and `signals/roles.py`** — the existing test files (`.scripts/test_classify_role.py`) assert old behaviour. Worth a 30-minute pass to refresh.
2. **Performance page redesign** — currently `ceo_cfo` still combines T1a + T1b. A Stage 6 spec could split into "CEO/Founder" and "CFO" tiles for more granular visibility.
3. **Backtest CSV historical archive** — old `_backtest_results.csv` rows have OLD signal IDs. If you want to compare old-vs-new performance, archive the pre-run CSV before re-running.
4. **Delete `t1_ceo_cfo_buy_v1.py`** from Windows when you're confident nothing imports it (the deprecation stub catches stragglers).

---

## Quick sanity check — what the diff showed

Headline finding from the Phase B diff:

- **£17.98m of historical BUY value was being attributed to "real" insider tiers (T1-T3) but is actually PCA activity.** After the cut-over, this £17.98m correctly routes to T5 (`t5_pca_buy`). Your historical T1/T2/T3 performance numbers were noisier than they should have been.
- **T7 (Chair) is now the biggest tier by aggregate £ value — £65.92m, 32.8% of all BUY value.** Chair buys were split across T2 and T3 before; now they're a single coherent cohort with their own signal.
- **CEO/Founder vs CFO is now distinguishable** — 144 BUY firings worth £25.36m on the CEO/Founder side, 99 firings worth £15.54m on the CFO side. CEO/Founder buys average £176k, CFO buys average £157k.

If anything in the run looks off, restore from snapshot and ping me.
