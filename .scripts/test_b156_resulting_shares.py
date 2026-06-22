"""B-156 (Sprint 61) -- resulting-holding parse tests.

Nine groups per the sprint-61 plan (section 1, step 7):
  1. Five narrative forms (family N1).
  2. Table family T2 with two directors (Wynnstay/Ondo layouts).
  3. Attribution rules (single candidate + single row; surname match;
     ambiguous -> None).
  4. BUY guard (resulting < shares -> None + 'resulting_lt_shares').
  5. MAR-template false-positive fixture (clean_buy_9562545.html -> None;
     'Aggregated volume' never captured).
  6. End-to-end parse_announcement on fixture_8855732.html.
  7. Upsert round-trip with migration 013 (INSERT + COALESCE on update).
  8. backtest HEADER position / length (64 cols incl. Sprint 63 pairs).
  9. holding_pct_increase math incl. NULL cases.

B-166 (Sprint 62) groups appended below: anchor widening, predicate
widening, anchorless families 3/4, and guard/attribution regression on
the new forms. All B-166 wordings are verbatim from real cached filings
(rns ids in each test).

ASCII-only prints. Tempfile DBs only.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import parse_pdmr  # noqa: E402
from parse_pdmr import (  # noqa: E402
    _attach_resulting_shares,
    _extract_resulting_holdings,
    _res_anchorless_candidates,
    _res_narrative_candidates,
    _res_surname_match,
    _res_table_candidates,
    parse_announcement,
)

FIXTURES = HERE / "fixtures"


def _table_html(header_cells, data_rows):
    """Minimal HTML table builder for T2 tests."""
    parts = ["<html><body><table>"]
    parts.append("<tr>" + "".join(f"<td>{c}</td>" for c in header_cells)
                 + "</tr>")
    for row in data_rows:
        parts.append("<tr>" + "".join(f"<td>{c}</td>" for c in row)
                     + "</tr>")
    parts.append("</table></body></html>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Group 1: five narrative forms (family N1)
# ---------------------------------------------------------------------------

class TestNarrativeForms(unittest.TestCase):

    def test_f1_beneficially_interested(self):
        # rns 8855732 wording
        text = ("Following this transaction Rob Thomas is beneficially "
                "interested in 5,629 shares, representing approximately "
                "0.02 per cent of the issued share capital of the Company.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Rob Thomas", 5629)])

    def test_f2_interest_in_the_company_of(self):
        # rns 8861668 wording
        text = ("Following this transaction, Philip Broadley has an "
                "interest in the Company of 53,415 common shares, "
                "representing 0.0219% of the Total Voting Rights.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Philip Broadley", 53415)])

    def test_f2_interest_in_shares(self):
        # rns 8863449 wording
        text = ("Following this transaction, Mark Stejbach has an interest "
                "in 14,924 shares in the Company.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Mark Stejbach", 14924)])

    def test_f3_total_holding_of(self):
        # rns 8863696 wording (all-caps name)
        text = ("Following this transaction the total holding of MS YVONNE "
                "STILLHART is 13,718 Shares")
        self.assertEqual(_res_narrative_candidates(text),
                         [("MS YVONNE STILLHART", 13718)])

    def test_f4_now_holds(self):
        text = ("Following these transactions, Jane McGann now holds "
                "120,500 ordinary shares.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Jane McGann", 120500)])

    def test_f5_possessive_total_holding(self):
        text = ("Following the above transaction, David Smith's total "
                "beneficial holding is 44,000.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("David Smith", 44000)])

    def test_year_value_rejected(self):
        # Bare year-range value (the year-as-shares bug family) -> rejected.
        text = ("Following this transaction John Brown is beneficially "
                "interested in 2025 shares.")
        self.assertEqual(_res_narrative_candidates(text), [])

    def test_comma_formatted_value_in_year_range_kept(self):
        # "2,025" is comma-formatted -> a real (small) holding, not a year.
        text = ("Following this transaction John Brown is beneficially "
                "interested in 2,025 shares.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("John Brown", 2025)])

    def test_no_anchor_no_candidates(self):
        text = "John Brown is beneficially interested in 5,000 shares."
        self.assertEqual(_res_narrative_candidates(text), [])


# ---------------------------------------------------------------------------
# Group 2: table family T2 (two directors)
# ---------------------------------------------------------------------------

class TestTableFamily(unittest.TestCase):

    def test_wynnstay_layout_short_header(self):
        # 8858155 layout: header row omits the name column (4 header cells,
        # 5 data cells) -- located by offset from the END of the row.
        html = _table_html(
            ["Position", "Number of Ordinary Shares acquired",
             "Resulting beneficial interest in Ordinary Shares",
             "% of Company issued share capital"],
            [["Alk Brand", "CEO", "253", "11,253", "0.05%"],
             ["Claire Williams", "Company Secretary", "425", "12,166",
              "0.05%"]],
        )
        self.assertEqual(_res_table_candidates(html),
                         [("Alk Brand", 11253), ("Claire Williams", 12166)])

    def test_ondo_layout_with_footnote_marker(self):
        # 8856771 layout: full header incl. a 'Percentage resulting
        # beneficial holding' twin column that must be skipped, and a
        # footnote marker '(1)' on the first name.
        html = _table_html(
            ["Name", "Title", "Number of Partnership Shares",
             "Number of Matching Shares", "Total resulting beneficial "
             "holding", "Percentage resulting beneficial holding"],
            [["Craig Foster (1)", "Chief Executive Officer", "461", "461",
              "2,450,642", "1.83%"],
             ["Kevin Withington", "Chief Financial Officer", "461", "461",
              "298,714", "0.22%"]],
        )
        self.assertEqual(
            _res_table_candidates(html),
            [("Craig Foster", 2450642), ("Kevin Withington", 298714)],
        )

    def test_percentage_only_table_yields_nothing(self):
        html = _table_html(
            ["Name", "Percentage resulting beneficial holding"],
            [["Craig Foster", "1.83%"]],
        )
        self.assertEqual(_res_table_candidates(html), [])


# ---------------------------------------------------------------------------
# Group 3: attribution rules
# ---------------------------------------------------------------------------

def _mk_row(director, shares=100, tx_type="BUY"):
    return {"director": director, "shares": shares, "type": tx_type}


class TestAttribution(unittest.TestCase):

    def test_single_candidate_single_row_attaches(self):
        rows = [_mk_row("Rob Thomas", shares=2129)]
        html = "<html><body></body></html>"
        text = ("Following this transaction Rob Thomas is beneficially "
                "interested in 5,629 shares.")
        warnings: list = []
        _attach_resulting_shares(rows, html, text, warnings)
        self.assertEqual(rows[0]["resulting_shares"], 5629)
        self.assertEqual(warnings, [])

    def test_single_candidate_single_row_attaches_despite_name_mismatch(self):
        # Plan rule: single candidate + single row -> attach (no name check).
        rows = [_mk_row("R. A. Thomas", shares=2129)]
        text = ("Following this transaction Rob Thomas is beneficially "
                "interested in 5,629 shares.")
        warnings: list = []
        _attach_resulting_shares(rows, "<html></html>", text, warnings)
        self.assertEqual(rows[0]["resulting_shares"], 5629)

    def test_multi_row_requires_surname_match(self):
        rows = [_mk_row("Alk Brand", shares=253),
                _mk_row("Claire Williams", shares=425)]
        html = _table_html(
            ["Position", "Acquired",
             "Resulting beneficial interest in Ordinary Shares", "%"],
            [["Alk Brand", "CEO", "253", "11,253", "0.05%"],
             ["Claire Williams", "Co Sec", "425", "12,166", "0.05%"]],
        )
        warnings: list = []
        _attach_resulting_shares(rows, html, "", warnings)
        self.assertEqual(rows[0]["resulting_shares"], 11253)
        self.assertEqual(rows[1]["resulting_shares"], 12166)

    def test_multi_row_no_match_stays_none(self):
        rows = [_mk_row("Alk Brand"), _mk_row("Somebody Else")]
        html = _table_html(
            ["Position", "Acquired",
             "Resulting beneficial interest in Ordinary Shares", "%"],
            [["Alk Brand", "CEO", "253", "11,253", "0.05%"]],
        )
        warnings: list = []
        _attach_resulting_shares(rows, html, "", warnings)
        self.assertEqual(rows[0]["resulting_shares"], 11253)
        self.assertIsNone(rows[1]["resulting_shares"])

    def test_ambiguous_multiple_values_for_same_surname_stays_none(self):
        rows = [_mk_row("John Smith")]
        text = ("Following this transaction John Smith is beneficially "
                "interested in 5,000 shares. Following this transaction "
                "Jane Smith is beneficially interested in 9,000 shares.")
        warnings: list = []
        _attach_resulting_shares(rows, "<html></html>", text, warnings)
        # Two candidates, one row: surname 'smith' matches both candidates
        # with different values -> ambiguous -> None.
        self.assertIsNone(rows[0]["resulting_shares"])

    def test_surname_match_ignores_titles_and_case(self):
        self.assertTrue(
            _res_surname_match("MS YVONNE STILLHART", "Yvonne Stillhart"))
        self.assertTrue(_res_surname_match("Rob Thomas", "Robert Thomas"))
        self.assertFalse(_res_surname_match("Rob Thomas", "Claire Williams"))
        self.assertFalse(_res_surname_match(None, "Claire Williams"))
        self.assertFalse(_res_surname_match("Rob Thomas", None))


# ---------------------------------------------------------------------------
# Group 4: BUY guard
# ---------------------------------------------------------------------------

class TestBuyGuard(unittest.TestCase):

    def test_buy_resulting_lt_shares_refused_with_warning(self):
        rows = [_mk_row("Rob Thomas", shares=10000, tx_type="BUY")]
        text = ("Following this transaction Rob Thomas is beneficially "
                "interested in 5,629 shares.")
        warnings: list = []
        _attach_resulting_shares(rows, "<html></html>", text, warnings)
        self.assertIsNone(rows[0]["resulting_shares"])
        self.assertIn("resulting_lt_shares", warnings)

    def test_sell_resulting_lt_shares_is_fine(self):
        # A SELL can legitimately leave fewer shares than were sold.
        rows = [_mk_row("Rob Thomas", shares=10000, tx_type="SELL")]
        text = ("Following this transaction Rob Thomas is beneficially "
                "interested in 5,629 shares.")
        warnings: list = []
        _attach_resulting_shares(rows, "<html></html>", text, warnings)
        self.assertEqual(rows[0]["resulting_shares"], 5629)
        self.assertEqual(warnings, [])

    def test_buy_resulting_equal_shares_attaches(self):
        # resulting == shares (first-time holding) passes the >= guard;
        # the derived pct is None downstream (prior == 0).
        rows = [_mk_row("Rob Thomas", shares=5629, tx_type="BUY")]
        text = ("Following this transaction Rob Thomas is beneficially "
                "interested in 5,629 shares.")
        warnings: list = []
        _attach_resulting_shares(rows, "<html></html>", text, warnings)
        self.assertEqual(rows[0]["resulting_shares"], 5629)


# ---------------------------------------------------------------------------
# Group 5: MAR-template false positive (fixture must yield None)
# ---------------------------------------------------------------------------

class TestMarTemplateFixture(unittest.TestCase):

    def test_clean_buy_fixture_yields_none(self):
        path = FIXTURES / "clean_buy_9562545.html"
        self.assertTrue(path.exists(), f"missing fixture: {path}")
        html = path.read_text(encoding="utf-8", errors="replace")
        # Document-level extraction finds NOTHING in the MAR template
        # (no narrative anchor, no resulting-holding table; the
        # 'Aggregated volume' figure is never captured).
        text = parse_pdmr.html_to_text(html)
        self.assertEqual(_extract_resulting_holdings(html, text), [])
        # And any rows the parser emits carry resulting_shares=None.
        rows, _warnings, _src = parse_announcement(
            html, "https://example/9562545", "9562545", "2026-06-01",
        )
        for r in rows:
            self.assertIn("resulting_shares", r)
            self.assertIsNone(r["resulting_shares"])

    def test_aggregated_volume_never_captured(self):
        # Guard: a MAR 'Aggregated volume' figure near a Following-anchor
        # must not be captured as a resulting holding.
        text = ("Following the transactions described below. "
                "Aggregated volume 50,000 shares at an aggregated price.")
        for name, _val in _res_narrative_candidates(text):
            self.fail("Aggregated volume must never be captured")
        html = _table_html(
            ["Aggregated volume", "Price"],
            [["50,000", "1.00"]],
        )
        self.assertEqual(_res_table_candidates(html), [])


# ---------------------------------------------------------------------------
# Group 6: end-to-end parse on the real Wynnstay filing 8855732
# ---------------------------------------------------------------------------

class TestEndToEnd8855732(unittest.TestCase):
    """Real Wynnstay filing (rns 8855732, Rob Thomas BUY 2,129 -> 5,629).

    NOTE: this filing's nested Price/Volume tranche table is not yet
    recognised by the row extractors (pre-existing limitation -- the
    filing is in the unparsed/pending population, confirmed at build
    time 2026-06-10), so parse_announcement emits no rows for it today.
    The B-156 layer is therefore asserted end-to-end on the real HTML:
    document-level extraction + attachment to the filing's transaction.
    If a future parser fix starts emitting rows, the attach call in the
    emission path is already wired and the extraction below stays true.
    """

    def test_fixture_8855732_extracts_resulting_shares(self):
        path = FIXTURES / "fixture_8855732.html"
        self.assertTrue(path.exists(), f"missing fixture: {path}")
        html = path.read_text(encoding="utf-8", errors="replace")
        text = parse_pdmr.html_to_text(html)
        # Document-level extraction on the real filing.
        self.assertEqual(_extract_resulting_holdings(html, text),
                         [("Rob Thomas", 5629)])
        # Attachment against the filing's actual transaction facts.
        rows = [{"director": "Rob Thomas", "shares": 2129, "type": "BUY"}]
        warnings: list = []
        _attach_resulting_shares(rows, html, text, warnings)
        self.assertEqual(rows[0]["resulting_shares"], 5629)
        self.assertEqual(warnings, [])
        # And whatever parse_announcement emits (none today) must carry
        # the key with a consistent value -- never a crash or a gate
        # rejection caused by B-156.
        emitted, _w, _src = parse_announcement(
            html,
            "https://www.investegate.co.uk/announcement/rns/"
            "wynnstay-group--wyn/director-and-pdmr-dealing/8855732",
            "8855732", "2025-05-01T07:01:36",
        )
        for r in emitted:
            self.assertIn("resulting_shares", r)


# ---------------------------------------------------------------------------
# Group 7: upsert round-trip with migration 013
# ---------------------------------------------------------------------------

def _tx_row(fp, resulting=None, shares=2129):
    return {
        "fingerprint": fp, "date": "2025-04-30", "ticker": "WYN",
        "company": "Wynnstay Group plc", "director": "Rob Thomas",
        "role": "Chief Financial Officer", "type": "BUY",
        "shares": shares, "price": 3.22175,
        "value": round(3.22175 * shares, 2), "context": None,
        "url": "https://example/8855732", "announced_at": "2025-05-01",
        "buy_strictness": "STRICT_BUY", "resulting_shares": resulting,
    }


class TestUpsertRoundTrip(unittest.TestCase):

    def test_migration_013_and_upsert_coalesce(self):
        import db as db_mod
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            with mock.patch.object(db_mod, "DB_PATH", db_path):
                conn = db_mod.connect()
                try:
                    # Migration 013 applied: column exists. Chain head moved
                    # to "14" (B-164 014_short_positions), "15"
                    # (B-168 015_director_pay), then "16"
                    # (B-171 016_conviction_scores).
                    cols = [r[1] for r in conn.execute(
                        "PRAGMA table_info(transactions)").fetchall()]
                    self.assertIn("resulting_shares", cols)
                    self.assertEqual(
                        db_mod.get_meta(conn, "schema_version"), "16")

                    # INSERT with a value.
                    inserted = db_mod.upsert_transaction(
                        conn, _tx_row("fp-1", resulting=5629), "regex")
                    conn.commit()
                    self.assertTrue(inserted)
                    got = conn.execute(
                        "SELECT resulting_shares FROM transactions "
                        "WHERE fingerprint = 'fp-1'").fetchone()
                    self.assertEqual(got["resulting_shares"], 5629)

                    # INSERT with None stays NULL...
                    db_mod.upsert_transaction(
                        conn, _tx_row("fp-2", resulting=None), "regex")
                    conn.commit()
                    got = conn.execute(
                        "SELECT resulting_shares FROM transactions "
                        "WHERE fingerprint = 'fp-2'").fetchone()
                    self.assertIsNone(got["resulting_shares"])

                    # ...and a re-seen row backfills via COALESCE.
                    updated = db_mod.upsert_transaction(
                        conn, _tx_row("fp-2", resulting=7000), "regex")
                    conn.commit()
                    self.assertFalse(updated)   # existing-row branch
                    got = conn.execute(
                        "SELECT resulting_shares, seen_count "
                        "FROM transactions WHERE fingerprint = 'fp-2'"
                    ).fetchone()
                    self.assertEqual(got["resulting_shares"], 7000)
                    self.assertEqual(got["seen_count"], 2)

                    # COALESCE never overwrites an existing non-NULL value.
                    db_mod.upsert_transaction(
                        conn, _tx_row("fp-1", resulting=999999), "regex")
                    conn.commit()
                    got = conn.execute(
                        "SELECT resulting_shares FROM transactions "
                        "WHERE fingerprint = 'fp-1'").fetchone()
                    self.assertEqual(got["resulting_shares"], 5629)
                finally:
                    conn.close()


# ---------------------------------------------------------------------------
# Group 8: backtest HEADER position / length
# ---------------------------------------------------------------------------

class TestBacktestHeader(unittest.TestCase):

    def test_header_positions(self):
        import backtest as bt
        # B-164 added short_pct_at_announcement directly before
        # windows_available, shifting the B-156 pair back by 1.
        # B-155, B-159 then B-161 (Sprint 63) each added a pair directly
        # before windows_available, shifting the block back by 2+2+2.
        idx_wa = bt.HEADER.index("windows_available")
        self.assertEqual(bt.HEADER[idx_wa - 1], "days_since_results")
        self.assertEqual(bt.HEADER[idx_wa - 2], "post_results_flag")
        self.assertEqual(bt.HEADER[idx_wa - 3], "net_shares_prior_12m")
        self.assertEqual(bt.HEADER[idx_wa - 4], "seller_reversal_flag")
        self.assertEqual(bt.HEADER[idx_wa - 5], "routine_prior_buy_years")
        self.assertEqual(bt.HEADER[idx_wa - 6], "routine_flag")
        self.assertEqual(bt.HEADER[idx_wa - 7], "short_pct_at_announcement")
        self.assertEqual(bt.HEADER[idx_wa - 8], "holding_pct_increase")
        self.assertEqual(bt.HEADER[idx_wa - 9], "resulting_shares")

    def test_header_length_64(self):
        import backtest as bt
        # 55 (post-B-160) + resulting_shares + holding_pct_increase = 57;
        # + short_pct_at_announcement (B-164) = 58;
        # + routine_flag + routine_prior_buy_years (B-155) = 60;
        # + seller_reversal_flag + net_shares_prior_12m (B-159) = 62;
        # + post_results_flag + days_since_results (B-161) = 64;
        # + 7 B-168 salary-multiple cols (appended after windows_available) = 71.
        self.assertEqual(len(bt.HEADER), 71)

    def test_select_firings_sql_references_columns(self):
        import inspect
        import backtest as bt
        source = inspect.getsource(bt._select_firings)
        self.assertIn("resulting_shares", source)
        self.assertIn("t.shares", source)


# ---------------------------------------------------------------------------
# Group 9: holding_pct_increase math incl. NULL cases
# ---------------------------------------------------------------------------

class TestHoldingPctIncrease(unittest.TestCase):

    def test_normal_buy(self):
        from backtest import _holding_pct_increase
        # 8855732: bought 2,129, resulting 5,629 -> prior 3,500.
        self.assertAlmostEqual(
            _holding_pct_increase("BUY", 2129, 5629), 2129 / 3500)

    def test_null_resulting_is_none(self):
        from backtest import _holding_pct_increase
        self.assertIsNone(_holding_pct_increase("BUY", 2129, None))

    def test_non_buy_is_none(self):
        from backtest import _holding_pct_increase
        self.assertIsNone(_holding_pct_increase("SELL", 2129, 5629))
        self.assertIsNone(_holding_pct_increase("SIP", 2129, 5629))
        self.assertIsNone(_holding_pct_increase(None, 2129, 5629))

    def test_prior_zero_first_time_holding_is_none(self):
        from backtest import _holding_pct_increase
        self.assertIsNone(_holding_pct_increase("BUY", 5629, 5629))

    def test_prior_negative_is_none(self):
        from backtest import _holding_pct_increase
        self.assertIsNone(_holding_pct_increase("BUY", 9000, 5629))

    def test_zero_shares_is_none(self):
        from backtest import _holding_pct_increase
        self.assertIsNone(_holding_pct_increase("BUY", 0, 5629))

    def test_exporter_mirror(self):
        from export_dashboard_json import _holding_pct_increase_tx
        tx = {"type": "BUY", "shares": 2129, "resulting_shares": 5629}
        self.assertAlmostEqual(_holding_pct_increase_tx(tx), 2129 / 3500)
        tx_null = {"type": "BUY", "shares": 2129, "resulting_shares": None}
        self.assertIsNone(_holding_pct_increase_tx(tx_null))
        tx_sell = {"type": "SELL", "shares": 2129, "resulting_shares": 5629}
        self.assertIsNone(_holding_pct_increase_tx(tx_sell))


# ---------------------------------------------------------------------------
# B-166 group A: anchor widening (verbatim wordings from real cached filings)
# ---------------------------------------------------------------------------

class TestB166AnchorWidening(unittest.TestCase):

    def test_following_this_purchase_9079891(self):
        text = ("Following this purchase, Mr Clarke has an interest in "
                "40,000 ordinary shares representing approximately 0.05 per "
                "cent. of the Company's voting share capital.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Mr Clarke", 40000)])

    def test_following_these_trades_9340385(self):
        text = ("Following these trades, Graham Duncan now has a beneficial "
                "interest in 100,250,617 Ordinary Shares, representing "
                "0.30% of the current total voting rights of the Company.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Graham Duncan", 100250617)])

    def test_following_above_acquisition_of_shares_8999734(self):
        text = ("Following the above acquisition of shares, Nick Hewson "
                "holds 1,446,609 Ordinary Shares in the Company.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Nick Hewson", 1446609)])

    def test_following_this_share_purchase_9237980(self):
        text = ("Following this share purchase, the total holdings of "
                "Simon Herrick is 27,539 Ordinary Shares.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Simon Herrick", 27539)])

    def test_following_the_sipp_transfer_9506876(self):
        text = ("Following the SIPP transfer, Louise Adrian's beneficial "
                "holding of Shares is 9,913,794 Shares, representing 2.31 "
                "per cent. of the Company's issued share capital.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Louise Adrian", 9913794)])

    def test_following_this_transfer_8978060(self):
        text = ("Following this transfer, Bob Holt's beneficial interest "
                "in the Company remains at 12,400,000 Ordinary Shares")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Bob Holt", 12400000)])

    def test_following_these_dealings_synthetic(self):
        text = ("Following these dealings, John Brown now holds 50,000 "
                "ordinary shares.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("John Brown", 50000)])


# ---------------------------------------------------------------------------
# B-166 group B: predicate widening (verbatim wordings, valid anchor)
# ---------------------------------------------------------------------------

class TestB166PredicateWidening(unittest.TestCase):

    def test_is_interested_in_without_beneficially_9469145(self):
        text = ("Following this transaction, Peter Hill is interested in "
                "3,448 Ordinary Shares of £1.00 each in Paragon "
                "Banking Group PLC.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Peter Hill", 3448)])

    def test_is_interested_in_without_beneficially_9467814(self):
        text = ("Following this transaction, Graeme Yorston is interested "
                "in 9,600 Ordinary Shares of £1.00 each in Paragon "
                "Banking Group PLC.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Graeme Yorston", 9600)])

    def test_holds_a_beneficial_interest_in_9285232(self):
        text = ("Following these transactions, Richard Woodman holds a "
                "beneficial interest in 508,659 Ordinary Shares of "
                "£1.00 each in the Company.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Richard Woodman", 508659)])

    def test_has_total_beneficial_interest_of_9275706(self):
        # NB bare '9763' is outside the 1990-2099 year window -> kept.
        text = ("Following this transaction, Rita Estevez has a total "
                "beneficial interest in the Company of 9763 Ordinary "
                "Shares, representing approximately 0.00 per cent. of the "
                "Company's issued share capital.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Rita Estevez", 9763)])

    def test_possessive_total_interest_is_now_8931559(self):
        text = ("Following these transactions, Giulio Cesareo's total "
                "interest in the Company is now 4,327,674 Ordinary Shares, "
                "representing 4.14% of the total voting rights of the "
                "Company.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Giulio Cesareo", 4327674)])

    def test_possessive_beneficial_holding_is_now_9508570(self):
        # Judges Scientific wording (also 9560193).
        text = ("Following this transaction Tim Prestidge's beneficial "
                "holding is now 1,248 Ordinary Shares representing "
                "approximately 0.02% of the total issued share capital "
                "and voting rights in the Company.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Tim Prestidge", 1248)])

    def test_possessive_beneficial_holding_stands_at(self):
        # Microlise wording (8955970/9605499 family).
        text = ("Following this transaction, Nick Wightman's beneficial "
                "holding stands at 139,353 Ordinary Shares, representing "
                "0.12% of the total issued share capital of the Company.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Nick Wightman", 139353)])

    def test_holds_a_total_of_8886038(self):
        text = ("Following this transaction, Paula Constant holds a total "
                "of 26,022 Ordinary Shares, representing 0.08% of the "
                "Company's issued ordinary share capital.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Paula Constant", 26022)])

    def test_beneficially_holds_9285232_summary(self):
        text = ("Following this transaction, Mr. Woodman beneficially "
                "holds 508,659 ordinary shares in the Company.")
        self.assertEqual(_res_narrative_candidates(text),
                         [("Mr. Woodman", 508659)])


# ---------------------------------------------------------------------------
# B-166 group C: anchorless families 3 and 4
# ---------------------------------------------------------------------------

class TestB166AnchorlessFamilies(unittest.TestCase):

    def test_f3_increases_named_total_holding_9537141(self):
        text = ("This transaction increases Mr. Fowlston's total holding "
                "to 9,930 ordinary shares.")
        self.assertEqual(_res_anchorless_candidates(text),
                         [("Mr. Fowlston", 9930)])

    def test_f3_increases_named_holding_in_company_9549363(self):
        text = ("This transaction increases Leslie Van De Walle's total "
                "holding in the company to 138,000 ordinary shares.")
        self.assertEqual(_res_anchorless_candidates(text),
                         [("Leslie Van De Walle", 138000)])

    def test_f3_bringing_and_increasing_pronoun_9519058(self):
        # Amigo Resources: two PDMRs, both pronoun-led -> name None each.
        text = ("bringing his total holding to 9,081,979 ordinary shares. "
                "Additionally, Non-Executive Director Qam Jaffri, an "
                "original subscriber to the loan notes, acquired "
                "12,500,000 ordinary shares at £0.003 per share, "
                "increasing his total holding to 50,000,000 ordinary "
                "shares.")
        cands = _res_anchorless_candidates(text)
        self.assertIn((None, 9081979), cands)
        self.assertIn((None, 50000000), cands)

    def test_f4_her_resulting_shareholding_9502995(self):
        text = ("Her resulting shareholding in the Company is 135,000 "
                "representing 0.05% of the Company's issued share capital.")
        self.assertEqual(_res_anchorless_candidates(text),
                         [(None, 135000)])

    def test_f4_his_resulting_shareholding_9553126(self):
        text = ("His resulting shareholding in the Company is 10,655 "
                "Shares representing 0.004% of the Company's issued share "
                "capital.")
        self.assertEqual(_res_anchorless_candidates(text),
                         [(None, 10655)])

    def test_anchorless_flows_into_document_extraction(self):
        text = ("This transaction increases Mr. Fowlston's total holding "
                "to 9,930 ordinary shares.")
        self.assertEqual(
            _extract_resulting_holdings("<html></html>", text),
            [("Mr. Fowlston", 9930)],
        )


# ---------------------------------------------------------------------------
# B-166 group D: guards + attribution unchanged on the new forms
# ---------------------------------------------------------------------------

class TestB166GuardsStillHold(unittest.TestCase):

    def test_percentage_value_never_captured_by_possessive_form(self):
        # _RES_NUM_END: 'holding is now 3.2%' must not yield 3.
        text = ("Following this transaction John Smith's beneficial "
                "holding is now 3.2% of the issued share capital.")
        self.assertEqual(_res_narrative_candidates(text), [])

    def test_year_value_rejected_on_anchorless_form(self):
        text = ("This transaction increases Mr. Brown's total holding to "
                "2025 shares.")
        self.assertEqual(_res_anchorless_candidates(text), [])

    def test_forbidden_context_vetoes_anchorless_form(self):
        text = ("Aggregated volume bringing his total holding to 50,000 "
                "shares.")
        self.assertEqual(_res_anchorless_candidates(text), [])

    def test_pronoun_candidate_never_attaches_to_multiple_rows(self):
        # name=None can only attach via single-candidate + single-row.
        rows = [_mk_row("Nicholas Beal", shares=100),
                _mk_row("Qam Jaffri", shares=100)]
        text = ("This transaction increases his total holding to 50,000 "
                "ordinary shares.")
        warnings: list = []
        _attach_resulting_shares(rows, "<html></html>", text, warnings)
        self.assertIsNone(rows[0]["resulting_shares"])
        self.assertIsNone(rows[1]["resulting_shares"])

    def test_pronoun_candidate_attaches_single_row(self):
        rows = [_mk_row("Jane Doe", shares=10000, tx_type="BUY")]
        text = ("Her resulting shareholding in the Company is 135,000 "
                "representing 0.05% of the Company's issued share capital.")
        warnings: list = []
        _attach_resulting_shares(rows, "<html></html>", text, warnings)
        self.assertEqual(rows[0]["resulting_shares"], 135000)
        self.assertEqual(warnings, [])

    def test_buy_guard_applies_to_anchorless_candidate(self):
        rows = [_mk_row("Jane Doe", shares=200000, tx_type="BUY")]
        text = ("Her resulting shareholding in the Company is 135,000 "
                "representing 0.05% of the Company's issued share capital.")
        warnings: list = []
        _attach_resulting_shares(rows, "<html></html>", text, warnings)
        self.assertIsNone(rows[0]["resulting_shares"])
        self.assertIn("resulting_lt_shares", warnings)

    def test_corporate_subject_never_captured(self):
        # rns 8857922 verbatim: the issuer's treasury count must never be
        # captured as a person's resulting holding (it would attach via
        # the single-candidate + single-row rule).
        text = ("Following the above transfer of treasury stock, the "
                "Company holds 4,772,867 ordinary shares as treasury "
                "shares.")
        self.assertEqual(_res_narrative_candidates(text), [])
        # Anchorless corporate subjects are rejected the same way.
        text2 = ("This transaction increases the Trust's total holding to "
                 "50,000 ordinary shares.")
        self.assertEqual(_res_anchorless_candidates(text2), [])

    def test_entity_with_noncorporate_qualifier_tokens_rejected(self):
        # QA fix: rns 9336969 verbatim. The head noun ('Trust') decides --
        # qualifier tokens ('BOG', 'Group', 'Employee') must not let an
        # entity subject through the corporate guard, or the trust's
        # holding could attach to a PDMR row via single-candidate +
        # single-row once these filings start emitting rows.
        text = ("Following this purchase, the BOG Group Employee Trust now "
                "holds 143,716 shares, representing 0.5% of the issued "
                "share capital of the Company.")
        self.assertEqual(_res_narrative_candidates(text), [])

    def test_window_boundary_never_clips_a_number(self):
        # 9605499 (Altitude): the 300-char window boundary used to land
        # inside "10,069,157", yielding a bogus (name, 1) candidate that
        # made attribution ambiguous. The window now extends to the end
        # of any number it would otherwise cut. Sweep filler lengths so
        # the boundary lands on every position inside the number.
        for filler_len in range(228, 241):
            filler = "x" * filler_len
            text = ("Following the purchase of Ordinary Shares " + filler
                    + " Martin Varley's beneficial holding is 10,069,157 "
                    "Ordinary Shares, representing approximately 13.8% of "
                    "the Company's issued share capital.")
            self.assertEqual(
                _res_narrative_candidates(text),
                [("Martin Varley", 10069157)],
                f"window clip at filler={filler_len}",
            )

    def test_original_anchor_and_forms_unchanged(self):
        # The six B-156 wordings must extract identically post-widening.
        cases = [
            ("Following this transaction Rob Thomas is beneficially "
             "interested in 5,629 shares.", ("Rob Thomas", 5629)),
            ("Following this transaction, Philip Broadley has an interest "
             "in the Company of 53,415 common shares.",
             ("Philip Broadley", 53415)),
            ("Following this transaction, Mark Stejbach has an interest "
             "in 14,924 shares in the Company.", ("Mark Stejbach", 14924)),
            ("Following this transaction the total holding of MS YVONNE "
             "STILLHART is 13,718 Shares", ("MS YVONNE STILLHART", 13718)),
        ]
        for text, expected in cases:
            self.assertEqual(_res_narrative_candidates(text), [expected])


if __name__ == "__main__":
    unittest.main()
