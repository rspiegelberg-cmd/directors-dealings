"""Unit tests for role_normalize.normalize_role.

Test fixtures are drawn from the live DB inspection on 2026-05-20.
Covers every precedence rule and edge case the spec calls out.
"""
from __future__ import annotations

import unittest

from role_normalize import (
    BUCKETS,
    CEO, CFO, OTHER_CHIEF, CHAIR_EXEC, CHAIR_NON_EXEC, NED,
    EXEC_DIRECTOR, DIVISIONAL, FOUNDER, PRESIDENT_VP,
    COMPANY_SECRETARY, PCA, PDMR_ONLY, OTHER, PARSER_FRAGMENT,
    normalize_role,
)


class TestNullAndBlank(unittest.TestCase):
    def test_none_input(self):
        self.assertEqual(normalize_role(None), OTHER)

    def test_empty_string(self):
        self.assertEqual(normalize_role(""), OTHER)

    def test_whitespace_only(self):
        self.assertEqual(normalize_role("   "), OTHER)


class TestCEO(unittest.TestCase):
    def test_chief_executive_officer(self):
        self.assertEqual(normalize_role("Chief Executive Officer"), CEO)

    def test_uppercase_ceo(self):
        self.assertEqual(normalize_role("CEO"), CEO)

    def test_chief_executive_only(self):
        self.assertEqual(normalize_role("Chief Executive"), CEO)

    def test_group_ceo_stays_ceo(self):
        # Group CEO == Group plc's CEO in UK usage — must stay CEO,
        # not be misrouted to Divisional.
        self.assertEqual(normalize_role("Group CEO"), CEO)
        self.assertEqual(normalize_role("Group Chief Executive Officer"), CEO)
        self.assertEqual(normalize_role("Group Chief Executive"), CEO)

    def test_ceo_with_pdmr_qualifier(self):
        self.assertEqual(
            normalize_role("Chief Executive Officer (Director/PDMR)"), CEO,
        )
        self.assertEqual(normalize_role("Chief Executive / PDMR"), CEO)
        self.assertEqual(
            normalize_role("Chief Executive Officer/Director"), CEO,
        )

    def test_ceo_designate(self):
        self.assertEqual(normalize_role("CEO Designate"), CEO)

    def test_lowercase_variant(self):
        self.assertEqual(normalize_role("Chief executive officer"), CEO)


class TestCFO(unittest.TestCase):
    def test_chief_financial_officer(self):
        self.assertEqual(normalize_role("Chief Financial Officer"), CFO)

    def test_cfo(self):
        self.assertEqual(normalize_role("CFO"), CFO)

    def test_finance_director(self):
        self.assertEqual(normalize_role("Finance Director"), CFO)

    def test_group_finance_director(self):
        self.assertEqual(normalize_role("Group Finance Director"), CFO)

    def test_group_cfo(self):
        self.assertEqual(normalize_role("Group Chief Financial Officer"), CFO)

    def test_financial_director(self):
        self.assertEqual(normalize_role("Financial Director"), CFO)

    def test_cfo_with_other_title(self):
        self.assertEqual(
            normalize_role("Executive Director and Chief Financial Officer"),
            CFO,
        )


class TestOtherChief(unittest.TestCase):
    def test_coo(self):
        self.assertEqual(
            normalize_role("Chief Operating Officer"), OTHER_CHIEF,
        )

    def test_cto(self):
        self.assertEqual(
            normalize_role("Chief Technology Officer"), OTHER_CHIEF,
        )

    def test_chief_commercial(self):
        self.assertEqual(
            normalize_role("Chief Commercial Officer"), OTHER_CHIEF,
        )

    def test_chief_risk_officer(self):
        self.assertEqual(
            normalize_role("Group Chief Risk Officer/PDMR"), OTHER_CHIEF,
        )

    def test_chief_marketing(self):
        self.assertEqual(
            normalize_role("Chief Marketing, Data and Sustainability Officer"),
            OTHER_CHIEF,
        )


