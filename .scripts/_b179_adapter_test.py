"""B-179 throwaway test for the ?->%s placeholder translator.

Run:  python .scripts/_b179_adapter_test.py
Verifies translate_placeholders preserves literal ? inside string literals,
comments, and dollar-quotes, while converting real placeholders.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from db import translate_placeholders as T  # noqa: E402

CASES = [
    # (input, expected)
    ("SELECT * FROM t WHERE a = ? AND b = ?",
     "SELECT * FROM t WHERE a = %s AND b = %s"),
    # ? inside a single-quoted literal must be preserved (GLOB pattern).
    ("SELECT * FROM t WHERE d NOT GLOB '????-??-??*' AND x = ?",
     "SELECT * FROM t WHERE d NOT GLOB '????-??-??*' AND x = %s"),
    # LIKE literal with % must be untouched; trailing placeholder converts.
    ("SELECT * FROM t WHERE u LIKE '%investegate%' AND y = ?",
     "SELECT * FROM t WHERE u LIKE '%investegate%' AND y = %s"),
    # No params at all -> unchanged.
    ("SELECT count(*) FROM t",
     "SELECT count(*) FROM t"),
    # '' escaped quote inside a literal, then a real placeholder.
    ("INSERT INTO t (s) VALUES ('it''s ok?') WHERE z = ?",
     "INSERT INTO t (s) VALUES ('it''s ok?') WHERE z = %s"),
    # ? inside a -- line comment is preserved; one after on next line converts.
    ("SELECT 1 -- is this a ? in a comment\nWHERE a = ?",
     "SELECT 1 -- is this a ? in a comment\nWHERE a = %s"),
    # ? inside a /* block comment */ preserved.
    ("SELECT /* a ? here */ 1 WHERE a = ?",
     "SELECT /* a ? here */ 1 WHERE a = %s"),
    # double-quoted identifier containing ? preserved.
    ('SELECT "wat?col" FROM t WHERE a = ?',
     'SELECT "wat?col" FROM t WHERE a = %s'),
    # already-%s (db.py internal SQL on PG) left alone, ? converted.
    ("SELECT value FROM meta WHERE key = %s OR key = ?",
     "SELECT value FROM meta WHERE key = %s OR key = %s"),
]

fails = 0
for i, (src, exp) in enumerate(CASES):
    got = T(src)
    ok = got == exp
    if not ok:
        fails += 1
        print(f"CASE {i} FAIL")
        print(f"  in : {src!r}")
        print(f"  exp: {exp!r}")
        print(f"  got: {got!r}")
    else:
        print(f"CASE {i} ok")

print(f"\n{len(CASES) - fails}/{len(CASES)} passed")
sys.exit(1 if fails else 0)
