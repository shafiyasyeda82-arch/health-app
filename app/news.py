"""Health news: disease outbreaks, vaccines, India health and WHO news.

Headlines are fetched from a few fixed web addresses (listed in SOURCES below), kept
in a small cache so the page loads quickly, and saved to news_cache.json so something
still shows if the internet is down. Only headlines, dates and links are shown.

To test the sources from the terminal, run:  python -m app.news
"""

import html
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import quote_plus, urlparse

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.database import get_session
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory="templates")

CACHE_FILE = Path(__file__).resolve().parent.parent / "news_cache.json"
CACHE_SECONDS = 30 * 60  # use saved headlines for this long before refreshing
RETRY_SECONDS = 10 * 60  # after a failed refresh, wait this long before trying again
MANUAL_GAP_SECONDS = 60  # the "Try again" button can be used once a minute
TIMEOUT = httpx.Timeout(6.0, connect=4.0)
MAX_BYTES = 3_000_000
ITEMS_PER_CATEGORY = 12
HEADERS = {"User-Agent": "HealthApp-StudentProject/1.0 (news headlines)"}

CATEGORIES = [
    ("outbreaks", "Outbreaks"),
    ("vaccines", "Vaccines"),
    ("india", "India health"),
    ("general", "WHO news"),
]
CATEGORY_KEYS = [key for key, _ in CATEGORIES]
CATEGORY_NOTES = {
    "outbreaks": "Outbreak reports from WHO, plus recent news about outbreaks and health alerts.",
    "vaccines": "News about vaccines and immunisation.",
    "india": "News about health in India, including the Health Ministry, NCDC and ICMR.",
    "general": "The latest news from the World Health Organization.",
}

VACCINE_WORDS = re.compile(
    r"vaccin|immuni[sz]|polio|measles|rubella|diphtheria|tetanus|pertussis|\bHPV\b|rabies|mpox",
    re.IGNORECASE,
)


def google_news(query: str) -> str:
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(query)
        + "&hl=en-IN&gl=IN&ceid=IN:en"
    )


# Every address the app ever contacts is listed here. Nothing a visitor types is
# ever used as an address.
SOURCES = [
    {
        "id": "who_don_api",
        "name": "WHO Disease Outbreak News",
        "type": "who_api",
        "url": "https://www.who.int/api/news/diseaseoutbreaknews"
        "?sf_culture=en&$orderby=PublicationDateAndTime%20desc&$top=15",
        "fallback_url": "https://www.who.int/api/news/diseaseoutbreaknews",
        "categories": ["outbreaks"],
    },
    {
        "id": "who_don_rss",
        "name": "WHO Disease Outbreak News (older feed)",
        "type": "rss",
        "url": "https://www.who.int/feeds/entity/csr/don/en/rss.xml",
        "categories": ["outbreaks"],
        "default_source": "WHO",
    },
    {
        "id": "who_news",
        "name": "WHO news",
        "type": "rss",
        "url": "https://www.who.int/rss-feeds/news-english.xml",
        "categories": ["general", "vaccines"],
        "filters": {"vaccines": VACCINE_WORDS},
        "default_source": "WHO",
    },
    {
        "id": "gn_outbreaks",
        "name": "Google News: outbreaks",
        "type": "rss",
        "url": google_news('disease outbreak OR epidemic OR "health alert" when:14d'),
        "categories": ["outbreaks"],
        "default_source": "News",
        "summary": False,
    },
    {
        "id": "gn_vaccines",
        "name": "Google News: vaccines",
        "type": "rss",
        "url": google_news("vaccine OR vaccination OR immunisation India when:30d"),
        "categories": ["vaccines"],
        "default_source": "News",
        "summary": False,
    },
    {
        "id": "gn_india",
        "name": "Google News: India health",
        "type": "rss",
        "url": google_news("India health ministry OR NCDC OR ICMR when:14d"),
        "categories": ["india"],
        "default_source": "News",
        "summary": False,
    },
]

OFFICIAL_LINKS = [
    ("WHO: Disease Outbreak News", "https://www.who.int/emergencies/disease-outbreak-news"),
    ("WHO: News", "https://www.who.int/news"),
    ("WHO in India", "https://www.who.int/india"),
    ("Ministry of Health and Family Welfare (India)", "https://mohfw.gov.in"),
    ("National Centre for Disease Control (India)", "https://ncdc.mohfw.gov.in"),
    ("Indian Council of Medical Research", "https://www.icmr.gov.in"),
    ("CDC (USA)", "https://www.cdc.gov"),
]


# ---------- Cleaning and reading the feeds ----------


