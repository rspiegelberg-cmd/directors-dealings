"""B-024 AST regression test for the db_health backup pattern.

Asserts every script in DB_WRITERS calls the canonical pattern:
  1. imports db_health
  2. calls db_health.check()  somewhere in main()/run()/_main()
  3. calls db_health.backup() somewhere in main()/run()/_main()
  4. calls db_health.seal()   somewhere in main()/run()/_main()

The point is to catch future drift. If a Sprint adds a new DB-writing
script and forgets the pattern, this test goes red. Likewise if anyone
removes the pattern from one of the listed scripts (e.g. by accident
during a refactor).

Cheap — pure AST, no DB, no filesystem writes. Safe to run in Claude's
Linux sandbox per CLAUDE.md's audited-safe list.

The seven scripts in DB_WRITERS are the Zone B scripts Rupert runs
standalone from PowerShell. The first four were wired up by B-024.
The last three (eval_signals, repair_dates, classify_issuers) were
wired up by C-1, C-2, C-3 on 2026-05-20 — they serve as "must remain
green" sentinels protecting against future regressions.

Reference pattern: see classify_issuers.py:run() — the canonical C-3
implementation. Each script in DB_WRITERS follows the same six-step
shape (pre-run integrity check, pre-run backup, transactional write,
post-run integrity check, conditional seal()).

RUNNING (Windows, Python 3.13+ requires discover form):
    python -m unittest discover -s .scripts -p "test_scripts_have_backup_pattern.py" -v

RUNNING (Claude bash sandbox, safe per CLAUDE.md):
    cd /sessions/<session>/mnt/DirectorsDealings && \
        python -m unittest discover -s .scripts -p "test_scripts_have_backup_pattern.py" -v
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Scripts that write to directors.db and must follow the db_health pattern.
# Adding a new DB-writing script? Add it here and apply the pattern.
DB_WRITERS = [
    # Wired up by B-024 (2026-05-21) — Sprint 4:
    "run_scrape.py",
    "backfill_filings.py",
    "backfill_prices.py",
    "backtest.py",
    # Wired up by B-029 (2026-05-21) — Sprint 4:
    "backfill_announced_at.py",
    # Wired up by C-1, C-2, C-3 (2026-05-20) — must remain green:
    "eval_signals.py",
    "repair_dates.py",
    "classify_issuers.py",
]


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _imports_db_health(tree: ast.AST) -> bool:
    """True if the module has `import db_health` or
    `from db_health import ...` anywhere."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "db_health":
                    return True
        if isinstance(node, ast.ImportFrom):
            if node.module == "db_health":
                return True
    return False


def _entry_functions(tree: ast.AST) -> list[ast.FunctionDef]:
    """Return top-level functions named main / run / _main."""
    entries: list[ast.FunctionDef] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("main", "run", "_main"):
            entries.append(node)
    return entries


def _calls_db_health_func(func_node: ast.AST, func_name: str) -> bool:
    """True if `db_health.<func_name>(...)` is called anywhere inside func_node.

    Conservative — only matches the `db_health.foo()` form. If a script
    uses `from db_health import foo` directly that's fine in principle,
    but the convention across this project is the dotted form (mirrors
    the canonical C-3 reference in classify_issuers.py), so requiring it
    also serves as a style guard.
    """
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            f = node.func
            if (
                isinstance(f, ast.Attribute)
                and f.attr == func_name
                and isinstance(f.value, ast.Name)
                and f.value.id == "db_health"
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScriptsHaveBackupPattern(unittest.TestCase):
    """Verify every DB-writing script follows the C-3 db_health pattern."""

    def _read_tree(self, fname: str) -> ast.AST:
        path = HERE / fname
        self.assertTrue(
            path.exists(),
            f"DB_WRITERS lists {fname} but the file does not exist. "
            "Either remove it from DB_WRITERS or create the script.",
        )
        return ast.parse(path.read_text(encoding="utf-8"), filename=fname)

    def test_each_script_imports_db_health(self) -> None:
        for fname in DB_WRITERS:
            with self.subTest(script=fname):
                tree = self._read_tree(fname)
                self.assertTrue(
                    _imports_db_health(tree),
                    f"{fname} does not import db_health. Add "
                    "`import db_health` and follow the C-3 pattern in "
                    "classify_issuers.py:run().",
                )

    def test_each_script_has_entry_function(self) -> None:
        for fname in DB_WRITERS:
            with self.subTest(script=fname):
                tree = self._read_tree(fname)
                entries = _entry_functions(tree)
                self.assertTrue(
                    entries,
                    f"{fname} has no main(), run() or _main() function. "
                    "The pattern requires a recognisable entry point; "
                    "wrap the top-level work in a function.",
                )

    def test_each_script_calls_check(self) -> None:
        """Integrity check before backup protects the .bak from being
        overwritten with garbage if the live DB is already corrupt."""
        for fname in DB_WRITERS:
            with self.subTest(script=fname):
                tree = self._read_tree(fname)
                entries = _entry_functions(tree)
                hit = any(_calls_db_health_func(e, "check") for e in entries)
                self.assertTrue(
                    hit,
                    f"{fname} does not call db_health.check() in its "
                    "entry function. See classify_issuers.py:run() for "
                    "the canonical C-3 pre-run check.",
                )

    def test_each_script_calls_backup(self) -> None:
        for fname in DB_WRITERS:
            with self.subTest(script=fname):
                tree = self._read_tree(fname)
                entries = _entry_functions(tree)
                hit = any(_calls_db_health_func(e, "backup") for e in entries)
                self.assertTrue(
                    hit,
                    f"{fname} does not call db_health.backup() in its "
                    "entry function. Pre-run snapshot is mandatory for "
                    "Zone B scripts — see classify_issuers.py:run().",
                )

    def test_each_script_calls_seal(self) -> None:
        for fname in DB_WRITERS:
            with self.subTest(script=fname):
                tree = self._read_tree(fname)
                entries = _entry_functions(tree)
                hit = any(_calls_db_health_func(e, "seal") for e in entries)
                self.assertTrue(
                    hit,
                    f"{fname} does not call db_health.seal() in its "
                    "entry function. Post-run seal is mandatory — see "
                    "classify_issuers.py:run() for the canonical pattern.",
                )


if __name__ == "__main__":
    unittest.main()
