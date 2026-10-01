"""Project monitor: poll the listing, filter by skills/budget, dedup, and call
the decision layer."""

import logging
import threading
from collections import OrderedDict
from typing import Callable, Dict, List, Set

from ponishabot.models import BidPlan, Profile, Project
from ponishabot.scraper import PonishaScraper
from ponishabot.decision import BidDecision
from ponishabot.notifier import TelegramNotifier

from datetime import datetime

logger = logging.getLogger(__name__)

# Max size for the seen set to prevent memory leak
MAX_SEEN_SIZE = 10000


def _fmt_budget(lo, hi) -> str:
    """Format a budget pair, tolerating None on either side."""
    lo_s = f"{lo:,}" if isinstance(lo, int) else "?"
    hi_s = f"{hi:,}" if isinstance(hi, int) else "?"
    return f"{lo_s}–{hi_s}"


class ProjectMonitor:
    def __init__(
        self,
        scraper: PonishaScraper,
        decision: BidDecision,
        notifier: TelegramNotifier,
        profile: Profile,
        refresh_interval: int = 300,
        max_pages: int = 24,
        dry_run: bool = True,
        min_remaining_proposals: int = 2,
        priority_window_minutes: int = 5,
    ):
        self.scraper = scraper
        self.decision = decision
        self.notifier = notifier
        self.profile = profile
        self.refresh_interval = refresh_interval
        self.max_pages = max_pages
        self.dry_run = dry_run
        self.min_remaining_proposals = min_remaining_proposals
        self.priority_window_minutes = priority_window_minutes
        self.remaining_proposals: "int | None" = None   # last known quota
        self._quota_fail_strikes = 0         # consecutive API quota-read failures
        self.seen: "OrderedDict[tuple, None]" = OrderedDict()  # seen keys (prevents repeats), LRU eviction
        self.bid_log: Set[str] = set()       # ids already bid on
        self._stop_event = threading.Event()
        self.observers: List[Callable[[Dict], None]] = []

    def _add_seen(self, key: tuple) -> None:
        """Add key to seen set with LRU eviction."""
        self.seen[key] = None
        # Move to end (most recent)
        self.seen.move_to_end(key)
        # Evict oldest if over limit
        while len(self.seen) > MAX_SEEN_SIZE:
            self.seen.popitem(last=False)

    def add_observer(self, cb: Callable[[Dict], None]) -> None:
        self.observers.append(cb)

    def emit(self, event: Dict) -> None:
        for cb in self.observers:
            try:
                cb(event)
            except Exception:
                pass  # a broken observer must not stop the loop

    def set_profile(self, skills=None, min_budget=None, max_budget=None) -> None:
        """Live profile update without restart (applied on next scan)."""
        if skills is not None:
            self.profile.skills = list(skills)
        if min_budget is not None:
            self.profile.min_budget = int(min_budget)
        if max_budget is not None:
            self.profile.max_budget = int(max_budget)

    def _filter_by_skills(self, project: Project) -> bool:
        """Does the listing share any skill with the profile?

        An untagged listing passes: ponisha's skill tags are optional and
        employers routinely skip them, so an empty list means "unknown", not
        "no overlap". Returning False here discarded the project before the
        decision engine ever ran — the third such gate found, after the two in
        decision.py.
        """
        if not self.profile.skills:
            return True
        # Drop nulls and blanks: ponisha's array can hold them, and a set of
        # {""} is non-empty yet matches nothing, which read as a real mismatch.
        proj = {(s or "").strip().lower() for s in project.skills} - {""}
        if not proj:
            return True  # untagged — let the decision engine judge it
        my = {(s or "").strip().lower() for s in self.profile.skills} - {""}
        return bool(proj & my)

    def run_once(self) -> List[Project]:
        """One poll + process cycle. Returns newly matched projects.

        Uses `fetch_new_projects()` which extracts __NEXT_DATA__ JSON with
        `approved_at` timestamps. Projects younger than
        `priority_window_minutes` are processed first (before older ones)
        so the bot bids on brand-new listings before the competition.
        """
        now_ms = int(datetime.now().timestamp() * 1000)
        window_ms = self.priority_window_minutes * 60 * 1000

        try:
            all_projects = self.scraper.fetch_new_projects(
                max_pages=self.max_pages)
        except Exception as e:
            logger.warning(f"fetch_new_projects failed ({e}); falling back to HTML cards")
            all_projects = self.scraper.fetch_all_pages(self.max_pages)

        # Partition into brand-new vs older
        new_first: List[Project] = []
        older: List[Project] = []
        for project in all_projects:
            if project.approved_at is not None:
                age_ms = now_ms - project.approved_at
                if age_ms <= window_ms:
                    new_first.append(project)
                else:
                    older.append(project)
            else:
                older.append(project)

        if new_first:
            logger.info(f"PRIORITY: {len(new_first)} project(s) posted in the last "
                        f"{self.priority_window_minutes} minutes — bidding first")
        sorted_projects = new_first + older

        new_matches: List[Project] = []

        for project in sorted_projects:
            # Abort quickly when Stop is pressed mid-cycle (don't finish
            # the whole scan — each project can take 15-30s with AI/Karlancer)
            if self._stop_event.is_set():
                logger.info("Stop requested — aborting remaining projects in this cycle")
                break

            key = project.dedup_key()
            if key in self.seen:
                continue

            # Empty listing skills: enrich from detail before filtering so the
            # project is not skipped forever. Hard detail failure → retry later
            # (do not mark seen).
            fetched = False
            if not project.skills:
                project = self.scraper.fetch_detail(project)
                if project is None:
                    continue
                fetched = True

            if not self._filter_by_skills(project):
                continue  # not marked seen — live skill edits can match it later

            if not fetched:
                project = self.scraper.fetch_detail(project)
                if project is None:
                    continue

            self._add_seen(key)

            # Decision
            plan: BidPlan = self.decision.decide(project, self.profile)
            budget = _fmt_budget(project.budget_min, project.budget_max)

            if not plan.should_bid:
                logger.info(f"Rejected: {project.title[:40]} ({plan.message})")
                continue

            notified = self.notifier.notify_new_project(
                project.title, project.url, budget, project.skills,
                price=plan.price, duration_days=plan.duration_days,
                reason=plan.message)
            if self.dry_run:
                if notified:
                    logger.info(
                        f"DRY_RUN match notify sent: {project.title[:60]} "
                        f"(bid skipped — dry_run=true)")
                else:
                    logger.info(
                        f"DRY_RUN match notify NOT sent: {project.title[:60]} "
                        f"(bid skipped — dry_run=true; see Telegram reason above)")
            # attach payment_steps to the project so the GUI can display them
            project._payment_steps = plan.payment_steps
            self.emit({"type": "new_project", "project": project, "plan": plan,
                       "budget": budget, "price": plan.price,
                       "duration_days": plan.duration_days,
                       "reason": plan.message})
            new_matches.append(project)

            if not self.dry_run and project.id not in self.bid_log:
                # Quota gate: prefer a live API read. Missing API session
                # (cannot decrypt token) is not an error — pass through.
                # Two consecutive API failures with a session → halt.
                has_api = getattr(self.scraper, "_api_session", None) is not None
                remaining = self.scraper.fetch_remaining_proposals()
                self.remaining_proposals = remaining
                if remaining is None:
                    if has_api:
                        self._quota_fail_strikes += 1
                        if self._quota_fail_strikes >= 2:
                            logger.warning(
                                "Quota API failed twice in a row — stopping "
                                "to avoid burning proposals")
                            self.notifier.notify_error(
                                "Quota API unavailable (2 failures) — bot stopped")
                            self.emit({"type": "quota_low", "remaining": None})
                            self.stop()
                            break
                    # no API capability: continue without gate
                else:
                    self._quota_fail_strikes = 0
                    if remaining <= self.min_remaining_proposals:
                        logger.warning(
                            f"Only {remaining} proposals left (threshold "
                            f"{self.min_remaining_proposals}) — stopping the bot")
                        self.notifier.notify_quota_low(remaining)
                        self.emit({"type": "quota_low", "remaining": remaining})
                        self.stop()
                        break
                self._submit_bid(project, plan)

        if not new_matches:
            logger.info("No new matching project found")
        return new_matches

    def _submit_bid(self, project: Project, plan: BidPlan) -> None:
        """Submit a bid — replaced by the real submitter (wired in app.py).
        Inert default: must NOT touch bid_log (that would poison dedup if
        dry_run is toggled while this stub is still attached)."""
        logger.info(f"Bidder not attached — bid skipped for {project.title[:40]}")

    def start(self) -> None:
        """Main loop until stop() is called (or KeyboardInterrupt)."""
        logger.info(f"Monitor started (poll interval: {self.refresh_interval}s, DRY_RUN={self.dry_run})")
        self.emit({"type": "status", "running": True})
        # Read the quota up front so the GUI shows it immediately (both modes)
        self.remaining_proposals = self.scraper.fetch_remaining_proposals()
        if self.remaining_proposals is not None:
            self.emit({"type": "quota_update", "remaining": self.remaining_proposals})
        while not self._stop_event.is_set():
            try:
                matches = self.run_once()
                self.emit({"type": "scan_done", "count": len(matches),
                           "remaining_proposals": self.remaining_proposals})
            except Exception as e:
                logger.error(f"Error in monitor cycle: {e}")
                self.notifier.notify_error(str(e))
            # Interruptible sleep: wakes immediately on stop() (max one interval)
            if self._stop_event.wait(self.refresh_interval):
                break
        self.emit({"type": "status", "running": False})
        logger.info("Monitor stopped")

    def stop(self) -> None:
        """Request stop — the current cycle finishes, then the loop exits."""
        self._stop_event.set()
