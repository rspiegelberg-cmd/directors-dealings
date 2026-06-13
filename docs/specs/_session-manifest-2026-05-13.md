# Session manifest — 2026-05-13

A record of every artefact produced or modified in the Cowork session of 2026-05-13. Reference for syncing to `C:\Dev\DirectorsDealings` and for future sessions wanting to know what came from this conversation.

## Session theme

Dashboard-designer agent created; Stage 5 + Stage 5.1 + Stage 4.6 + Stage 4.5 specs drafted; wireframes v1 → v2 → scoreboard-with-tooltips → company-page mockup iterated with Rupert.

## Files produced (all on the Cowork-mounted folder)

| Path (relative to project root) | Lines | Purpose |
|---|---|---|
| `CLAUDE.md` | 55 | Project guide pointing at the dashboard-designer agent |
| `docs/agents/dashboard-designer.md` | 89 | Designer agent w/ continuous model-assessment mandate |
| `docs/specs/stage-05-readme.md` | 41 | Read-me-first orientation linking all Stage 5 specs |
| `docs/specs/stage-05-design-notes.md` | 94 | Locked design decisions + sparkline + tooltips |
| `docs/specs/stage-05-build-spec.md` | 201 | Main dashboard build brief |
| `docs/specs/stage-05-1-company-page.md` | 153 | Per-ticker detail page spec |
| `docs/specs/stage-04-6-dashboard-exporter.md` | 140 | SQLite + backtest CSV → JSON pipe |
| `docs/specs/stage-04-5-data-quality.md` | 51 | F1 +4232% outlier gating fix |

## Memory entries updated (not on disk in project folder)

- `feedback_truncation_check_mandatory.md` — added three 2026-05-13 truncation incidents and codified bash-heredoc as default for spec writes over ~100 lines.
- `reference_dashboard_designer_agent.md` — created earlier in session, indexed in MEMORY.md.

## To mirror this folder to C:\Dev\DirectorsDealings

Run in Windows PowerShell or cmd:

```
robocopy "C:\Users\Rupert Spiegelberg\Documents\Claude\Projects\Directors Dealings" "C:\Dev\DirectorsDealings" /E /XO /NFL /NDL
```

`/E` recurses subfolders, `/XO` skips files older than destination (won't overwrite newer manual edits), `/NFL /NDL` quiets the per-file logging.

To do a one-way dry-run first (recommended):
```
robocopy "C:\Users\Rupert Spiegelberg\Documents\Claude\Projects\Directors Dealings" "C:\Dev\DirectorsDealings" /E /XO /L
```

The `/L` flag lists what would change without actually copying. Inspect the list, then re-run without `/L`.

## Stage 5 reading order (when you come back)

1. `docs/specs/stage-05-readme.md`
2. `docs/specs/stage-05-design-notes.md`
3. `docs/specs/stage-05-build-spec.md`
4. `docs/specs/stage-05-1-company-page.md`
5. `docs/specs/stage-04-6-dashboard-exporter.md`
6. `docs/specs/stage-04-5-data-quality.md`

## Open calls awaiting your decision

- Confirm or kill the "brewing" cluster definition (2+ directors, most recent buy 30–90 days back).
- One-click deprecate-button toast vs write-to-disk endpoint.
- Default theme: dark or light for v1.