class TestChairExecutive(unittest.TestCase):
    def test_bare_chair(self):
        self.assertEqual(normalize_role("Chair"), CHAIR_EXEC)

    def test_chairman(self):
        self.assertEqual(normalize_role("Chairman"), CHAIR_EXEC)

    def test_executive_chair(self):
        self.assertEqual(normalize_role("Executive Chair"), CHAIR_EXEC)

    def test_executive_chairman(self):
        self.assertEqual(normalize_role("Executive Chairman"), CHAIR_EXEC)


class TestChairNonExec(unittest.TestCase):
    """Rupert Q4 (broadened 2026-05-21): ANY chair role that's
    non-executive or independent buckets with NEDs, not Chairs. The
    legacy CHAIR_NON_EXEC bucket is no longer returned by
    normalize_role — these all route to NED.

    The class name is kept for git-history continuity; semantically
    these are now NED tests.
    """

    def test_non_executive_chairman(self):
        self.assertEqual(
            normalize_role("Non-Executive Chairman"), NED,
        )

    def test_non_executive_chair(self):
        self.assertEqual(normalize_role("Non-Executive Chair"), NED)

    def test_non_executive_chair_lowercase(self):
        self.assertEqual(normalize_role("Non-executive Chair"), NED)

    def test_independent_non_executive_chairman(self):
        self.assertEqual(
            normalize_role("Independent Non-Executive Chairman"),
            NED,
        )

    def test_non_exec_director_chair_of_board(self):
        # "Non-executive Director (Chair of the Board)" — under broad Q4,
        # this person is primarily a NED with chair responsibilities,
        # not a separate Chair bucket. Routes to NED.
        self.assertEqual(
            normalize_role("Non-executive Director (Chair of the Board)"),
            NED,
        )


class TestNED(unittest.TestCase):
    """NED must work across all case/spacing variants and pull in
    Senior Independent Director and bare 'Director'."""

    def test_canonical(self):
        self.assertEqual(
            normalize_role("Non-Executive Director"), NED,
        )

    def test_case_variant_1(self):
        self.assertEqual(
            normalize_role("Non-executive Director"), NED,
        )

    def test_case_variant_2(self):
        self.assertEqual(
            normalize_role("Non-executive director"), NED,
        )

    def test_case_variant_3(self):
        self.assertEqual(
            normalize_role("NON-EXECUTIVE DIRECTOR"), NED,
        )

    def test_independent_non_exec(self):
        self.assertEqual(
            normalize_role("Independent Non-Executive Director"), NED,
        )

    def test_senior_independent_director(self):
        self.assertEqual(
            normalize_role("Senior Independent Director"), NED,
        )

    def test_supervisory_board(self):
        self.assertEqual(
            normalize_role("Member of the Supervisory Board of Canal+ SA"),
            NED,
        )

    def test_bare_director_maps_to_ned(self):
        """UK convention: bare 'Director' on an RNS Form-310 is NED."""
        self.assertEqual(normalize_role("Director"), NED)
        self.assertEqual(normalize_role("DIRECTOR"), NED)


class TestExecutiveDirector(unittest.TestCase):
    def test_executive_director(self):
        self.assertEqual(
            normalize_role("Executive Director"), EXEC_DIRECTOR,
        )

    def test_managing_director(self):
        self.assertEqual(
            normalize_role("Managing Director"), EXEC_DIRECTOR,
        )

    def test_business_development_director(self):
        # Business Development Director is operational, not a Chief title.
        self.assertEqual(
            normalize_role("Business Development Director"), EXEC_DIRECTOR,
        )

    def test_bare_executive(self):
        self.assertEqual(normalize_role("Executive"), EXEC_DIRECTOR)


class TestDivisional(unittest.TestCase):
    def test_ceo_north_america(self):
        self.assertEqual(
            normalize_role("Chief Executive Officer, North America"),
            DIVISIONAL,
        )

    def test_ceo_europe(self):
        self.assertEqual(
            normalize_role("CEO Continental Europe"), DIVISIONAL,
        )

    def test_ceo_savills(self):
        self.assertEqual(
            normalize_role("CEO, Savills UK & EMEA/ PDMR"), DIVISIONAL,
        )

    def test_managing_director_regional(self):
        self.assertEqual(
            normalize_role("Managing Director: Recruitment Ireland"),
            DIVISIONAL,
        )

    def test_regional_director(self):
        self.assertEqual(
            normalize_role("REGIONAL DIRECTOR"), DIVISIONAL,
        )

    def test_managing_director_apac(self):
        self.assertEqual(
            normalize_role("Managing Director APAC"), DIVISIONAL,
        )


