"""Scraper for ponisha.ir using requests + BeautifulSoup.

ponisha returns the listing and detail pages as server-rendered HTML
(MUI / React CSS classes). Plain HTTP scraping works.

Verified page structure (from opening the pages):
- Listing: each card is an <article>. Fields available on the card are only
  id / title / skills (spans after "مهارت ها:") / bid count
  ("پیشنهادها" value in span.value). Budget and publish time are NOT on the card.
- Detail (/project/{ID}): budget sometimes appears as "مبلغ سرمایه گذاری: X تومان"
  (not always). The "زمان باقی‌مانده" value is not in the initial HTML.
"""

import copy
import json as _json
import logging
import re
from datetime import datetime
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from ponishabot.models import Project

logger = logging.getLogger(__name__)

PERSIAN_TO_ASCII = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

# The detail page ends with a swiper carousel of ~10 *other* projects, each
# carrying its own full description and budget sentence. Scraping text from the
# whole page therefore picks up a stranger's numbers: project 760635 was read as
# a 12M budget from an Odoo ERP slide, while the employer's real floor was 45M —
# an 18-day, 15.4M proposal that ponisha rejected on submit.
CAROUSEL_SELECTORS = (".mySwiper", ".swiper-wrapper", ".swiper-slide")


def normalize_digits(text: str) -> str:
    return text.translate(PERSIAN_TO_ASCII) if text else ""


