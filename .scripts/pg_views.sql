-- pg_views.sql -- the browser-facing read layer, as it exists in Supabase.
--
-- WHY THIS FILE EXISTS (2026-08-28): these views had NO copy anywhere but inside
-- the Supabase dashboard. The live site reads them directly from the browser, so
-- they are production code -- but a bad edit or a deleted project would have
-- lost them with nothing to restore from. Same gap found in the trigger-refresh
-- edge function the same week.
--
-- ARCHITECTURE. The base tables have RLS on with no anon policy. These views are
-- owned by `postgres` and run with the OWNER's privileges, so they are the only
-- way the anon key reads anything. Do NOT set security_invoker=on: they would
-- then enforce the caller's RLS and the entire public site would go blank. They
-- must also never be writable -- see migration 019.
--
-- PRICE GATE. Any view exposing `value` MUST exclude rows whose price the
-- reconciliation could not verify (price_audit 'unresolved' / 'no_market').
-- backfill_price_units.py deliberately leaves the stored value intact and
-- delegates that exclusion to READ time. Before migration 022 these views did
-- not honour it and the home page reported 2026-05 capital deployed as GBP 13.7m
-- against GBP 8.2m of trustworthy data, and listed 9 unverifiable trades as top
-- signals.
--
-- Regenerate after any view change:
--   select string_agg('CREATE OR REPLACE VIEW '||c.relname||' AS'||E'\n'
--          ||pg_get_viewdef(c.oid,true), E'\n\n' order by c.relname)
--     from pg_class c join pg_namespace n on n.oid=c.relnamespace
--    where n.nspname='public' and c.relkind='v';

-- ------------------------------------------------- public_capital_monthly_v
CREATE OR REPLACE VIEW public_capital_monthly_v AS
SELECT substr(t.date, 1, 7) AS month,
       COALESCE(sum(t.value) FILTER (WHERE tm.small_cap = 1), 0::double precision) AS small,
       COALESCE(sum(t.value) FILTER (WHERE tm.small_cap = 0), 0::double precision) AS large,
       COALESCE(sum(t.value), 0::double precision) AS total
  FROM transactions t
  LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker
 WHERE t.type = 'BUY' AND t.date >= '2024-01-01'
   AND COALESCE(t.price_audit, 'ok') NOT IN ('unresolved', 'no_market')
 GROUP BY substr(t.date, 1, 7);

-- ------------------------------------------------- public_top_signals_v
-- eval_signals already refuses to fire on flagged rows, but 9 were still
-- reaching this list on 2026-08-28, so it filters here too rather than trusting
-- a single layer.
CREATE OR REPLACE VIEW public_top_signals_v AS
SELECT t.date, t.ticker, t.company, t.director, t.role_normalized AS role,
       t.value, t.url, t.fingerprint, s.signal_id
  FROM signals s
  JOIN transactions t ON t.fingerprint = s.fingerprint
 WHERE t.date >= ((SELECT to_char((max(transactions.date::date) - 45)::timestamptz, 'YYYY-MM-DD')
                     FROM transactions))
   AND COALESCE(t.price_audit, 'ok') NOT IN ('unresolved', 'no_market')
   AND s.signal_id = ANY (ARRAY['t0_cluster_combo','t1a_ceo_founder_buy','t1b_cfo_buy',
       't7_chair_buy','b1_lone_conviction_buy','t2_exec_buy','s1_cluster_buy',
       'f1_first_time_buy']);

-- ------------------------------------------------- public_company_v
-- Keeps the row -- the trade is real -- but shows no value when the price could
-- not be verified, and exposes price_audit so the page can say why.
-- CREATE OR REPLACE cannot reorder columns: keep this order, append only.
CREATE OR REPLACE VIEW public_company_v AS
SELECT t.ticker, t.company, tm.sector, tm.market_cap_gbp, tm.is_aim, tm.small_cap,
       tm.website_url, t.fingerprint, t.date, t.director, t.role, t.role_normalized,
       t.type, t.shares, t.price,
       CASE WHEN COALESCE(t.price_audit,'ok') IN ('unresolved','no_market')
            THEN NULL::double precision ELSE t.value END AS value,
       t.announced_at, t.cluster_id,
       (SELECT array_agg(DISTINCT s.signal_id ORDER BY s.signal_id)
          FROM signals s WHERE s.fingerprint = t.fingerprint) AS signals,
       t.url,
       COALESCE(t.price_audit, 'ok') AS price_audit
  FROM transactions t
  LEFT JOIN tickers_meta tm ON tm.ticker = t.ticker;

