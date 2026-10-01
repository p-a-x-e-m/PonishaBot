"""Scraper for karlancer.com — market price comparison.

This scraper searches karlancer.com for projects matching given skills
and extracts budget/price data to help the AI price ponisha.ir bids
competitively. Read-only: never submits anything to karlancer.

URL patterns (from karlancer.com):
  Search:  https://www.karlancer.com/search/?q={skill}
  Project: https://karlancer.com/projects/{slug}-{hash}
"""

import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from ponishabot.models import MarketComparison

logger = logging.getLogger(__name__)

PERSIAN_TO_ASCII = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _normalize_digits(text: str) -> str:
    return text.translate(PERSIAN_TO_ASCII) if text else ""


def _parse_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"[\d,]+", _normalize_digits(text))
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


class KarlancerScraper:
    """Search karlancer.com for market price comparison data."""

    def __init__(self, session: requests.Session,
                 base_url: str = "https://www.karlancer.com"):
        self.session = session
        self.base_url = base_url
        self.search_url = f"{base_url}/search/"

    def fetch_similar_projects(self, skill: str,
                                max_pages: int = 2,
                                timeout: int = 25) -> MarketComparison:
        """Search karlancer.com for projects matching a skill keyword.
        Returns MarketComparison with aggregated pricing data.

        Args:
            skill: Persian or English skill name (e.g. "Django", "طراحی سایت")
            max_pages: max number of search result pages to fetch
            timeout: request timeout in seconds

        Returns:
            MarketComparison with similar_projects list, avg/min/max budget
        """
        all_projects = []
        try:
            for page_num in range(1, max_pages + 1):
                params = {"q": skill, "page": page_num}
                resp = self._get_with_retry(self.search_url, params=params, timeout=timeout)
                if resp is None:
                    break
                if resp.status_code != 200:
                    logger.warning(f"Karlancer search returned {resp.status_code}")
                    break
                html = resp.text
                projects = self._parse_search_results(html)
                if not projects:
                    break
                all_projects.extend(projects)
                logger.info(f"Karlancer page {page_num}: {len(projects)} projects")

                # Rate limiting: be nice to karlancer
                if page_num < max_pages:
                    time.sleep(1)

        except Exception as e:
            logger.warning(f"Karlancer search error: {e}")

        # Calculate market stats
        budgets = [p["budget"] for p in all_projects if p.get("budget")]
        avg_budget = sum(budgets) // len(budgets) if budgets else None
        min_budget = min(budgets) if budgets else None
        max_budget = max(budgets) if budgets else None

        comparison = MarketComparison(
            similar_projects=all_projects[:20],  # limit to 20 for AI prompt
            avg_budget=avg_budget,
            min_budget=min_budget,
            max_budget=max_budget,
            num_results=len(all_projects),
        )

        logger.info(
            f"Karlancer market: {len(all_projects)} projects, "
            f"avg={avg_budget}, min={min_budget}, max={max_budget}")
        return comparison

    def _get_with_retry(self, url: str, params: dict, timeout: int,
                        max_retries: int = 3) -> Optional[requests.Response]:
        """GET with retry on 429/5xx and exponential backoff."""
        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=timeout)
                if resp.status_code == 200:
                    return resp
                if resp.status_code in (429, 500, 502, 503, 504):
                    wait = (2 ** attempt) + (0.5 * attempt)  # 1s, 2.5s, 4.5s
                    logger.warning(f"Karlancer {resp.status_code}, retrying in {wait}s (attempt {attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue
                logger.warning(f"Karlancer returned {resp.status_code}")
                return resp
            except requests.RequestException as e:
                if attempt == max_retries - 1:
                    logger.warning(f"Karlancer request failed after {max_retries} attempts: {e}")
                    return None
                wait = (2 ** attempt)
                logger.warning(f"Karlancer request error: {e}, retrying in {wait}s")
                time.sleep(wait)
        return None

    def _parse_search_results(self, html: str) -> List[Dict]:
        """Parse karlancer.com search results page.
        Returns list of dicts: {title, budget, url, skills, posted_at, employer_rating}"""
        soup = BeautifulSoup(html, "html.parser")
        projects = []

        # Try multiple selectors (karlancer uses MUI/React CSS classes)
        # Selector 1: article tags (like ponisha)
        for article in soup.find_all("article"):
            proj = self._parse_card(article)
            if proj:
                projects.append(proj)

        # Selector 2: div.bg-white cards (common karlancer pattern)
        if not projects:
            for card in soup.find_all("div", class_=re.compile(r"bg-white|project-card|card")):
                proj = self._parse_card(card)
                if proj:
                    projects.append(proj)

        return projects

    def _parse_card(self, el) -> Optional[Dict]:
        """Parse a single project card element."""
        link = el.find("a", href=re.compile(r"/projects?/"))
        if not link:
            return None

        title = link.get_text(strip=True)
        if not title:
            return None

        href = link.get("href", "")
        url = href if href.startswith("http") else f"{self.base_url}{href}"

        # Budget: "بودجه: X تومان" or "X,Y تومان"
        text = el.get_text(" ", strip=True)
        budget = self._extract_budget_from_text(text)

        # Skills
        skills = self._extract_skills(el)

        # Employer rating
        rating = self._extract_rating(el)

        return {
            "title": title[:100],
            "budget": budget,
            "url": url,
            "skills": skills[:5],
            "employer_rating": rating,
        }

    def _extract_budget_from_text(self, text: str) -> Optional[int]:
        """Extract budget from card text. Returns single value (midpoint if range)."""
        if not text:
            return None
        norm = _normalize_digits(text)

        # Pattern 1: Range "X تا Y میلیون/هزار تومان"
        m = re.search(r"([\d,]+)\s*تا\s*([\d,]+)\s*(میلیون|میليون|هزار)?\s*تومان", text)
        if m:
            lo = _parse_int(m.group(1))
            hi = _parse_int(m.group(2))
            unit = m.group(3) or ""
            if "میلیون" in unit or "میليون" in unit:
                return (lo * 1_000_000 + hi * 1_000_000) // 2
            if "هزار" in unit:
                return (lo * 1_000 + hi * 1_000) // 2
            return (lo + hi) // 2

        # Pattern 2: "بودجه: X,Y,Z تومان" or "بودجه X,Y,Z تومان"
        m = re.search(r"بودجه[^\d]*([\d,]+)\s*تومان", text)
        if m:
            return _parse_int(m.group(1))

        # Pattern 3: "X,Y تومان" (simple amount)
        m = re.search(r"([\d,]+)\s*تومان", text)
        if m:
            return _parse_int(m.group(1))

        return None

    def _extract_skills(self, el) -> List[str]:
        """Extract skills from card — similar to ponisha pattern."""
        skills = []
        text = el.get_text("\n", strip=True)

        # Look for "مهارت" label
        for label in ["مهارت‌ها:", "مهارت ها:", "skills:"]:
            idx = text.find(label)
            if idx != -1:
                rest = text[idx + len(label):]
                # skills until next section
                for stop in ["پیشنهاد", "بودجه", "مشاهده", "وضعیت"]:
                    stop_idx = rest.find(stop)
                    if stop_idx != -1:
                        rest = rest[:stop_idx]
                        break
                skills = [s.strip() for s in rest.split("\n") if s.strip()]
                break

        # Also check for skill tags in spans/divs with specific classes
        if not skills:
            for span in el.find_all(["span", "div"],
                                     class_=re.compile(r"skill|tag|badge|chip")):
                txt = span.get_text(strip=True)
                if txt and len(txt) < 50:
                    skills.append(txt)

        return skills

    def _extract_rating(self, el) -> Optional[float]:
        """Extract employer rating if shown (e.g., 'امتیاز کارفرما: 4.8')."""
        text = el.get_text(" ", strip=True)
        m = re.search(r"امتیاز[^\d]*([\d.]+)", text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return None
