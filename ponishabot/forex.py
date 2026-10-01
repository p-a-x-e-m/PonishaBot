"""Live USD/Toman (forex) rate provider for Iran-aware pricing.

We use the USDT/Toman pair from a reachable Iranian exchange API (Wallex)
instead of Torob, which bot-blocks (ArCaptcha). The rate is used to apply a
forex uplift to AI-estimated prices so bids track the dollar when the rial
devalues — without blindly copying the (distorted, often-too-low) local
market listings.

If the network is unavailable the provider returns None and callers simply
skip the uplift (fail-open: never block bidding on a rate fetch error).
"""

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_WALLEX_MARKETS = "https://api.wallex.ir/v1/markets"


class ForexProvider:
    def __init__(self, source: str = "wallex", timeout: int = 15, cache_ttl: int = 300):
        self.source = source
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._cached_rate: Optional[int] = None
        self._cache_time: float = 0.0

    def get_usd_toman(self) -> Optional[int]:
        """Return the current USDT→Toman rate, or None on failure.
        Uses in-memory cache with TTL to avoid rate-limiting.
        """
        now = time.time()
        if self._cached_rate is not None and (now - self._cache_time) < self.cache_ttl:
            return self._cached_rate

        if self.source == "wallex":
            rate = self._wallex()
        else:
            logger.warning(f"Unknown forex source: {self.source}")
            rate = None

        if rate is not None:
            self._cached_rate = rate
            self._cache_time = now
        return rate

    def _wallex(self) -> Optional[int]:
        try:
            resp = requests.get(_WALLEX_MARKETS,
                                headers={"User-Agent": "Mozilla/5.0"},
                                timeout=self.timeout)
            resp.raise_for_status()
            syms = resp.json().get("result", {}).get("symbols", {})
            pair = syms.get("USDTTMN") or syms.get("USDTTOMAN")
            if not pair:
                logger.warning("Wallex: USDTTMN pair not found")
                return None
            stats = pair.get("stats", {})
            for key in ("lastPrice", "askPrice", "bidPrice"):
                val = stats.get(key)
                if val:
                    try:
                        return int(float(val))
                    except (TypeError, ValueError):
                        continue
            return None
        except Exception as e:
            logger.warning(f"Forex fetch failed: {e}")
            return None

    @staticmethod
    def uplift_factor(current_rate: int, baseline_rate: int,
                      sensitivity: float = 0.5) -> float:
        """Compute a price multiplier from the dollar move.

        When the dollar rises above the baseline, prices go up; when it falls,
        they go down (never below 1.0 so we never discount below the AI
        estimate on a stronger rial). `sensitivity` is how much of the
        percentage move is passed through (0.5 = half, 1.0 = full).
        """
        if not current_rate or not baseline_rate:
            return 1.0
        move = (current_rate - baseline_rate) / baseline_rate  # e.g. +0.20 = +20%
        factor = 1.0 + move * sensitivity
        return max(1.0, round(factor, 4))