def parse_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"\d[\d,]*", normalize_digits(text))
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _coerce_int(value) -> Optional[int]:
    """Coerce a JSON field to int. Strings ('4500000'), floats and ints pass
    through as int; None / garbage / booleans become None.

    (bool is explicitly rejected: int(True) == 1 would corrupt a budget.)"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "")
        if not value:
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _strip_carousels(soup: BeautifulSoup) -> BeautifulSoup:
    """Return a copy of `soup` with the similar-projects carousel removed.

    The carousel holds ~10 unrelated listings, each with a full description and
    its own "بودجه / مبلغ ... تومان" sentence. Any regex that walks the page text
    can therefore match a stranger's budget. The copy keeps the caller's soup
    intact for the DOM-scoped extractors, which target specific nodes.
    """
    clean = copy.copy(soup)
    for sel in CAROUSEL_SELECTORS:
        for node in clean.select(sel):
            node.decompose()
    return clean


def _page_text(soup: BeautifulSoup) -> str:
    """Whitespace-collapsed page text with the carousel stripped out."""
    return _strip_carousels(soup).get_text(" ", strip=True)


class PonishaScraper:
    def __init__(self, session: requests.Session, base_url: str = "https://ponisha.ir",
                 api_session=None, api_base: str = "https://api.ponisha.ir/api/v1"):
        self.session = session
        self.base_url = base_url
        self.listings_url = f"{base_url}/search/projects"
        self._api_session = api_session
        self.api_base = api_base

    def fetch_listing_page(self, page: int = 1, timeout: int = 25) -> str:
        resp = self.session.get(self.listings_url, params={"page": page}, timeout=timeout)
        if resp.status_code == 403:
            raise RuntimeError("403 — request rejected; check cookies and avoid using the slug")
        resp.raise_for_status()
        return resp.text

    def parse_project_cards(self, html: str) -> List[Project]:
        """Extract project cards from the listing articles."""
        soup = BeautifulSoup(html, "html.parser")
        projects: List[Project] = []
        seen = set()

        for article in soup.find_all("article"):
            link = article.find("a", href=re.compile(r"/project/\d+"))
            if not link:
                continue
            m = re.search(r"/project/(\d+)", link.get("href", ""))
            if not m:
                continue
            pid = m.group(1)
            if pid in seen:
                continue
            seen.add(pid)

            url = f"{self.base_url}/project/{pid}"  # no slug (avoids 403)
            title = link.get_text(strip=True)
            if not title:
                continue

            skills = self._extract_skills(article)
            bid_count = self._extract_bid_count(article)

            projects.append(Project(
                id=pid, title=title, url=url,
                skills=skills, bid_count=bid_count,
            ))

        logger.info(f"{len(projects)} projects found on page")
        return projects

    def fetch_detail(self, project: Project, timeout: int = 25) -> "Project | None":
        """Enrich budget, skills, badges, and description from the detail page.
        Returns None on hard failure (403 / network / HTTP error) so the
        caller can retry next cycle instead of marking the project seen.
        """
        try:
            resp = self.session.get(project.url, timeout=timeout)
            if resp.status_code == 403:
                logger.warning(f"403 for details {project.url}")
                return None
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            # Carousel-free text: see CAROUSEL_SELECTORS. Every text scrape on
            # this page must use this, or the "similar projects" slides at the
            # bottom leak a stranger's budget into this project.
            text = _page_text(soup)

            # __NEXT_DATA__ is the authoritative budget: it is keyed by project
            # id (so a carousel cannot bleed into it) and it is the employer's
            # own amount_min/amount_max. It used to be consulted only when the
            # HTML came up empty — which meant a *wrong* HTML value silently
            # won. On 760635 the HTML said 12M (an Odoo slide's budget) against
            # a real 45M floor, so the price was never raised and ponisha
            # rejected the proposal at submit.
            jmin, jmax = self._budget_from_next_data(resp.text, project.id)
            if jmin is not None:
                project.budget_min, project.budget_max = jmin, jmax
                logger.debug(f"Budget from JSON: {jmin:,}-{jmax:,}")
            else:
                # No JSON budget (listing-style layout, or the project is not
                # in dehydratedState): fall back to the page text.
                bmin, bmax = self._extract_budget(soup)
                project.budget_min, project.budget_max = bmin, bmax
                logger.debug(f"Budget from HTML: {bmin}-{bmax}")
            project.skills = project.skills or self._extract_skills(soup)
            # Fallback: if skills are still empty, try __NEXT_DATA__ JSON
            if not project.skills:
                project.skills = self._extract_skills_from_next_data(resp.text)
            project.skill_count = len(project.skills)
            project.deadline = self._extract_deadline(soup)
            project.description = self._extract_description(soup) or project.description
            project.badges = self._extract_badges(soup)
            budget_from_text = self._extract_budget_from_text(text)
            project.budget_from_text = budget_from_text

            # If HTML budget is missing/unrealistic (<1000 Toman) but text parsing found one, use it
            if budget_from_text:
                lo, hi = budget_from_text
                if lo and lo > 0:
                    if project.budget_min is None or project.budget_min < 1000:
                        project.budget_min = lo
                        project.budget_max = hi or lo
                        logger.debug(f"Budget from text: {lo:,}-{hi:,}")
        except Exception as e:
            logger.warning(f"Error fetching details for {project.id}: {e}")
            return None
        return project

    def fetch_all_pages(self, max_pages: int = 24) -> List[Project]:
        all_projects: List[Project] = []
        for page in range(1, max_pages + 1):
            try:
                html = self.fetch_listing_page(page)
                cards = self.parse_project_cards(html)
                if not cards:
                    break
                all_projects.extend(cards)
            except Exception as e:
                logger.error(f"Error on page {page}: {e}")
                break
        return all_projects

    def fetch_new_projects(self, max_pages: int = 1, timeout: int = 25) -> List[Project]:
        """Fetch projects from __NEXT_DATA__ JSON across pages 1..max_pages.

        Returns projects sorted newest-first, with `approved_at` timestamps
        populated so the monitor can prioritize projects that are
        `priority_window_minutes` old. This is faster than the HTML card
        parser because it extracts the structured data directly — skills,
        bid count and timestamp are all in the JSON, no second request.

        Page 1 errors propagate (the monitor then falls back to HTML cards);
        an error on a later page just truncates the result. Pagination also
        stops as soon as a page yields no unseen project id.
        """
        pages = max(1, int(max_pages or 1))
        all_projects: List[Project] = []
        seen: set = set()
        for page in range(1, pages + 1):
            try:
                resp = self.session.get(self.listings_url, params={"page": page},
                                        timeout=timeout)
                if resp.status_code == 403:
                    raise RuntimeError("403 — request rejected; check cookies")
                resp.raise_for_status()
                batch = self._parse_next_data(resp.text, log=(page == 1))
            except Exception as e:
                if page == 1:
                    raise
                logger.warning(f"Listing page {page} failed ({e}); "
                               f"pagination stopped at page {page - 1}")
                break
            fresh = [p for p in batch if p.id not in seen]
            if not fresh:
                break  # past the last page (empty or repeated results)
            seen.update(p.id for p in fresh)
            all_projects.extend(fresh)

        # Newest first (highest approved_at = newest)
        all_projects.sort(key=lambda x: x.approved_at or 0, reverse=True)
        if all_projects and pages > 1:
            logger.info(f"Fetched {len(all_projects)} projects "
                        f"across up to {pages} page(s)")
        return all_projects

    def _parse_next_data(self, html: str, log: bool = True) -> List[Project]:
        """Extract projects from the Next.js __NEXT_DATA__ script tag."""
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag:
            logger.warning("__NEXT_DATA__ not found; falling back to HTML cards")
            return self.parse_project_cards(html)
        try:
            data = _json.loads(tag.get_text())
            queries = data.get("props", {}).get("pageProps", {}) \
                         .get("dehydratedState", {}).get("queries", [])
        except (_json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"__NEXT_DATA__ parse error: {e}")
            return self.parse_project_cards(html)

        # The search-projects query is the last one with data.data = [...]
        projects_raw = None
        for q in reversed(queries):
            st = q.get("state", {}).get("data")
            if isinstance(st, dict) and isinstance(st.get("data"), list):
                projects_raw = st["data"]
                break
        if not projects_raw:
            logger.warning("__NEXT_DATA__ had no project list; falling back to HTML")
            return self.parse_project_cards(html)

        now_ms = int(datetime.now().timestamp() * 1000)
        projects: List[Project] = []
        for p in projects_raw:
            pid = str(p.get("id", ""))
            if not pid:
                continue
            title = p.get("title", "")
            if not title:
                continue
            slug = p.get("slug", "")
            url = f"{self.base_url}/project/{pid}" if not slug \
                else f"{self.base_url}/project/{pid}/{slug}"

            skills = []
            for s in (p.get("skills") or []):
                t = s.get("title") if isinstance(s, dict) else str(s)
                if t:
                    skills.append(t)

            # Budget from JSON (amount_min / amount_max if available) — coerce
            # to int here. NOTE: never do this via `locals()[attr] = ...`:
            # in CPython writes to locals() inside a function are discarded,
            # so the raw (possibly string) value would pass through.
            budget_min = _coerce_int(p.get("amount_min"))
            budget_max = _coerce_int(p.get("amount_max"))

            approved = p.get("approved_at")  # epoch ms
            bid_count = p.get("project_bids_count")
            if bid_count is not None:
                try:
                    bid_count = int(bid_count)
                except (TypeError, ValueError):
                    bid_count = None

            proj = Project(
                id=pid, title=title, url=url, skills=skills,
                bid_count=bid_count,
                budget_min=budget_min, budget_max=budget_max,
                approved_at=approved,
            )
            projects.append(proj)

        # Newest first (highest approved_at = newest)
        projects.sort(key=lambda x: x.approved_at or 0, reverse=True)
        if projects and log:
            newest = projects[0]
            age = (now_ms - (newest.approved_at or now_ms)) / 1000 / 60
            logger.info(f"Fetched {len(projects)} projects from __NEXT_DATA__ "
                        f"(newest: {age:.0f}min ago)")
        return projects

    def fetch_remaining_proposals(self, api_session=None) -> "int | None":
        """Remaining proposal quota from ponisha's REST API.
        remaining = plan.credit_coin_count - plan.debit_coin_count.
        Returns None when there is no API session OR on read error — callers
        must distinguish (monitor uses 2-strikes on errors when a session exists)."""
        session = api_session
        if session is None:
            session = getattr(self, "_api_session", None)
        if session is None:
            return None
        try:
            resp = session.get(f"{self.api_base}/users/me", timeout=15)
            resp.raise_for_status()
            plan = resp.json()["data"]["plan"]
            remaining = int(plan["credit_coin_count"]) - int(plan["debit_coin_count"])
            logger.info(f"Proposal quota: {remaining} remaining "
                        f"({plan.get('title', '?')})")
            return remaining
        except Exception as e:
            logger.warning(f"Could not read proposal quota: {e}")
            return None

    # ─── Extractors ───
    def _budget_from_next_data(self, html: str,
                               project_id: str = "") -> Tuple[Optional[int], Optional[int]]:
        """The employer's amount_min/amount_max from the detail page's JSON.

        Walks the same dehydratedState path as the skills fallback. Matched on
        id when one is given, because the page also embeds listings for other
        projects and picking the wrong one would attach a stranger's budget.
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
            tag = soup.find("script", id="__NEXT_DATA__")
            if not tag:
                return None, None
            data = _json.loads(tag.get_text())
            queries = (data.get("props", {}).get("pageProps", {})
                       .get("dehydratedState", {}).get("queries", []))
            for q in queries:
                st = q.get("state", {}).get("data")
                if not isinstance(st, dict):
                    continue
                if project_id and str(st.get("id")) != str(project_id):
                    continue
                lo, hi = st.get("amount_min"), st.get("amount_max")
                if lo is None and hi is None:
                    continue
                return (int(lo) if lo else None, int(hi) if hi else None)
        except Exception as e:
            logger.debug(f"Budget from __NEXT_DATA__ failed: {e}")
        return None, None

    def _extract_skills_from_next_data(self, html: str) -> List[str]:
        """Extract skills from the detail page's __NEXT_DATA__ JSON.

        Some projects have skills only as structured data in __NEXT_DATA__,
        not as parseable text on the HTML page. This is the fallback when
        the HTML text parser returns empty skills.
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
            tag = soup.find("script", id="__NEXT_DATA__")
            if not tag:
                return []
            data = _json.loads(tag.get_text())
            queries = data.get("props", {}).get("pageProps", {}) \
                          .get("dehydratedState", {}).get("queries", [])
            for q in queries:
                st = q.get("state", {}).get("data")
                if not isinstance(st, dict):
                    continue
                # Detail page: st IS the project (flat dict with skills key)
                sk = st.get("skills", [])
                if sk and isinstance(sk, list) and isinstance(sk[0], dict):
                    return [s.get("title", "") for s in sk if s.get("title")]
                # Listing page layout: st.data is a list of projects
                items = st.get("data")
                if isinstance(items, list) and items and isinstance(items[0], dict):
                    sk = items[0].get("skills", [])
                    if sk and isinstance(sk, list) and isinstance(sk[0], dict):
                        return [s.get("title", "") for s in sk if s.get("title")]
        except Exception as e:
            logger.debug(f"__NEXT_DATA__ skill extraction failed: {e}")
        return []

    def _extract_skills(self, el) -> List[str]:
        """Takes skills between the 'مهارت ها:' label and the next section."""
        text = el.get_text("\n", strip=True)
        start = text.find("مهارت ها:")
        if start == -1:
            return []
        rest = text[start + len("مهارت ها:"):]
        # the next section that ends the skills block
        for stop in ("فرصت انتخاب", "پیشنهادها", "مشاهده"):
            idx = rest.find(stop)
            if idx != -1:
                rest = rest[:idx]
                break
        skills = [s.strip() for s in rest.replace("مهارت ها:", "").split("\n") if s.strip()]
        # drop possible separators
        skills = [s for s in skills if s not in ("مهارت ها:", "")]
        return skills

    def _extract_bid_count(self, el) -> Optional[int]:
        # <span class="title">پیشنهادها</span><span class="value">18</span>
        title = el.find("span", class_=lambda c: c and "title" in c, string=lambda s: s and "پیشنهاد" in s)
        if title:
            val = title.find_next_sibling("span", class_=lambda c: c and "value" in c)
            if val:
                return parse_int(val.get_text())
        return None

    def _extract_badges(self, el) -> List[str]:
        """Extract project badges like فوری، متمایز، برجسته، حرفه‌ای، سرپرست پروژه، بی‌نهایت."""
        badges = []
        badge_keywords = ["فوری", "متمایز", "برجسته", "حرفه‌ای", "سرپرست پروژه", "بی‌نهایت"]
        text = el.get_text(" ", strip=True)
        for kw in badge_keywords:
            if kw in text:
                badges.append(kw)
        return badges

    def _extract_budget_from_text(self, text: str) -> Tuple[Optional[int], Optional[int]]:
        """Extract budget range from free text description.
        Handles patterns like '۴۰ تا ۵۰ میلیون تومان', '۴۰ تا ۵۰ میلیون', '۵۰ میلیون تومان', etc.
        Returns (min_budget, max_budget) in Toman, or (None, None) if not found."""
        if not text:
            return None, None

        # Pattern 1: Range "از X تا Y میلیون/هزار تومان" or "X تا Y میلیون"
        # Note: (?:تومان)? makes the whole word optional — not `تومان?` which
        # only makes the final ن optional.
        m = re.search(
            r"(?:از\s+)?([\d,]+)\s*تا\s*([\d,]+)\s*(میلیون|میليون|هزار)?\s*(?:تومان)?",
            text)
        if m:
            lo = parse_int(m.group(1))
            hi = parse_int(m.group(2))
            unit = m.group(3) or ""
            if lo is None or hi is None:
                pass
            elif "میلیون" in unit or "میليون" in unit:
                return lo * 1_000_000, hi * 1_000_000
            elif "هزار" in unit:
                return lo * 1_000, hi * 1_000
            else:
                return lo, hi  # assume Toman

        # Pattern 2: Single amount "X میلیون/هزار تومان"
        m = re.search(r"([\d,]+)\s*(میلیون|میليون|هزار)\s*(?:تومان)?", text)
        if m:
            val = parse_int(m.group(1))
            unit = m.group(2) or ""
            if val is None:
                return None, None
            if "میلیون" in unit or "میليون" in unit:
                val = val * 1_000_000
            elif "هزار" in unit:
                val = val * 1_000
            return val, val

        return None, None

    def _extract_budget(self, el) -> Tuple[Optional[int], Optional[int]]:
        # Strip the carousel first: given the whole page this regex matched a
        # slide's "بودجه کل پروژه: ۱۲ میلیون تومان" — another project's budget —
        # and missed the employer's real 45M. Scoped to a card or a detail
        # section the strip is a no-op, so this stays safe for other callers.
        text = _strip_carousels(el).get_text(" ", strip=True)

        def with_unit(num: Optional[int], unit: str) -> Optional[int]:
            if num is None:
                return None
            if "میلیون" in unit:
                return num * 1_000_000
            if "هزار" in unit:
                return num * 1_000
            return num

        # Ranged: "از ۳ تا ۷ میلیون تومان" (unit after the range applies to
        # BOTH ends) or "از ۳,۰۰۰,۰۰۰ تا ۷,۰۰۰,۰۰۰ تومان" (unit-less)
        m = re.search(
            r"از\s*([\d,۰-۹]+)\s*تا\s*([\d,۰-۹]+)\s*(هزار|میلیون)?\s*تومان", text)
        if m:
            unit = m.group(3) or ""
            return (with_unit(parse_int(m.group(1)), unit),
                    with_unit(parse_int(m.group(2)), unit))
        # "مبلغ سرمایه گذاری: ۳۹۰ هزار تومان"
        m = re.search(r"مبلغ[^\d]*([\d,۰-۹]+)\s*(هزار|میلیون)?\s*تومان", text)
        if m:
            v = with_unit(parse_int(m.group(1)), m.group(2) or "")
            return v, v
        # Fallback — the number must be adjacent to the keyword and must carry
        # a unit or تومان.
        #
        # This used to be `(?:بودجه|مبلغ)[^\d]*([\d,۰-۹]+)\s*(هزار|میلیون)?(?:\s*تومان)?`,
        # where [^\d]* could run across the whole page and both the unit and
        # تومان were optional. On a listing containing "مبلغ کل" with no number
        # after it, the regex walked forward and latched onto any later digit —
        # "معرفی حدود ۱۰ پکیج" became a 10 Toman budget, and one project came
        # out as 16 Toman, which then fed the model nonsense.
        m = re.search(
            r"(?:بودجه|مبلغ)[^\d]{0,20}?([\d,۰-۹]+)\s*(هزار|میلیون)\b"
            r"|(?:بودجه|مبلغ)[^\d]{0,20}?([\d,۰-۹]+)\s*تومان",
            text)
        if m:
            num = m.group(1) or m.group(3)
            unit = m.group(2) or ""
            v = with_unit(parse_int(num), unit)
            if v is not None:
                return v, v
        return None, None

    def _extract_deadline(self, el) -> Optional[str]:
        # value is not in the initial HTML; try to find number+unit next to the label
        node = el.find(string=lambda s: s and "زمان باقی‌مانده" in s)
        if node and node.parent:
            nxt = node.parent.find_next(["span", "div"])
            if nxt and nxt.get_text(strip=True):
                return nxt.get_text(strip=True)
        return None

    def _extract_description(self, el) -> Optional[str]:
        """Extract project description from a ponisha.ir detail page.
        ponisha uses a MUI Collapse wrapper for the project description
        text — fetch it from there first, then fall back to generic selectors."""
        # Method 1: MUI Collapse wrapper (most reliable on ponisha)
        collapse = el.find("div", class_=lambda c: c and "MuiCollapse-wrapperInner" in c)
        if collapse:
            parts = []
            for p in collapse.find_all("p"):
                txt = p.get_text(strip=True)
                if txt and len(txt) > 20:
                    parts.append(txt)
            if parts:
                return "\n".join(parts)

        # Method 2: first long <p> before the carousel section
        # Carousel items have class 'swiper-slide' — skip those
        carousel = el.find("div", class_=lambda c: c and "swiper-slide" in str(c))
        for p in el.find_all("p"):
            # stop before carousel section
            if carousel and p.sourceline and carousel.sourceline and p.sourceline > carousel.sourceline:
                break
            txt = p.get_text(strip=True)
            if len(txt) > 80:
                if not any(kw in txt[:40] for kw in [
                    "پیشنهاد ویژه", "فرصت انتخاب", "ترکیبی از قیمت",
                    "کارفرما", "فریلنسرهایی که"
                ]):
                    return txt

        # Method 3: classic selectors
        for sel in ["div.description", "section.description"]:
            node = el.select_one(sel)
            if node and len(node.get_text(strip=True)) > 50:
                return node.get_text(" ", strip=True)
        return None
