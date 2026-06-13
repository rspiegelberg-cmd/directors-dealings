"""DEPRECATED — split into t1a_ceo_founder_buy_v1 + t1b_cfo_buy_v1.

B-025 Phase B (2026-05-20) replaced the combined t1_ceo_cfo_buy signal
with two per-bucket signals so CEO/Founder firings can be measured
separately from CFO firings:

  * t1a_ceo_founder_buy   (T1a — CEO + Founder)
  * t1b_cfo_buy           (T1b — CFO)

If you're reading this because something broke: update your code to
import one or both of the new modules above. The old combined module
no longer fires.

This file is kept on disk for two reasons:
  1. Audit trail — git history shows the deprecation point.
  2. FUSE mount in the dev sandbox can't delete files (Linux side).
     Rupert can delete this file manually from Windows when he wants.

Importing this module raises ImportError to fail-fast any callers
that haven't migrated to the new signal IDs.
"""
from __future__ import annotations

raise ImportError(
    "t1_ceo_cfo_buy_v1 was deprecated by B-025 Phase B (2026-05-20). "
    "Use t1a_ceo_founder_buy_v1 (T1a — CEO + Founder) or "
    "t1b_cfo_buy_v1 (T1b — CFO) instead. "
    "See docs/specs/role-normalization-pass.md.",
)