class TestFounder(unittest.TestCase):
    def test_president_and_founder(self):
        self.assertEqual(
            normalize_role("President and Founder"), FOUNDER,
        )

    def test_founder_and_president(self):
        self.assertEqual(
            normalize_role("Founder & President"), FOUNDER,
        )

    def test_founder_must_beat_ceo(self):
        # If we ever see "CEO and Founder", Founder takes precedence.
        self.assertEqual(
            normalize_role("CEO and Founder"), FOUNDER,
        )


class TestPresidentVP(unittest.TestCase):
    def test_division_president(self):
        self.assertEqual(
            normalize_role("Division President"), PRESIDENT_VP,
        )

    def test_svp(self):
        self.assertEqual(
            normalize_role("group Senior Executive Vice-President"),
            PRESIDENT_VP,
        )

    def test_vp_corporate(self):
        self.assertEqual(
            normalize_role("VP, Corporate Affairs"), PRESIDENT_VP,
        )


class TestCompanySecretary(unittest.TestCase):
    def test_company_secretary(self):
        self.assertEqual(
            normalize_role("Company Secretary"), COMPANY_SECRETARY,
        )

    def test_general_counsel(self):
        self.assertEqual(
            normalize_role("General Counsel"), COMPANY_SECRETARY,
        )

    def test_combined(self):
        self.assertEqual(
            normalize_role("General Counsel and Company Secretary"),
            COMPANY_SECRETARY,
        )


class TestPCA(unittest.TestCase):
    """PCA must beat every exec title that follows it."""

    def test_pca_alone(self):
        self.assertEqual(normalize_role("PCA"), PCA)

    def test_pca_to_pdmr(self):
        self.assertEqual(normalize_role("PCA to PDMR"), PCA)

    def test_pca_of_chair(self):
        # The critical precedence test: PCA must beat Chair.
        self.assertEqual(
            normalize_role("PCA to Jim Brown, Chair"), PCA,
        )

    def test_pca_of_ceo(self):
        self.assertEqual(
            normalize_role(
                "PCA - Spouse of Peter Duffy, Chief Executive Officer",
            ),
            PCA,
        )

    def test_closely_associated(self):
        self.assertEqual(
            normalize_role(
                "Person closely associated with Jack Pailing, "
                "Non-Executive Director",
            ),
            PCA,
        )

    def test_spouse_of(self):
        self.assertEqual(
            normalize_role(
                "Spouse of Chief Financial Officer, David Arnold",
            ),
            PCA,
        )


class TestPDMROnly(unittest.TestCase):
    def test_bare_pdmr(self):
        self.assertEqual(normalize_role("PDMR"), PDMR_ONLY)

    def test_director_pdmr(self):
        self.assertEqual(normalize_role("Director/PDMR"), PDMR_ONLY)

    def test_pdmr_of_the_company(self):
        self.assertEqual(normalize_role("PDMR of the Company"), PDMR_ONLY)


class TestParserFragment(unittest.TestCase):
    """The data-quality flag — strings that aren't roles at all."""

    def test_table_pipe_leak(self):
        self.assertTrue(
            normalize_role(
                "| Partnership Shares | Matching Shares | Total Sha",
            ) == PARSER_FRAGMENT,
        )

    def test_nature_of_transaction(self):
        self.assertEqual(
            normalize_role("Nature of the transaction"), PARSER_FRAGMENT,
        )

    def test_number_of_shares(self):
        self.assertEqual(
            normalize_role("Number of shares acquired"), PARSER_FRAGMENT,
        )

    def test_price_paid(self):
        self.assertEqual(
            normalize_role("Price paid per share"), PARSER_FRAGMENT,
        )

    def test_as_per_1a(self):
        self.assertEqual(normalize_role("As per 1(a)"), PARSER_FRAGMENT)
        self.assertEqual(normalize_role("(1a)"), PARSER_FRAGMENT)

    def test_sentence_fragment(self):
        self.assertEqual(
            normalize_role(
                "the business to capitalise on opportunities in its markets",
            ),
            PARSER_FRAGMENT,
        )


