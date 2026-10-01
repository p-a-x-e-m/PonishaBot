"""Data models shared by all ponishabot modules."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Project:
    """A project scraped from ponisha.ir."""
    id: str
    title: str
    url: str                              # https://ponisha.ir/project/{ID} (no slug)
    budget_min: Optional[int] = None
    budget_max: Optional[int] = None
    skills: List[str] = field(default_factory=list)
    employer: Optional[str] = None
    posted_at: Optional[str] = None       # raw Persian posted-time string, if shown
    approved_at: Optional[int] = None     # Unix epoch (ms) from __NEXT_DATA__ — when the project was posted
    deadline: Optional[str] = None        # "time remaining to send proposal"
    bid_count: Optional[int] = None
    suggested_days: Optional[int] = None  # suggested duration (detail page)
    description: Optional[str] = None     # filled only after fetch_detail
    # Competitive signals for AI pricing
    badges: List[str] = field(default_factory=list)        # e.g., ["فوری", "متمایز"]
    budget_from_text: Optional[Tuple[int, int]] = None     # (min, max) parsed from description text
    skill_count: int = 0                                   # number of skills (len(skills))

    def dedup_key(self) -> tuple:
        # id only: JSON listing URLs include a slug, HTML fallback URLs do not;
        # titles can change — id is the only stable key across paths.
        return (self.id,)


@dataclass
class Profile:
    """The user's own skills and constraints (built from config.yaml)."""
    skills: List[str]
    min_budget: int = 0
    max_budget: int = 10_000_000
    name: str = ""         # display name (e.g. "Ali Rezaei")
    about: str = ""        # experience / portfolio / rate notes (fed to AI)
    hourly_min: int = 0    # preferred minimum hourly rate (Toman)
    hourly_max: int = 0    # preferred maximum hourly rate (Toman)


@dataclass
class MarketComparison:
    """Market comparison data from karlancer.com"""
    similar_projects: List[Dict] = field(default_factory=list)
    avg_budget: Optional[int] = None
    min_budget: Optional[int] = None
    max_budget: Optional[int] = None
    num_results: int = 0


@dataclass
class BidPlan:
    """The final decision for one project — output of the decision layer."""
    should_bid: bool
    price: Optional[int] = None           # unit: Toman
    duration_days: Optional[int] = None
    message: str = ""                     # AI's short reasoning (log/Telegram)
    proposal: str = ""                    # full proposal text for the bid form
    payment_steps: List[dict] = field(default_factory=list)  # [{"title", "percent"}]
