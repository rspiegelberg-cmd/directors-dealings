"""B-092 helper — characterise _pending_review.json by announcement provider.

Read-only. No DB, no writes. Lists how many pending filings came from each
Primary Information Provider (the path segment after /announcement/ in the URL,
e.g. rns, bzw, eqs, gnw), and shows sample headlines for the non-RNS ones — so
we can see which newly-discovered providers (TotalEnergies, Next 15, M&G,
Magnum, Ferguson, etc.) need a parser-layout recogniser.

Run:
    python .scripts/scan_pending_providers.py
    python .scripts/scan_pending_providers.py --provider bzw   # drill into one
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

PENDING = Path(__file__).resolve().parent / "_pending_review.json"
_PROV_RE = re.compile(r"/announcement/([a-z0-9]+)/", re.IGNORECASE)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", default=None,
                    help="Show all headlines for one provider segment.")
    ap.add_argument("--limit", type=int, default=5,
                    help="Sample headlines per provider (default 5).")
    args = ap.parse_args()

    data = json.loads(PENDING.read_text(encoding="utf-8"))
    items = data.get("items", data) if isinstance(data, dict) else data
    rows = items.values() if isinstance(items, dict) else items

    by_prov = collections.Counter()
    samples: dict[str, list[str]] = collections.defaultdict(list)
    for it in rows:
        if not isinstance(it, dict):
            continue
        url = it.get("url", "") or ""
        m = _PROV_RE.search(url)
        prov = (m.group(1).lower() if m else "(none)")
        by_prov[prov] += 1
        hl = (it.get("headline") or "").strip()
        if hl and len(samples[prov]) < max(args.limit, 1):
            samples[prov].append(hl)

    total = sum(by_prov.values())
    print(f"pending entries: {total}")
    print("\nby provider segment:")
    for prov, n in by_prov.most_common():
        print(f"  {prov:10} {n}")

    if args.provider:
        p = args.provider.lower()
        print(f"\nall headlines for provider '{p}':")
        for it in rows:
            if not isinstance(it, dict):
                continue
            m = _PROV_RE.search(it.get("url", "") or "")
            if m and m.group(1).lower() == p:
                print("  ", (it.get("headline") or "")[:80], "|", it.get("url", ""))
    else:
        print("\nnon-RNS provider sample headlines (candidates for B-092 layout work):")
        for prov, exs in samples.items():
            if prov in ("rns", "(none)"):
                continue
            print(f"  [{prov}]")
            for e in exs:
                print("     ", e[:78])


if __name__ == "__main__":
    main()
