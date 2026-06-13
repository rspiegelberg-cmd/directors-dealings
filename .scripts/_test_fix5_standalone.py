"""Sprint 11 Fix #5 — standalone helper test.

One-shot scratch test used during Fix #5 build to validate the
_normalise_director_name helper against the exception JSON file
WITHOUT importing parse_pdmr.py. Needed only because FUSE staleness
prevented Claude's Linux sandbox from seeing the updated parse_pdmr.py
during the build session.

Safe to delete once Rupert has run the canonical Windows-side
`python -m unittest discover -s .scripts -p "test_*.py"` and the
Sprint11NameNormalisationTests class in test_parser.py has been
exercised. All 30 of the same assertions live in test_parser.py.
"""
# Intentionally minimal — the canonical home for these tests is
# .scripts/test_parser.py :: Sprint11NameNormalisationTests.
if __name__ == "__main__":
    print("Sprint 11 Fix #5 standalone — see test_parser.py for the canonical tests.")
