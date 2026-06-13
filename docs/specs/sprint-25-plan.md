# Sprint 25 — "PDMR editor" (spec 10)

**Date:** 2026-06-03 → 2026-06-04
**Spec:** `docs/specs/10-pdmr-editor.md`
**Roadmap ref:** Sprint 25 in `docs/specs/roadmap-2026-06-03.md`
**Status:** ✅ CLOSED 2026-06-04 — All 5 phases shipped

---

## Goal

Build the PDMR review & edit tool in four gated phases. Sprint 25 ships
Phase 0 (read-only) only. Phases 1–4 follow in later sessions.

---

## Phase 0 — Read-only review surface (THIS SPRINT) ✅

**Gate: Rupert can open `/review` in the browser, browse the pending queue
and search parsed transactions, and click through to the side-by-side viewer
— all without any writes to the DB or the queue file.**

### Deliverables

| File | Change |
|------|--------|
| `docs/specs/sprint-25-plan.md` | This file |
| `.scripts/export_dashboard_json.py` | Add `build_pending_review_export()` + `build_tx_index()` — writes `outputs/data/pending_review.json` and `outputs/data/tx_index.json` |
| `server.py` | Add `GET /review`, `GET /api/rns-html/<rns_id>`, `GET /api/tx/<fingerprint>` |
| `outputs/review.html` | New page: Tab A (pending queue), Tab B (tx search), side-by-side viewer |
| `.scripts/dashboard/render_company.py` | Add pencil icon per transaction row linking to `/review?tab=tx&fp={fingerprint}` |
| `.scripts/test_pdmr_editor_phase0.py` | Unit tests for Phase 0 |

### FUSE zone compliance

- `outputs/data/pending_review.json` and `outputs/data/tx_index.json` are
  written by `export_dashboard_json.py` (Windows Python, Zone B). Claude does
  not write them from bash.
- `server.py` reads `.scripts/_scrape_cache/{rns_id}.html` via `Path.read_bytes()`
  — read-only, Zone A safe.
- No SQLite writes in Phase 0.

---

## Phase 1 — Staging only (future sprint)

Add the editor fields + `POST /api/stage-edit` + `.data/_edit_queue.json` +
"show queued edits" view. Still no DB write — edits accumulate in the queue.

**Gate:** Inspect the queue JSON before proceeding.

---

## Phase 2 — Apply + audit (future sprint, Rupert-run Windows script)

`apply_edits.py`: validation, fingerprint cascade, audit JSONL, `.bak` backup,
plain UPDATE path first (non-key edits only).

**Gate:** QA agent review before Rupert applies to the real DB.

---

## Phase 3 — Fingerprint-changing edits + reject + multi-add + recompute ✅ 2026-06-04

- Fixed dead-code duplicate `elif action == "add"` in `apply_edits.py` run() — `resolved_rns_ids` now correctly populated for manual adds
- `run_pending_sweep.py`: `_load_already_resolved_ids()` + `_select_candidates()` skips rejected + manually-added RNS IDs (sticky protection end-to-end)
- `test_pdmr_editor_phase3.py` — 21 tests green

---

## Phase 4 — Undo + reject taxonomy + polish ✅ 2026-06-04

- `apply_delete` action in `apply_edits.py` — removes `parser_source='manual'` rows + signals; safeguards non-manual rows
- `GET /api/audit-log` in `server.py` — reads `_edit_audit.jsonl`, last 50 entries most-recent-first
- Audit & Undo collapsible panel in `review.html` — last 20 entries, Undo button per entry
- Phase badge → "Phase 4 — Complete"
- `test_pdmr_editor_phase4.py` — 22 tests green

**Additional fixes shipped same session:**
- `export_dashboard_json.py`: `extracted[:3]` array exported (Tab A pre-fill fix); `_load_resolved_rns_ids()` safety filter
- Review nav link added to all pages (Today, Performance, company, drilldowns, templates.py default)

---

## Questions answered before Phase 1

From spec §7, all decisions locked:

1. **Write path:** Option 1 (stage-then-apply via `_edit_queue.json`). ✅
2. **Sticky protection:** `parser_source='manual'` wins; ingest/reparse skips those RNS IDs. ✅
3. **Recompute:** Auto-run 4-step pipeline at end of `apply_edits.py`, with `--no-pipeline` flag. ✅
4. **Undo depth:** Single-step undo v1; `.bak` for disaster. ✅
5. **Reject taxonomy:** Record reason (junk / boilerplate / foreign_currency / duplicate). ✅
6. **Tab A scope:** Show manually-recoverable buckets first (corporate_actions, could_not_classify,
   data_quirks, multi_tranche, other). Hide bundled_multi_pdmr and foreign_currency by default
   (toggle to show). ✅
