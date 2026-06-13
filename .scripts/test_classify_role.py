"""Unit tests for the role classifier (Sprint 1 — Performance page redesign v1).

The 12 cases below come from the backend plan §3.1 / Step 2 test matrix in
`docs/specs/performance-page-redesign-v1-backend-plan.md`. Three additional
edge cases are appended to cover the Chairman precedence (Rupert Q4) and
the never-raises contract.

B-027 (2026-05-21): refreshed to match B-025 Phase B's 6-tier output. The
classifier USED to return 'ceo_cfo' / 'other_exec' / 'ned' / None. It now
returns one of:
    * 't1a' — CEO / Founder bucket
    * 't1b' — CFO bucket
    * 't2'  — other senior exec (COO, MD, President, Group Exec Director,
              Chief X Officer, etc.)
    * 't3'  — Non-Executive Director / Senior Independent Director /
              Non-executive Chairman
    * 't5'  — PCA (Person Closely Associated)
    * 't7'  — Chair (bare 'Chairman' / 'Chair')
    * None  — catch-all (T4 / T6 / parser fragments)

Precedence is unchanged — CEO still beats Chair, NED still beats Chair.

Run under:
    python -m unittest .scripts.test_classify_role
or:
    python -m unittest discover -s .scripts -p "test_*.py"
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from classify_role import classify_role  # noqa: E402


class TestClassifyRoleMatrix(unittest.TestCase):
    """The 12-case matrix from backend plan §3.1, refreshed for Phase B tiers."""

    def test_01_chief_executive_officer_maps_to_t1a(self):
        self.assertEqual(classify_role("", "Chief Executive Officer"), "t1a")

    def test_02_chief_executive_and_chairman_maps_to_t1a(self):
        # Critical: CEO must beat Chair — "Chief Executive" wins over "Chairman".
        self.assertEqual(
            classify_role("", "Chief Executive Officer and Chairman"),
            "t1a",
        )

    def test_03_non_executive_director_maps_to_t3(self):
        self.assertEqual(classify_role("", "Non-Executive Director"), "t3")

    def test_04_senior_independent_director_maps_to_t3(self):
        self.assertEqual(classify_role("", "Senior Independent Director"), "t3")

    def test_05_bare_chairman_maps_to_t7(self):
        # Per Rupert Q4: a plain "Chairman" with no qualifier → t7 (Chair tile).
        self.assertEqual(classify_role("", "Chairman"), "t7")

    def test_06_group_executive_director_maps_to_t2(self):
        self.assertEqual(
            classify_role("", "Group Executive Director"),
            "t2",
        )

    def test_07_chief_operating_officer_maps_to_t2(self):
        # Must NOT mis-classify as t1a — only "Chief Executive" and "Chief
        # Financial" hit the CEO / CFO branches.
        self.assertEqual(
            classify_role("", "Chief Operating Officer"),
            "t2",
        )

    def test_08_t1_role_class_with_empty_role_str_maps_to_t1a(self):
        # role_class fallback path — when role_str is empty.
        # Legacy "T1" maps to t1a (the larger half — see classify_role.py).
        self.assertEqual(classify_role("T1", ""), "t1a")

    def test_09_empty_role_class_and_empty_role_str_returns_none(self):
        self.assertIsNone(classify_role("", ""))

    def test_10_none_inputs_return_none(self):
        # Contract: never raises on None inputs.
        self.assertIsNone(classify_role(None, None))

    def test_11_company_secretary_returns_none(self):
        # Catch-all bucket — not a director-role we surface in the role tile.
        self.assertIsNone(classify_role("", "Company Secretary"))

    def test_12_non_executive_no_hyphen_maps_to_t3(self):
        # `normalize_role()` tolerates the unhyphenated form.
        self.assertEqual(classify_role("", "Non Executive Director"), "t3")


class TestClassifyRoleEdgeCases(unittest.TestCase):
    """Three additional cases that pin Rupert's locked decisions and
    the never-raises contract."""

    def test_13_non_executive_chairman_maps_to_t3(self):
        # Rupert Q4: "Non-Executive Chairman" must hit the NED branch before
        # the Chair branch. This is the key precedence test.
        self.assertEqual(
            classify_role("", "Non-Executive Chairman"),
            "t3",
        )

    def test_14_t3_role_class_with_empty_role_str_maps_to_t3(self):
        # role_class fallback path for NED.
        self.assertEqual(classify_role("T3", ""), "t3")

    def test_15_unmappable_garbage_never_raises_returns_none(self):
        # Belt-and-braces: weird inputs (numbers, whitespace, garbage strings)
        # must return None, never raise. The function signature is permissive
        # because real CSV rows can carry anything.
        try:
            result_garbage = classify_role("", "!!! @@@ ###")
            result_whitespace = classify_role("   ", "   ")
            result_t4 = classify_role("T4", "Person of significant control")
        except Exception as e:  # pragma: no cover — guard rail
            self.fail(f"classify_role raised on garbage input: {e!r}")
        self.assertIsNone(result_garbage)
        self.assertIsNone(result_whitespace)
        # T4 in role_class maps to None per _LEGACY_ROLE_CLASS_TO_TILE.
        self.assertIsNone(result_t4)


class TestClassifyRoleSprint2Patch(unittest.TestCase):
    """Sprint 2 diagnostic patch (2026-05-18): six tests pinning the regex
    additions that recover ~48-59 corpus rows from the None bucket.

    Post-Phase-B, the bucket labels these recover into are now t1b (CFO
    variants) and t2 (Chief-X-Officer, MD, President).

    Precedence is unchanged — t1a / t1b still win over t2 for
    "Chief Executive" / "Chief Financial"."""

    def test_16_finance_director_maps_to_t1b(self):
        # UK convention: "Finance Director" is the UK equivalent of CFO.
        self.assertEqual(classify_role("", "Finance Director"), "t1b")

    def test_17_financial_director_maps_to_t1b(self):
        # Variant spelling — same UK CFO concept.
        self.assertEqual(classify_role("", "Financial Director"), "t1b")

    def test_18_chief_finance_officer_maps_to_t1b(self):
        # US / Australian variant of CFO (vs the more common "Chief Financial
        # Officer"). The bare "Chief Finance" pattern catches this.
        self.assertEqual(
            classify_role("", "Chief Finance Officer"),
            "t1b",
        )

    def test_19_chief_marketing_officer_maps_to_t2(self):
        # Generic "Chief <X> Officer" catch — also covers CIO (Investment),
        # CTO (when spelled out), CDO, CSO, etc.
        self.assertEqual(
            classify_role("", "Chief Marketing Officer"),
            "t2",
        )
        # Sanity: a complex multi-word variant must also match.
        self.assertEqual(
            classify_role("", "Chief Digital & Technology Officer"),
            "t2",
        )

    def test_20_managing_director_and_president_map_to_t2(self):
        self.assertEqual(
            classify_role("", "Managing Director"),
            "t2",
        )
        self.assertEqual(
            classify_role("", "Senior Vice President"),
            "t2",
        )
        self.assertEqual(
            classify_role("", "President Corporate Development"),
            "t2",
        )

    def test_21_patch_does_not_break_existing_precedence(self):
        # Regression guard: the patch must NOT mis-classify a CEO/CFO into
        # t2 via the new generic Chief-X-Officer pattern. The CEO / CFO
        # bucket lookup runs first and catches "Chief Executive Officer"
        # before any other-exec pattern sees it.
        self.assertEqual(
            classify_role("", "Chief Executive Officer"),
            "t1a",
        )
        self.assertEqual(
            classify_role("", "Chief Financial Officer"),
            "t1b",
        )
        # And "Non-Executive Chairman" still routes to t3, not t7.
        self.assertEqual(
            classify_role("", "Non-Executive Chairman"),
            "t3",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