def clean_text(value) -> str:
    """Remove any HTML tags and extra spaces from text that came from the web."""
    text = str(value or "")
    text = re.sub(r"<[^>]*>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"<[^>]*>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def parse_date(value) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    moment = None
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        moment = None
    if moment is None:
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def make_item(title, link, source, published, summary) -> Optional[dict]:
    """Build one headline, or None if it is not usable. Links must be http(s)."""
    title = shorten(clean_text(title), 200)
    link = (link or "").strip()
    parsed = urlparse(link)
    if not title or parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return {
        "title": title,
        "link": link,
        "source": shorten(clean_text(source), 80),
        "published": published.isoformat() if published else "",
        "summary": shorten(clean_text(summary), 280),
    }


def local_name(tag) -> str:
    return tag.split("}", 1)[-1] if isinstance(tag, str) else ""


def child_text(node, name: str) -> str:
    for child in node:
        if local_name(child.tag) == name:
            return "".join(child.itertext())
    return ""


def parse_rss(content: bytes, default_source: str, use_summary: bool) -> List[dict]:
    lowered = content.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("unexpected XML declarations")
    root = ET.fromstring(content)

    items = []
    for node in root.iter():
        kind = local_name(node.tag)
        if kind not in ("item", "entry"):
            continue

        title = child_text(node, "title")
        link = ""
        if kind == "item":
            link = child_text(node, "link").strip()
        else:
            for child in node:
                if local_name(child.tag) == "link" and child.get("href"):
                    link = child.get("href")
                    break

        published = parse_date(
            child_text(node, "pubDate")
            or child_text(node, "updated")
            or child_text(node, "published")
            or child_text(node, "date")
        )
        summary = ""
        if use_summary:
            summary = child_text(node, "description") or child_text(node, "summary")

        source = default_source
        for child in node:
            if local_name(child.tag) == "source" and (child.text or "").strip():
                source = clean_text(child.text)
                break
        # Google News headlines end with " - Publisher"; drop that duplicate.
        suffix = f" - {source}"
        clean_title = clean_text(title)
        if source and clean_title.endswith(suffix):
            title = clean_title[: -len(suffix)]

        item = make_item(title, link, source, published, summary)
        if item:
            items.append(item)
    return items


def who_item_link(row: dict) -> str:
    raw = str(row.get("ItemDefaultUrl") or row.get("UrlName") or "").strip()
    if not raw:
        return ""
    if raw.startswith("http"):
        return raw
    if "disease-outbreak-news" in raw:
        return "https://www.who.int/" + raw.lstrip("/")
    return "https://www.who.int/emergencies/disease-outbreak-news/item/" + raw.lstrip("/")


def parse_who_api(content: bytes) -> List[dict]:
    data = json.loads(content.decode("utf-8", errors="replace"))
    rows = data.get("value") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("unexpected format")

    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        published = parse_date(
            row.get("PublicationDateAndTime")
            or row.get("PublicationDate")
            or row.get("DateCreated")
        )
        item = make_item(
            row.get("Title") or row.get("title"),
            who_item_link(row),
            "WHO",
            published,
            row.get("Summary") or row.get("Overview") or "",
        )
        if item:
            items.append(item)
    return items


def download(client: httpx.Client, url: str) -> bytes:
    response = client.get(url)
    response.raise_for_status()
    content = response.content
    if len(content) > MAX_BYTES:
        raise ValueError("response too large")
    return content


def read_source(client: httpx.Client, source: dict, url: str) -> List[dict]:
    content = download(client, url)
    if source["type"] == "who_api":
        return parse_who_api(content)
    return parse_rss(
        content, source.get("default_source", ""), source.get("summary", True)
    )


def fetch_source(source: dict) -> Tuple[List[dict], Optional[str]]:
    """Fetch one source. Returns (items, None) or ([], reason it failed)."""
    urls = [source["url"]]
    if source.get("fallback_url"):
        urls.append(source["fallback_url"])
    reason = "no items found"
    try:
        with httpx.Client(
            timeout=TIMEOUT, follow_redirects=True, headers=HEADERS
        ) as client:
            for url in urls:
                try:
                    items = read_source(client, source, url)
                except Exception as exc:  # try the next address, then give up
                    reason = f"{type(exc).__name__}: {exc}"[:200]
                    continue
                if items:
                    return items, None
                reason = "no items found"
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"[:200]
    return [], reason


def sort_key(item: dict) -> float:
    try:
        return datetime.fromisoformat(item["published"]).timestamp()
    except ValueError:
        return 0.0


def dedupe_sort(items: List[dict]) -> List[dict]:
    seen = set()
    unique = []
    for item in sorted(items, key=sort_key, reverse=True):
        key = re.sub(r"[^a-z0-9]+", "", item["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def refresh_all() -> dict:
    """Fetch every source at the same time and sort the headlines into categories."""
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        results = list(pool.map(fetch_source, SOURCES))

    per_category = {key: [] for key in CATEGORY_KEYS}
    report = []
    for source, (items, error) in zip(SOURCES, results):
        report.append(
            {
                "name": source["name"],
                "ok": error is None,
                "count": len(items),
                "error": error,
                "categories": source["categories"],
            }
        )
        for category in source["categories"]:
            pattern = source.get("filters", {}).get(category)
            for item in items:
                if pattern and not pattern.search(item["title"] + " " + item["summary"]):
                    continue
                per_category[category].append(item)

    for key in per_category:
        per_category[key] = dedupe_sort(per_category[key])[:ITEMS_PER_CATEGORY]
    return {"categories": per_category, "report": report}


# ---------- Cache (in memory, and in a file for when the internet is down) ----------

_lock = threading.Lock()
_state = {
    "loaded": False,
    "categories": {},
    "fetched_at": 0.0,
    "last_attempt": 0.0,
    "report": [],
}


def load_disk_once() -> None:
    if _state["loaded"]:
        return
    _state["loaded"] = True
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        categories = data.get("categories", {})
        if isinstance(categories, dict):
            _state["categories"] = {
                key: categories[key]
                for key in CATEGORY_KEYS
                if isinstance(categories.get(key), list)
            }
            _state["fetched_at"] = float(data.get("fetched_at", 0.0))
    except (OSError, ValueError, TypeError):
        pass  # no saved copy yet, or it could not be read


def save_disk() -> None:
    try:
        payload = {
            "fetched_at": _state["fetched_at"],
            "categories": _state["categories"],
        }
        temp = CACHE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(CACHE_FILE)
    except OSError:
        pass  # saving is only a convenience


def get_news(force: bool = False) -> dict:
    now = time.time()
    with _lock:
        load_disk_once()
        has_data = any(_state["categories"].values())
        fresh = has_data and now - _state["fetched_at"] < CACHE_SECONDS
        gap = MANUAL_GAP_SECONDS if force else RETRY_SECONDS
        waiting = now - _state["last_attempt"] < gap
        if (force or not fresh) and not waiting:
            _state["last_attempt"] = now
            result = refresh_all()
            _state["report"] = result["report"]
            if any(r["ok"] for r in result["report"]):
                for key, items in result["categories"].items():
                    if items:  # keep older headlines if this refresh found none
                        _state["categories"][key] = items
                _state["fetched_at"] = time.time()
                save_disk()
        return {
            "categories": dict(_state["categories"]),
            "fetched_at": _state["fetched_at"],
            "report": list(_state["report"]),
        }


# ---------- Page ----------


def get_current_user_or_none(
    request: Request, session: Session = Depends(get_session)
) -> Optional[User]:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    return session.get(User, user_id)


def display_items(items: List[dict]) -> List[dict]:
    shown = []
    for item in items:
        date_text = ""
        try:
            date_text = (
                datetime.fromisoformat(item["published"]).astimezone().strftime("%d %b %Y")
            )
        except ValueError:
            pass
        shown.append({**item, "date_text": date_text})
    return shown


@router.get("/news", response_class=HTMLResponse, include_in_schema=False)
def news_page(
    request: Request,
    category: Optional[str] = None,
    user: Optional[User] = Depends(get_current_user_or_none),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)

    key = category if category in CATEGORY_KEYS else CATEGORY_KEYS[0]
    snapshot = get_news()
    items = display_items(snapshot["categories"].get(key, []))

    relevant = [r for r in snapshot["report"] if key in r["categories"]]
    none_loaded = bool(relevant) and not any(r["ok"] for r in relevant)

    updated_text = ""
    if snapshot["fetched_at"]:
        updated_text = datetime.fromtimestamp(snapshot["fetched_at"]).strftime(
            "%d %b %Y, %H:%M"
        )

    return templates.TemplateResponse(
        request,
        "news.html",
        {
            "user": user,
            "category": key,
            "tabs": [
                {"key": k, "label": label, "active": k == key}
                for k, label in CATEGORIES
            ],
            "note": CATEGORY_NOTES[key],
            "items": items,
            "updated_text": updated_text,
            "none_loaded": none_loaded,
            "official_links": OFFICIAL_LINKS,
        },
    )


@router.post("/news/refresh", include_in_schema=False)
def refresh_news(
    category: str = Form("outbreaks"),
    user: Optional[User] = Depends(get_current_user_or_none),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    get_news(force=True)
    key = category if category in CATEGORY_KEYS else CATEGORY_KEYS[0]
    return RedirectResponse(url=f"/news?category={key}", status_code=303)


if __name__ == "__main__":
    # Run from the project folder with:  python -m app.news
    print("Checking each news source...\n")
    outcome = refresh_all()
    for entry in outcome["report"]:
        if entry["ok"]:
            print(f"OK      {entry['name']}: {entry['count']} headlines")
        else:
            print(f"FAILED  {entry['name']}: {entry['error']}")
    print()
    for key, label in CATEGORIES:
        print(f"{label}: {len(outcome['categories'][key])} headlines")