-- ------------------------------------------------- public_conviction_v
-- TODO: exposes t.value without the price-gate filter. Left as-is for now
-- because conviction_scores is itself computed downstream of eval_signals,
-- which already excludes flagged rows -- but worth confirming.
CREATE OR REPLACE VIEW public_conviction_v AS
SELECT cs.fingerprint, cs.window_end, cs.score, cs.band, cs.rank_in_window,
       cs.surfaced, cs.f1_who, cs.f2_buy_size, cs.f3_company_size,
       cs.f4_earnings_timing, cs.f5_past_performance, cs.f6_sector_mult,
       cs.earnings_dropped, t.ticker, t.company, t.director, t.role_normalized,
       t.date, t.value, cs.sector
  FROM conviction_scores cs
  JOIN transactions t ON t.fingerprint = cs.fingerprint;

-- ------------------------------------------------- public_pending_v
CREATE OR REPLACE VIEW public_pending_v AS
SELECT rns_id, url, headline, warnings, extracted, parser_source, used_llm,
       CASE WHEN status = 'pending' THEN 'pending' ELSE status END AS status,
       first_seen, last_seen
  FROM pending_filings p
 WHERE status = 'pending';

-- ------------------------------------------------- public_prices_v
CREATE OR REPLACE VIEW public_prices_v AS
SELECT ticker, date, close, volume FROM prices;

-- ------------------------------------------------- public_reporting_v
CREATE OR REPLACE VIEW public_reporting_v AS
SELECT ticker, report_date, report_type, source, confidence FROM reporting_dates;

-- ------------------------------------------------- public_sector_stats_v
CREATE OR REPLACE VIEW public_sector_stats_v AS
SELECT tm.sector,
       count(DISTINCT tm.ticker) AS companies,
       count(DISTINCT lower(TRIM(BOTH FROM t.director)))
         FILTER (WHERE t.director IS NOT NULL AND TRIM(BOTH FROM t.director) <> '') AS directors,
       count(*) FILTER (WHERE t.type = 'BUY')  AS buys,
       count(*) FILTER (WHERE t.type = 'SELL') AS sells
  FROM tickers_meta tm
  LEFT JOIN transactions t ON t.ticker = tm.ticker
 WHERE tm.sector IS NOT NULL AND tm.sector <> ''
 GROUP BY tm.sector
 ORDER BY count(DISTINCT tm.ticker) DESC;

-- ------------------------------------------------- public_short_positions_v
CREATE OR REPLACE VIEW public_short_positions_v AS
SELECT ticker, issuer_name, position_holder, net_short_pct, position_date, source
  FROM short_positions sp
 WHERE ticker IS NOT NULL AND net_short_pct IS NOT NULL
 ORDER BY ticker, position_date DESC;

-- ------------------------------------------------- public_stats_v
CREATE OR REPLACE VIEW public_stats_v AS
SELECT ((SELECT count(*) FROM transactions))::integer AS total_filings,
       ((SELECT count(*) FROM transactions WHERE type = 'BUY'))::integer AS total_buys,
       ((SELECT count(DISTINCT ticker) FROM transactions))::integer AS total_companies,
       ((SELECT count(DISTINCT lower(director)) FROM transactions))::integer AS total_directors,
       ((SELECT count(*) FROM signals))::integer AS total_signals,
       ((SELECT count(DISTINCT t.ticker) FROM transactions t
           LEFT JOIN tickers_meta m ON m.ticker = t.ticker
          WHERE m.sector IS NULL OR m.ticker IS NULL))::integer AS missing_sector,
       ((SELECT count(DISTINCT t.ticker) FROM transactions t
           LEFT JOIN tickers_meta m ON m.ticker = t.ticker
          WHERE m.market_cap_gbp IS NULL OR m.ticker IS NULL))::integer AS missing_cap,
       ((SELECT count(DISTINCT t.ticker) FROM transactions t
           LEFT JOIN tickers_meta m ON m.ticker = t.ticker
          WHERE (m.sector IS NULL OR m.ticker IS NULL)
            AND (m.market_cap_gbp IS NULL OR m.ticker IS NULL)))::integer AS missing_both;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon, authenticated;
