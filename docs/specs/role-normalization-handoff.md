# B-025 Phase A — Hand-off package for Rupert

**Status:** Phase A code complete, awaiting your backfill run.
**Date:** 2026-05-20

---

## What's been built

### New files
- `.scripts/role_normalize.py` — the canonical mapper (`normalize_role()`)
- `.scripts/backfill_role_normalized.py` — Zone B script to populate the column
- `.scripts/schema_migrations/004_add_role_normalized.sql` — adds the column
- `.scripts/test_role_normalize.py` — 82 unit tests (all passing)
- `.scripts/test_role_backfill.py` — 6 integration tests (all passing)
- `docs/specs/role-normalization-pass.md` — full spec
- `docs/specs/role-normalization-handoff.md` — this file

### Edited files
- `.scripts/db.py` — adds migration step 3→4; `upsert_transaction` populates `role_normalized` on new inserts
- `.scripts/export_dashboard_json.py` — emits `role_normalized` in JSON payloads
- `.scripts/backtest.py` — adds `role_normalized` column to CSV (raw `role` and `role_class` retained)
- `.scripts/dashboard/render_helpers.py` — `role_chip()` uses canonical bucket palette
- `.scripts/dashboard/render_company.py` — buy-marker exec/NED classification uses canonical buckets
- `dashboard/index.html` — JS `roleChipCls()` uses canonical bucket palette
- `docs/backlog.md` — B-025 entry with Phase A/B plan

---

## What you need to run (PowerShell, in order)

### Step 1 — Dry-run the backfill first
Confirms the migration applies and shows the projected bucket distribution.
No writes to the DB.

```powershell
cd C:\Dev\DirectorsDealings
python .scripts\backfill_role_normalized.py --dry-run
```

**Expected output:** distribution table showing roughly:
- CEO ≈ 385 (18.3%)
- CFO ≈ 228 (10.8%)
- NED ≈ 504 (23.9%)
- Other / unclassified ≈ 60 (2.8%)
- Parser fragment ≈ 57 (2.7%)
- All 5 acceptance floor checks: PASS

If anything looks wildly off, stop and tell me.

### Step 2 — Apply the backfill
Takes a fresh `.bak` snapshot first (the auto-backup is broken per memory),
runs migration 004 if needed, populates `role_normalized` for every row,
verifies integrity.

```powershell
python .scripts\backfill_role_normalized.py
```

**Expected output:** same distribution as the dry-run, exits 0, prints
"Backfill complete. role_normalized is now populated for all 2108 rows."

A pre-run snapshot will be saved to:
`.data\directors.db.bak-pre-role-normalize-20260520`

**If anything goes wrong**, restore from the snapshot:
```powershell
copy .data\directors.db.bak-pre-role-normalize-20260520 .data\directors.db
```

### Step 3 — Run the test suite
Confirms nothing else broke.

```powershell
python -m unittest discover -s .scripts -p "test_*.py" 2>&1
```

The 82 + 6 new tests should all pass alongside whatever was there before.

### Step 4 — Regenerate the dashboard
The cosmetic chip code now reads `role_normalized`, so the dashboard
needs a re-render.

```powershell
python .scripts\export_dashboard_json.py
python .scripts\build_dashboard.py
```

Open `dashboard\index.html` and verify the role chips show the new
short labels: CEO, CFO, NED, Chair, NE Chair, Founder, Div Exec, etc.
Hover over a chip to see the full raw role as a tooltip.

---

## What's deliberately NOT yet done (Phase B)

These are gated on you reviewing a diff report:

- **Signal engine cut-over.** `signals/roles.py` (T1/T2/T3/T4 firing) and
  `classify_role.py` (Performance role tiles) still read raw `role`. They
  will continue to misfire on case variants until Phase B.
- **Re-run historical backtest.** When Phase B lands, T1-T4 firing counts
  will shift — case-variant misfires get corrected, regional CEOs move
  from T1 to T2, etc. We do this only after you've seen the delta.
- **Performance page "Top buys by role" tiles.** Still bucket as
  `ceo_cfo / other_exec / ned`. Will switch to the 14 buckets in Phase B.

## When you're ready for Phase B

Tell me "let's do Phase B". I'll:

1. Run a diff: T1-T4 firing counts using raw `role` vs `role_normalized`.
2. Generate a CSV of which historical firings move bucket.
3. Show you the delta. You sign off (or push back).
4. Cut `signals/roles.py` and `classify_role.py` over.
5. Re-run backtest. Updated performance numbers replace old.

Estimated Phase B time: ~1-2 hours of my work + your sign-off gate.

---

## Key facts to keep in mind

- **The mapper is conservative.** It routes to "Other / unclassified" when
  in doubt rather than guessing into a load-bearing bucket. You shouldn't
  see signal noise increase from Phase A alone.
- **Raw `role` is preserved everywhere.** Audit trail intact. You can
  always see what the RNS form actually said.
- **The auto-backup is still broken.** This backfill script takes its own
  manual `.bak` first, so you're protected for this run specifically.
  B-024 (auto-backup fix) remains an open backlog item.
- **88 tests** cover the mapper + backfill atomicity. All passing.

## If you want to roll back

Restore from the snapshot:
```powershell
copy .data\directors.db.bak-pre-role-normalize-20260520 .data\directors.db
```

The schema migration is forward-only but harmless — the `role_normalized`
column on a rolled-back DB is just NULL for all rows. To fully undo:

```powershell
# Restore DB
copy .data\directors.db.bak-pre-role-normalize-20260520 .data\directors.db
# Then revert all the Phase A files via git or your editor
```

Code changes are isolated to the 7 files listed at the top of this doc.