class TestPrecedenceContract(unittest.TestCase):
    """These tests pin down the precedence rules. If any fail, the mapper
    is making a categorisation error that will affect signal firing."""

    def test_pca_beats_chair(self):
        # "Person Closely Associated with a Chair" is a PCA, not a Chair.
        for case in (
            "PCA of Chair",
            "Spouse of Chairman",
            "Person closely associated with the Chair",
        ):
            self.assertEqual(normalize_role(case), PCA, f"Failed: {case}")

    def test_pca_beats_ceo(self):
        for case in (
            "PCA of CEO",
            "Wife of Chief Executive Officer",
            "Family trust of CEO",
        ):
            self.assertEqual(normalize_role(case), PCA, f"Failed: {case}")

    def test_founder_beats_president(self):
        # "President and Founder" → Founder, not President / VP
        self.assertEqual(normalize_role("President and Founder"), FOUNDER)

    def test_divisional_beats_ceo(self):
        # Regional CEO must NOT pollute the T1 CEO cohort.
        self.assertEqual(
            normalize_role("Chief Executive Officer, North America"),
            DIVISIONAL,
        )

    def test_non_exec_chair_beats_chair(self):
        # Rupert Q4 (broadened 2026-05-21): "Non-Executive Chair"
        # buckets with NEDs, not CHAIR_NON_EXEC. Test name kept for
        # git-history continuity — the precedence point still holds
        # (the non-exec qualifier beats the bare-Chair fallback), the
        # destination bucket has just moved from CHAIR_NON_EXEC to NED.
        self.assertEqual(
            normalize_role("Non-Executive Chair"), NED,
        )

    def test_non_exec_director_not_chair(self):
        # Plain "Non-Executive Director" without "Chair" is NED.
        self.assertEqual(
            normalize_role("Non-Executive Director"), NED,
        )

    def test_bare_director_is_ned(self):
        # UK convention. This is the 90-row gain.
        self.assertEqual(normalize_role("Director"), NED)


class TestNeverRaises(unittest.TestCase):
    """The mapper must never raise on any input."""

    def test_assorted_pathological_inputs(self):
        for case in (
            None, "", "   ", "\n\t",
            "!@#$%^&*()",
            "🎉",
            "x" * 1000,
            "Multiple   spaces",
            "MIXED   case   ROLE",
        ):
            try:
                result = normalize_role(case)
                self.assertIn(result, BUCKETS)
            except Exception as e:
                self.fail(f"normalize_role({case!r}) raised {e!r}")


class TestReturnsOnlyCanonical(unittest.TestCase):
    """Every return value must be one of the 15 canonical bucket strings."""

    def test_sample_from_live_corpus(self):
        # Sample of raw strings from the 2026-05-20 DB inspection.
        live_samples = [
            "Non-Executive Director", "Chief Executive Officer",
            "Chief Financial Officer", "Director", "President and Founder",
            "PDMR", "Chair", "CEO", "Chief Executive",
            "Non-executive Director", "PDMR (Non-executive Director)",
            "Non-Executive Chairman", "Non-Executive Chair",
            "Independent Non-Executive Director", "Executive Director",
            "Executive Chairman", "Chief Executive Officer, North America",
            "Non-executive director", "Chairman", "Chief Commercial Officer",
            "As per 1(a)", "PCA to PDMR",
            "Fund Manager of Artemis UK Future Leaders plc",
            "Chief Technology, Marketing and Data Officer",
            "PDMR (Non-executive Director)", "PDMR - Chief Financial Officer",
            "Group CEO", "Group Chief Financial Officer",
            "CEO, Savills UK & EMEA/ PDMR",
            "Person closely associated with Jack Pailing, Non-Executive Chair",
            "Nature of the transaction",
        ]
        for raw in live_samples:
            result = normalize_role(raw)
            self.assertIn(
                result, BUCKETS,
                f"normalize_role({raw!r}) returned non-canonical {result!r}",
            )


if __name__ == "__main__":
    unittest.main()
