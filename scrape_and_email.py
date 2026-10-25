import html as html_lib
import json
import os
import re
import time
import subprocess
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

DATA_FILE = "events.json"
LOG_FILE = "scrape_log.txt"
SITES_FILE = "sites.json"

INPUT_REGION = (os.getenv("INPUT_REGION", "Groningen").strip() or "Groningen")
INPUT_SITES_RAW = (os.getenv("INPUT_SITES", "[]").strip() or "[]")
INPUT_DATE_FROM = os.getenv("INPUT_DATE_FROM", "").strip()
INPUT_DATE_TO = os.getenv("INPUT_DATE_TO", "").strip()
INPUT_CLEAR_ARCHIVE = os.getenv("INPUT_CLEAR_ARCHIVE", "").strip().lower() in {"1", "true", "yes", "ja"}
INPUT_SERPAPI_LINKS_RAW = os.getenv("INPUT_SERPAPI_LINKS", "[]").strip() or "[]"
INPUT_SERPAPI_RAW_LOG = os.getenv("INPUT_SERPAPI_RAW_LOG", "[]").strip() or "[]"
INPUT_SERPAPI_EVENTS_RAW = os.getenv("INPUT_SERPAPI_EVENTS", "[]").strip() or "[]"
INPUT_AI_EVENTS_RAW = os.getenv("INPUT_AI_EVENTS", "[]").strip() or "[]"

REQUEST_HEADERS = {
    # Use a normal browser signature: Groupon and iBOOD reject non-browser scraper user agents.
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def env_int(name, default, minimum, maximum):
    try:
        value = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


MAX_EVENTS_PER_SITE = env_int("INPUT_MAX_EVENTS_PER_SITE", 20, 1, 100)
SITE_TIME_LIMIT_SECONDS = env_int("INPUT_SITE_TIME_LIMIT_SECONDS", 20, 3, 60)
MAX_RAW_ROWS = 1200
TODAY = date.today()

MONTH_NUMBERS = {
    "januari": 1,
    "februari": 2,
    "maart": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "july": 7,
    "juli": 7,
    "augustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mrt": 3,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "okt": 10,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "may": 5,
    "june": 6,
    "august": 8,
    "october": 10,
}

MONTH_PATTERN = "|".join(sorted(map(re.escape, MONTH_NUMBERS), key=len, reverse=True))
WEEKDAY_PATTERN = (
    "maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag|"
    "ma|di|wo|do|vr|za|zo|"
    "monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    "mon|tue|wed|thu|fri|sat|sun"
)

ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](20\d{2}))?\b")
DUTCH_DATE_RE = re.compile(
    rf"\b(?:{WEEKDAY_PATTERN})?\.?\s*(\d{{1,2}})(?:\s*(?:t/m|tot en met|-)\s*\d{{1,2}})?\s+({MONTH_PATTERN})(?:\s+(20\d{{2}}))?\b",
    re.I,
)
MONTH_FIRST_RE = re.compile(rf"\b({MONTH_PATTERN})\s+(\d{{1,2}})(?:,?\s+(20\d{{2}}))?\b", re.I)
TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:.][0-5]\d(?:\s*[-/]\s*([01]?\d|2[0-3])[:.][0-5]\d)?\b")
PRICE_RE = re.compile(r"\b(gratis|free|(?:eur|\u20ac|\?)\s*\d+(?:[,.]\d{1,2})?)\b", re.I)
DATE_BLOCK_START_RE = re.compile(rf"(?=(?:{WEEKDAY_PATTERN})?\.?\s*\d{{1,2}}\s+(?:{MONTH_PATTERN})\b)", re.I)
EVENT_WORDS_RE = re.compile(
    r"\b(event|evenement|agenda|programma|concert|festival|theater|film|bioscoop|markt|workshop|lezing|expo|expositie|tentoonstelling|voorstelling|activiteit|activiteiten|tickets|muziek|cabaret|dans|opera|museum|kermis|kids|familie|cursus|talk|spreekuur|programma)\b",
    re.I,
)
BAD_URL_RE = re.compile(
    r"\b(contact|privacy|cookie|cookies|voorwaarden|login|account|nieuwsbrief|vacature|werken-bij|pers|over-ons|disclaimer|facebook|instagram|linkedin|youtube|x\.com|twitter|winkelwagen|cart)\b",
    re.I,
)
BAD_TITLE_RE = re.compile(
    r"^(menu|home|agenda|programma|filter|datum|soort|locatie|sluiten|zoek|zoeken|tickets?|koop ticket|mijn tickets|meer info|lees meer|bekijk|bekijk volledige programma|toon info|favoriet|voeg toe|image|profiel|privacy|contact|nieuwsbrief)$",
    re.I,
)
STATUS_WORDS_RE = re.compile(
    r"\b(laatste kaarten|uitverkocht|sold out|geannuleerd|cancelled|tickets?|koop ticket|meer info|lees meer|reeds gestart|net bevestigd|extra datum)\b",
    re.I,
)
DESCRIPTION_START_RE = re.compile(
    r"\b(Nieuw-Zeelandse|Praktische|Powerhouse|Instrumentele|Hedendaagse|Introspectieve|Troostrijk|Muzikale|Bekend|Kampioenschap|Grootste|Invloedrijke|Ierse|Pioniers|Verhalen|Monumentale|Indierockband|Winnaars|Sensationele|Britse|Rauwe|Reggaegigant|Wereldberoemd|Amerikaanse|Japanse|IJslandse|Gratis kijkje|Inspirerende|Slimme|Traditionele|Unieke|Messcherpe|Akoestisch|Absolute|Indrukwekkend|Virtuoos|Prachtige|Zangers)\b",
    re.I,
)
STAGE_WORDS_RE = re.compile(r"\b(mainstage|downstage|zienema|dansen|kelderbar|clubkaartshow)\b", re.I)
GENERIC_TITLE_WORDS = {
    "activiteit",
    "activiteiten",
    "agenda",
    "cabaret",
    "concert",
    "cursus",
    "dans",
    "doc",
    "documentaire",
    "drama",
    "event",
    "events",
    "expositie",
    "familie",
    "feest",
    "film",
    "gratis",
    "kids",
    "komedie",
    "locatie",
    "markt",
    "museum",
    "muziek",
    "programma",
    "special",
    "spreekuur",
    "talk",
    "theater",
    "tickets",
    "workshop",
}

RAW_DATA_ROWS = []
SITE_RESULTS = []

DEFAULT_LOCATIONS = {
    "forum.nl": "Forum Groningen",
    "spotgroningen.nl": "SPOT Groningen",
    "vera-groningen.nl": "VERA Groningen",
    "simplon.nl": "Simplon Groningen",
    "martiniplaza.nl": "Martiniplaza Groningen",
    "groningermuseum.nl": "Groninger Museum",
    "visitgroningen.nl": "Groningen",
    "groningen.uitloper.nu": "Groningen",
    "kultuuragenda.nl": "Groningen",
    "noorderzon.nl": "Noorderplantsoen Groningen",
    "concertgebouw.nl": "Concertgebouw Amsterdam",
    "hedon.nl": "Hedon Zwolle",
    "hedon-zwolle.nl": "Hedon Zwolle",
    "zwolsetheaters.nl": "Hedon Zwolle",
    "paradiso.nl": "Paradiso Amsterdam",
    "vvvameland.nl": "Ameland",
    "biosagenda.nl": "Groningen",
}

COMMON_PATHS_FOR_ROOT_SITES = [
    "/agenda",
    "/nl/agenda",
    "/programma",
    "/program",
    "/events",
    "/evenementen",
    "/activiteiten",
    "/activiteiten-en-evenementen",
    "/concerten-en-tickets",
    "/en/concerts-and-tickets",
]


def clean_text(value):
    text = html_lib.unescape(str(value or ""))
    text = (
        text.replace("\u00c3\u00a2\u00c2\u0082\u00c2\u00ac", "\u20ac")
        .replace("\u00e2\u0082\u00ac", "\u20ac")
        .replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compact(value, limit=300):
    text = clean_text(value)
    return text[:limit].rstrip()


def split_lines(value):
    if hasattr(value, "get_text"):
        raw = value.get_text("\n", strip=True)
    else:
        raw = str(value or "")
    lines = [clean_text(line) for line in re.split(r"[\r\n]+", raw)]
    return [line for line in lines if line]


def parse_json_list(value):
    try:
        data = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def normalized_url(value, base=None):
    raw = clean_text(value)
    if not raw or raw == "#":
        return ""
    if not re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I) and re.match(r"^[\w.-]+\.[a-z]{2,}", raw, re.I):
        raw = f"https://{raw}"
    try:
        target = urljoin(base or "", raw)
        target, _ = urldefrag(target)
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"} or "." not in parsed.netloc:
            return ""
        return parsed.geturl()
    except Exception:
        return ""


def canonical_host(value):
    url = normalized_url(value)
    if not url:
        return ""
    return urlparse(url).hostname.lower().removeprefix("www.")


def same_host(candidate, site_url):
    return bool(canonical_host(candidate) and canonical_host(candidate) == canonical_host(site_url))


def host_default_location(site_url):
    host = canonical_host(site_url)
    for known, location in DEFAULT_LOCATIONS.items():
        if host == known or host.endswith(f".{known}") or known.endswith(host):
            return location
    return INPUT_REGION or "Groningen"


def detect_scraper_type(site_url):
    """Choose the preferred scraper strategy for a website."""
    host = canonical_host(site_url)
    if host == "vera-groningen.nl":
        return "programma-kaarten"
    if host == "groningen.uitloper.nu":
        return "uitloper-agenda"
    if host == "martiniplaza.nl":
        return "agenda-kaarten"
    if host == "spotgroningen.nl":
        return "universeel-met-kaartfallback"
    if host == "simplon.nl":
        return "compacte-agenda-kaarten"
    if host == "forum.nl":
        return "universeel-eventkaarten"
    if host == "zwolsetheaters.nl":
        return "JSON-LD + detailpagina-crawler"
    if host == "visitgroningen.nl":
        return "Visit Groningen agenda-/routekaarten"
    if host == "groningermuseum.nl":
        return "universele tentoonstellings- en activiteitenparser"
    if host == "kultuuragenda.nl":
        return "universele detailpagina-parser (Kultuuragenda)"
    if host == "noorderzon.nl":
        return "universele programma- en detailpagina-parser"
    if host == "biosagenda.nl":
        return "bioscoopfilms en speeldata (BiosAgenda Groningen)"
    if host == "socialdeal.nl":
        return "Social Deal-aanbiedingen"
    if host in {"groupon.nl", "ibood.com"}:
        return "aanbiedingen"
    return "universeel-jsonld-links-kaarten"


def source_label(url):
    host = canonical_host(url)
    return host or "website"


def add_raw(source, site, title, event_date="", url="", status="", raw_text=""):
    if len(RAW_DATA_ROWS) >= MAX_RAW_ROWS:
        return
    RAW_DATA_ROWS.append(
        {
            "source": clean_text(source or "Website"),
            "site": clean_text(site),
            "title": compact(title, 180),
            "date": clean_text(event_date),
            "url": normalized_url(url) or clean_text(url),
            "status": compact(status, 80),
            "rawText": compact(raw_text, 420),
        }
    )


def parse_reference_date():
    match = ISO_DATE_RE.search(INPUT_DATE_FROM)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass
    return TODAY


REFERENCE_DATE = parse_reference_date()


def iso_from_parts(day_value, month_value, year_value=None):
    try:
        day = int(day_value)
        month = int(month_value)
        year = int(year_value) if year_value else REFERENCE_DATE.year
        found = date(year, month, day)
    except (TypeError, ValueError):
        return ""

    if not year_value:
        lower_bound = REFERENCE_DATE - timedelta(days=14)
        if found < lower_bound:
            try:
                found = date(year + 1, month, day)
            except ValueError:
                return ""
    return found.isoformat()


def parse_date_text(value, context_date=""):
    text = clean_text(value)
    if context_date and not text:
        return context_date

    lower = text.lower()
    if re.search(r"\bvandaag\b|\btoday\b", lower):
        return TODAY.isoformat()
    if re.search(r"\bmorgen\b|\btomorrow\b", lower):
        return (TODAY + timedelta(days=1)).isoformat()
    if re.search(r"\bovermorgen\b", lower):
        return (TODAY + timedelta(days=2)).isoformat()

    match = ISO_DATE_RE.search(text)
    if match:
        return iso_from_parts(match.group(3), match.group(2), match.group(1))

    match = DUTCH_DATE_RE.search(text)
    if match:
        month = MONTH_NUMBERS.get(match.group(2).lower())
        if month:
            return iso_from_parts(match.group(1), month, match.group(3))

    match = MONTH_FIRST_RE.search(text)
    if match:
        month = MONTH_NUMBERS.get(match.group(1).lower())
        if month:
            return iso_from_parts(match.group(2), month, match.group(3))

    match = NUMERIC_DATE_RE.search(text)
    if match:
        first = int(match.group(1))
        second = int(match.group(2))
        if second <= 12:
            return iso_from_parts(first, second, match.group(3))

    return context_date or ""


def strip_date_prefix(value):
    text = clean_text(value)
    text = re.sub(rf"^(?:{WEEKDAY_PATTERN})?\.?\s*\d{{1,2}}\s+({MONTH_PATTERN})(?:\s+20\d{{2}})?\s*", "", text, flags=re.I)
    text = re.sub(r"^(vandaag|morgen|overmorgen|today|tomorrow)\s*", "", text, flags=re.I)
    return clean_text(text)


def clean_title(value):
    title = clean_text(value)
    title = re.sub(r"\s*\|\s*.*$", "", title)
    title = re.sub(r"\s+-\s+(Forum|SPOT Groningen|VERA Groningen|Paradiso|Hedon|Concertgebouw).*$", "", title, flags=re.I)
    title = STATUS_WORDS_RE.sub(" ", title)
    title = re.sub(r"\s+", " ", title).strip(" -|")
    title = re.sub(r"\b(CAN|USA|UK|GB|NL|BEL|DE|FR|IT|ES|INT)\b$", "", title).strip()
    return title


def title_is_usable(title):
    title = clean_title(title)
    if title.lower() in GENERIC_TITLE_WORDS:
        return False
    if len(title) < 3 or len(title) > 140:
        return False
    if BAD_TITLE_RE.match(title):
        return False
    if re.fullmatch(r"\d{1,2}[:.]\d{2}.*", title):
        return False
    if parse_date_text(title) and len(title.split()) <= 4:
        return False
    return True


TYPE_MARKERS = [
    "Multigenre",
    "Muziek",
    "Theater",
    "Dans",
    "Film",
    "Cabaret",
    "Klassiek",
    "Pop/rock",
    "Roots/americana",
    "Jazz",
    "Blues",
    "Hiphop",
    "Opera",
    "Familie",
    "Circus",
    "Kleinkunst",
    "Stand-up",
    "Talk",
    "Workshop",
    "Cursus",
    "Expositie",
    "Spreekuur",
    "Doc",
    "Kids",
]


def title_from_dated_text(value):
    text = clean_text(value)
    if not parse_date_text(text):
        return ""
    tail = strip_date_prefix(text)
    tail = re.split(r"\b(Mainstage|Downstage|Zienema|Dansen|Ticket|doors|start|Koop ticket|Sold out)\b", tail, flags=re.I)[0]
    tail = STATUS_WORDS_RE.split(tail)[0]
    for marker in TYPE_MARKERS:
        pattern = re.compile(rf"\s+{re.escape(marker)}\b", re.I)
        match = pattern.search(tail)
        if match and len(tail[: match.start()].split()) >= 2:
            tail = tail[: match.start()]
            break
    description_start = DESCRIPTION_START_RE.search(tail)
    if description_start and len(tail[: description_start.start()].split()) >= 2:
        tail = tail[: description_start.start()]
    if len(tail) > 95:
        words = tail.split()
        tail = " ".join(words[: min(7, len(words))])
    return clean_title(tail)


def classify_type(text, host=""):
    lower = clean_text(text).lower()
    if any(domain in (host + " " + lower) for domain in ("socialdeal.nl", "groupon.nl", "ibood.com")):
        return "Aanbiedingen"
    if "zienema" in lower or re.search(r"\bfilm|bioscoop|movie|cinema\b", lower):
        return "Film"
    if re.search(r"\bdoc|documentaire\b", lower):
        return "Documentaire"
    if re.search(r"\bmainstage|downstage|concert|muziek|music|pop|rock|jazz|blues|metal|klassiek|opera|orkest|band\b", lower):
        return "Concert"
    if re.search(r"\btheater|toneel|cabaret|musical|kleinkunst|stand-up\b", lower):
        return "Theater"
    if re.search(r"\bfestival|fest\b", lower):
        return "Festival"
    if re.search(r"\bdans|dance|feest|club\b", lower):
        return "Dans"
    if re.search(r"\bexpo|expositie|tentoonstelling|museum\b", lower):
        return "Expositie"
    if re.search(r"\bworkshop|cursus|training\b", lower):
        return "Workshop"
    if re.search(r"\btalk|lezing|college\b", lower):
        return "Lezing"
    if re.search(r"\bkind|kids|familie|jeugd\b", lower):
        return "Familie"
    if re.search(r"\bspreekuur|inloop\b", lower):
        return "Spreekuur"
    if "groningermuseum.nl" in host:
        return "Expositie"
    return "Activiteit"


def first_time(value):
    match = TIME_RE.search(clean_text(value))
    return match.group(0).replace(".", ":") if match else ""


def first_price(value):
    match = PRICE_RE.search(clean_text(value))
    if not match:
        return ""
    price = clean_text(match.group(1)).replace("EUR", "\u20ac").replace("eur", "\u20ac").replace("?", "\u20ac")
    price = re.sub(r"\u20ac\s*(?=\d)", "\u20ac ", price)
    return price[:40]


def meta_content(soup, *names):
    for name in names:
        selector = f'meta[property="{name}"], meta[name="{name}"]'
        tag = soup.select_one(selector)
        if tag and tag.get("content"):
            return clean_text(tag.get("content"))
    return ""


def first_image(soup, base_url=""):
    for value in [
        meta_content(soup, "og:image", "twitter:image"),
        *(img.get("src") or img.get("data-src") or "" for img in soup.find_all("img", limit=6)),
    ]:
        url = normalized_url(value, base_url)
        if url:
            return url
    return ""


def jsonld_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from jsonld_nodes(item)
    elif isinstance(value, dict):
        yield value
        for key in ("@graph", "graph", "itemListElement", "item", "mainEntity", "about", "workPerformed", "subEvent"):
            if key in value:
                yield from jsonld_nodes(value[key])


def jsonld_type_is_event(item):
    value = item.get("@type") or item.get("type") or ""
    if isinstance(value, list):
        return any("event" == str(part).lower().split("/")[-1] for part in value)
    return "event" == str(value).lower().split("/")[-1]


def value_text(value):
    if isinstance(value, list):
        return clean_text(" ".join(value_text(part) for part in value if part))
    if isinstance(value, dict):
        return clean_text(value.get("name") or value.get("text") or value.get("description") or "")
    return clean_text(value)


def location_from_jsonld(value, default_location):
    if isinstance(value, list):
        return next((location_from_jsonld(item, "") for item in value if location_from_jsonld(item, "")), default_location)
    if isinstance(value, dict):
        name = clean_text(value.get("name"))
        address = value.get("address")
        if isinstance(address, dict):
            city = clean_text(address.get("addressLocality"))
            street = clean_text(address.get("streetAddress"))
            return clean_text(name or street or city or default_location)
        return name or default_location
    return clean_text(value) or default_location


def image_from_jsonld(value, base_url):
    if isinstance(value, list):
        for item in value:
            result = image_from_jsonld(item, base_url)
            if result:
                return result
    if isinstance(value, dict):
        return normalized_url(value.get("url") or value.get("contentUrl"), base_url)
    return normalized_url(value, base_url)


def event_from_jsonld(item, page_url, site_url, discovery_source):
    default_location = host_default_location(site_url)
    event_date = parse_date_text(item.get("startDate") or item.get("doorTime") or item.get("endDate") or "")
    website = normalized_url(item.get("url") or item.get("@id") or page_url, page_url)
    text_blob = " ".join(
        value_text(item.get(key)) for key in ("name", "description", "genre", "keywords", "eventAttendanceMode") if item.get(key)
    )
    raw = {
        "date": event_date,
        "title": item.get("name") or "",
        "type": classify_type(text_blob, canonical_host(site_url)),
        "location": location_from_jsonld(item.get("location"), default_location),
        "website": website,
        "description": item.get("description") or "",
        "image": image_from_jsonld(item.get("image"), page_url),
        "time": first_time(str(item.get("startDate") or "")),
        "cost": "",
        "discoverySource": discovery_source,
        "siteUrl": site_url,
    }
    offers = item.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if isinstance(offers, dict):
        price = offers.get("price")
        currency = offers.get("priceCurrency") or ""
        raw["cost"] = clean_text(f"{currency} {price}") if price is not None else ""
    return normalize_event(raw, site_url)


def parse_jsonld_events(soup, page_url, site_url, discovery_source):
    events = []
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        text = script.string or script.get_text(" ", strip=True)
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        for node in jsonld_nodes(data):
            if jsonld_type_is_event(node):
                event = event_from_jsonld(node, page_url, site_url, discovery_source)
                if event:
                    events.append(event)
    return events


def page_title(soup):
    for selector in ("h1", "[itemprop='name']", "title"):
        tag = soup.select_one(selector)
        if tag:
            title = clean_title(tag.get_text(" ", strip=True))
            if title_is_usable(title):
                return title
    return ""


def node_title(node):
    for selector in (
        "[itemprop='name']",
        "[class*='title']",
        "[class*='titel']",
        "[class*='name']",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    ):
        tag = node.select_one(selector) if hasattr(node, "select_one") else None
        if tag:
            title = clean_title(tag.get("content") or tag.get_text(" ", strip=True))
            if title_is_usable(title):
                return title
    title = clean_title(node.get("aria-label") or node.get("title") or "")
    if title_is_usable(title):
        return title
    dated = title_from_dated_text(node.get_text(" ", strip=True) if hasattr(node, "get_text") else "")
    if title_is_usable(dated):
        return dated
    for line in split_lines(node):
        candidate = clean_title(strip_date_prefix(line))
        if title_is_usable(candidate) and not PRICE_RE.search(candidate) and not TIME_RE.fullmatch(candidate):
            return candidate
    return ""


def best_link_for_node(node, page_url, site_url):
    links = []
    if getattr(node, "name", "") == "a" and node.get("href"):
        links.append(node)
    if hasattr(node, "find_all"):
        links.extend(node.find_all("a", href=True))
    candidates = []
    host = canonical_host(site_url)
    is_vera = "vera-groningen.nl" in host
    for anchor in links:
        url = normalized_url(anchor.get("href"), page_url)
        if not url or not same_host(url, site_url) or BAD_URL_RE.search(url):
            continue
        path = urlparse(url).path
        # Prefer actual event detail pages over general overview pages.
        if is_vera and re.search(r"/events/[^/?#]+", path, re.I):
            return url
        if "kultuuragenda.nl" in host and re.search(r"/agenda/\d+/[^/?#]+", url, re.I):
            return url
        candidates.append(url)
    if not candidates:
        return normalized_url(page_url)
    detail_pattern = re.compile(r"/(?:events?|agenda|concerten?|programma|voorstellingen?|activiteiten?|event-details|show|shows|tickets?)/[^/?#]+", re.I)
    detailed = [url for url in candidates if detail_pattern.search(urlparse(url).path)]
    if detailed:
        # Prefer the deepest detail URL, but keep stable ordering for ties.
        return max(detailed, key=lambda url: len([part for part in urlparse(url).path.split("/") if part]))
    non_root = [url for url in candidates if urlparse(url).path.strip("/")]
    return non_root[0] if non_root else candidates[0]


def line_after_date_location(line):
    text = clean_text(line)
    if "," in text:
        after = clean_text(text.split(",", 1)[1])
        if after and len(after) < 80 and not PRICE_RE.search(after):
            return after
    return ""


def location_from_lines(lines, default_location):
    for line in lines:
        if re.search(r"\b(locatie|zaal|kamer|bibliotheek|forum groningen|oosterpoort|stadsschouwburg|vera|simplon|hedon|paradiso|martiniplaza|concertgebouw|ameland)\b", line, re.I):
            cleaned = clean_text(re.sub(r"^(locatie|zaal)\s*:?\s*", "", line, flags=re.I))
            if len(cleaned) <= 90 and not BAD_TITLE_RE.match(cleaned):
                return cleaned
    for line in lines:
        possible = line_after_date_location(line)
        if possible:
            return possible
    return default_location


def description_from_lines(lines, title):
    parts = []
    title_norm = clean_text(title).lower()
    for line in lines:
        lower = line.lower()
        if lower == title_norm or parse_date_text(line) or BAD_TITLE_RE.match(line):
            continue
        if PRICE_RE.search(line) or TIME_RE.fullmatch(line):
            continue
        if len(line) < 18:
            continue
        parts.append(line)
        if len(" ".join(parts)) > 260:
            break
    return compact(" ".join(parts), 320)


def description_after_title(chunk, title):
    text = strip_date_prefix(chunk)
    title_text = clean_text(title)
    if title_text and text.lower().startswith(title_text.lower()):
        text = text[len(title_text) :]
    text = STATUS_WORDS_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -|")
    return compact(text, 320)


def normalize_event(raw, site_url):
    website = normalized_url(raw.get("website"), site_url)
    title = clean_title(raw.get("title") or "")
    event_date = parse_date_text(raw.get("date") or raw.get("dateText") or "")
    if not event_date and raw.get("contextDate"):
        event_date = parse_date_text("", raw.get("contextDate"))
    if not title_is_usable(title) or not event_date or not website:
        return None

    raw_location = clean_text(raw.get("location") or "")
    location_unknown = not raw_location or re.fullmatch(r"(?:locatie\s+)?(?:onbekend|unknown|n/?a|-)", raw_location, re.I)
    if canonical_host(site_url).endswith("paradiso.nl"):
        location = "Amsterdam"
    elif "concertgebouw.nl" in canonical_host(site_url) and location_unknown:
        location = "Concertgebouw Amsterdam"
    elif (canonical_host(site_url).endswith("hedon.nl") or canonical_host(site_url).endswith("zwolsetheaters.nl")) and location_unknown:
        location = "Hedon Zwolle"
    elif "vera-groningen.nl" in canonical_host(site_url):
        # Always show the venue's standard location, even if page metadata names a room/stage.
        location = "VERA Groningen"
    else:
        location = raw_location or host_default_location(site_url)
    event_type = classify_type(" ".join([raw.get("type", ""), title, raw.get("description", ""), location]), canonical_host(site_url))
    discovery_source = clean_text(raw.get("discoverySource") or "Website")
    description = compact(raw.get("description") or "", 420)
    image = normalized_url(raw.get("image") or "", website)
    cost = first_price(raw.get("cost") or raw.get("description") or "")
    time_value = first_time(raw.get("time") or raw.get("date") or raw.get("description") or "")

    return {
        "date": event_date,
        "time": time_value,
        "title": title,
        "type": event_type[:1].upper() + event_type[1:],
        "location": location,
        "locationLink": "",
        "website": website,
        "source": source_label(website),
        "discoverySource": discovery_source,
        "description": description or f"Meer informatie staat op {source_label(website)}.",
        "cost": cost,
        "image": image,
    }


def event_key(event):
    title = clean_text(event.get("title", "")).lower()
    title = re.sub(r"[^a-z0-9]+", " ", title).strip()
    return f"{event.get('date', '')}|{title}"


def event_delete_key(event):
    website = normalized_url(event.get("website", "")).lower().rstrip("/")
    return f"{event_key(event)}|{website}"


def event_score(event):
    score = 0
    discovery = clean_text(event.get("discoverySource") or "").lower()
    if discovery == "website":
        score += 100
    elif discovery in {"mistral", "chatgpt", "openai", "serpapi"}:
        score += 10
    if event.get("website"):
        score += 10
    if event.get("description") and len(event["description"]) > 80:
        score += 4
    if event.get("image"):
        score += 3
    if event.get("time"):
        score += 2
    if event.get("location"):
        score += 2
    return score


def dedupe_events(events):
    by_key = {}
    for event in events:
        if not event:
            continue
        key = event_key(event)
        current = by_key.get(key)
        if not current or event_score(event) > event_score(current):
            by_key[key] = event
    return sorted(by_key.values(), key=lambda item: (item.get("date", "9999-99-99"), item.get("title", "")))


def kultuuragenda_title_from_url(page_url):
    """Recover a readable activity title from Kultuuragenda detail slugs when page headings are generic."""
    path = urlparse(page_url).path
    match = re.search(r"/agenda/\d+/([^/?#]+)", path, re.I)
    if not match:
        return ""
    slug = match.group(1).replace("-", " ").strip()
    if not slug:
        return ""
    # Kultuuragenda commonly encodes the series prefix as "classics-<film title>".
    if slug.lower().startswith("classics "):
        return "Classics: " + slug[len("classics "):].title()
    return slug.title()


def extract_detail_event(soup, page_url, site_url, discovery_source):
    host = canonical_host(site_url)
    if host == "kultuuragenda.nl":
        # Some detail pages expose a generic H1 (for example "Het"). Prefer social
        # metadata/document title, and fall back to the event-specific URL slug.
        title = meta_content(soup, "og:title", "twitter:title")
        if not title and soup.title:
            title = clean_title(soup.title.get_text(" ", strip=True))
        if not title or not title_is_usable(title) or clean_text(title).lower() in {"het", "de", "een", "agenda", "programma"}:
            title = kultuuragenda_title_from_url(page_url)
        if not title or not title_is_usable(title):
            title = page_title(soup)
    else:
        title = page_title(soup) or meta_content(soup, "og:title", "twitter:title")
    description = meta_content(soup, "description", "og:description", "twitter:description")
    text = soup.get_text(" ", strip=True)
    raw = {
        "date": meta_content(soup, "event:start_time", "article:published_time") or parse_date_text(text),
        "title": title,
        "type": classify_type(text, canonical_host(site_url)),
        "location": location_from_lines(split_lines(soup)[:120], host_default_location(site_url)),
        "website": page_url,
        "description": description or description_from_lines(split_lines(soup)[:80], title),
        "image": first_image(soup, page_url),
        "time": first_time(text),
        "cost": first_price(text),
        "discoverySource": discovery_source,
    }
    return normalize_event(raw, site_url)


def extract_anchor_events(soup, page_url, site_url, discovery_source):
    events = []
    for anchor in soup.find_all("a", href=True):
        text = clean_text(anchor.get_text(" ", strip=True))
        if len(text) < 8 or not parse_date_text(text):
            continue
        url = normalized_url(anchor.get("href"), page_url)
        if not url or not same_host(url, site_url) or BAD_URL_RE.search(url):
            continue
        title = node_title(anchor) or title_from_dated_text(text)
        raw = {
            "date": parse_date_text(text),
            "title": title,
            "type": classify_type(text, canonical_host(site_url)),
            "location": location_from_lines(split_lines(anchor), host_default_location(site_url)),
            "website": url,
            "description": description_from_lines(split_lines(anchor), title),
            "image": "",
            "time": first_time(text),
            "cost": first_price(text),
            "discoverySource": discovery_source,
        }
        event = normalize_event(raw, site_url)
        if event:
            events.append(event)
            add_raw(discovery_source, site_url, event["title"], event["date"], event["website"], "event uit link", text)
    return events


def extract_inline_dated_events(soup, page_url, site_url, discovery_source):
    text = clean_text(soup.get_text(" ", strip=True))
    chunks = [clean_text(chunk) for chunk in DATE_BLOCK_START_RE.split(text) if clean_text(chunk)]
    events = []
    for chunk in chunks[:700]:
        if not parse_date_text(chunk):
            continue
        title = title_from_dated_text(chunk)
        if not title_is_usable(title):
            continue
        raw = {
            "date": parse_date_text(chunk),
            "title": title,
            "type": classify_type(chunk, canonical_host(site_url)),
            "location": host_default_location(site_url),
            "website": page_url,
            "description": description_after_title(chunk, title),
            "image": "",
            "time": first_time(chunk),
            "cost": first_price(chunk),
            "discoverySource": discovery_source,
        }
        event = normalize_event(raw, site_url)
        if event:
            events.append(event)
            add_raw(discovery_source, site_url, event["title"], event["date"], event["website"], "event uit datumblok", chunk)
    return events


def extract_card_events(soup, page_url, site_url, discovery_source):
    events = []
    selector = (
        "article, li, "
        "[class*='event'], [class*='agenda'], [class*='program'], [class*='programma'], "
        "[class*='card'], [class*='item'], [class*='teaser'], [class*='tile']"
    )
    for node in soup.select(selector)[:450]:
        text = clean_text(node.get_text(" ", strip=True))
        if len(text) < 20 or len(text) > 2200:
            continue
        event_date = parse_date_text(text)
        if not event_date:
            continue
        title = node_title(node) or title_from_dated_text(text)
        if not title_is_usable(title):
            continue
        link = best_link_for_node(node, page_url, site_url)
        lines = split_lines(node)
        raw = {
            "date": event_date,
            "title": title,
            "type": classify_type(text, canonical_host(site_url)),
            "location": location_from_lines(lines, host_default_location(site_url)),
            "website": link,
            "description": description_from_lines(lines, title),
            "image": first_image(node, page_url),
            "time": first_time(text),
            "cost": first_price(text),
            "discoverySource": discovery_source,
        }
        event = normalize_event(raw, site_url)
        if event:
            events.append(event)
            add_raw(discovery_source, site_url, event["title"], event["date"], event["website"], "event uit kaart", text)
    return events


def extract_line_events(soup, page_url, site_url, discovery_source):
    lines = split_lines(soup)
    events = []
    current_date = ""
    for index, line in enumerate(lines[:900]):
        parsed = parse_date_text(line)
        if parsed and (len(line.split()) <= 6 or re.match(r"^(vandaag|morgen|overmorgen|maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)", line, re.I)):
            current_date = parsed
        if parsed:
            title = title_from_dated_text(line)
            if not title_is_usable(title):
                title = clean_title(lines[index - 1]) if index > 0 else ""
            if not title_is_usable(title) and index + 1 < len(lines):
                title = clean_title(lines[index + 1])
            if not title_is_usable(title):
                continue
            window = lines[max(0, index - 1) : min(len(lines), index + 5)]
            raw = {
                "date": parsed,
                "title": title,
                "type": classify_type(" ".join(window), canonical_host(site_url)),
                "location": location_from_lines(window, host_default_location(site_url)),
                "website": page_url,
                "description": description_from_lines(window, title),
                "image": "",
                "time": first_time(" ".join(window)),
                "cost": first_price(" ".join(window)),
                "discoverySource": discovery_source,
            }
            event = normalize_event(raw, site_url)
            if event:
                events.append(event)
                add_raw(discovery_source, site_url, event["title"], event["date"], event["website"], "event uit tekstregel", " | ".join(window))
        elif current_date and title_is_usable(line):
            next_window = lines[index : min(len(lines), index + 5)]
            if not any(TIME_RE.search(part) or PRICE_RE.search(part) or EVENT_WORDS_RE.search(part) for part in next_window):
                continue
            raw = {
                "date": current_date,
                "title": line,
                "type": classify_type(" ".join(next_window), canonical_host(site_url)),
                "location": location_from_lines(next_window, host_default_location(site_url)),
                "website": page_url,
                "description": description_from_lines(next_window, line),
                "image": "",
                "time": first_time(" ".join(next_window)),
                "cost": first_price(" ".join(next_window)),
                "discoverySource": discovery_source,
            }
            event = normalize_event(raw, site_url)
            if event:
                events.append(event)
                add_raw(discovery_source, site_url, event["title"], event["date"], event["website"], "event uit dagblok", " | ".join(next_window))
    return events


def extract_vera_programme_events(html, page_url, site_url, discovery_source):
    """Parse VERA's programme listing as dated event cards, including links to ticket/event details."""
    page_path = urlparse(page_url).path.lower().rstrip("/") or "/"
    if "vera-groningen.nl" not in canonical_host(site_url) or page_path not in {"/programma", "/"}:
        return []

    soup = BeautifulSoup(html, "html.parser")
    events = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        text = clean_text(anchor.get_text(" ", strip=True) or anchor.get("aria-label") or anchor.get("title"))
        if not text or not parse_date_text(text):
            continue

        # The page's event links include weekday + day + month, then title and venue/ticket metadata.
        date_match = re.search(
            rf"(?:{WEEKDAY_PATTERN})\.?\s+\d{{1,2}}\s+(?:{MONTH_PATTERN})(?:\s+20\d{{2}})?",
            text,
            re.I,
        )
        if not date_match:
            continue
        date_text = date_match.group(0)
        event_date = parse_date_text(date_text)
        tail = text[date_match.end():].strip(" -|:")
        tail = re.split(r"\b(?:Mainstage|Downstage|Zienema|Dansen|Kelderbar|Ticket:|doors:|start:|support|Raw postpunk|Raw Irish folk|Early slowcore pioneers|Snarky and energetic|In\s+20\d{2}|Weinig bands)\b", tail, maxsplit=1, flags=re.I)[0]
        tail = STATUS_WORDS_RE.sub(" ", tail)
        title = clean_title(tail)
        if not event_date or not title_is_usable(title) or len(title) < 3:
            continue

        href = normalized_url(anchor.get("href"), page_url)
        if not href or BAD_URL_RE.search(href):
            continue
        key = (event_date, re.sub(r"[^a-z0-9]+", " ", title.lower()).strip())
        if key in seen:
            continue
        seen.add(key)

        lower_text = text.lower()
        start_pos = lower_text.find("start:")
        time_value = first_time(text[start_pos:]) if start_pos >= 0 else first_time(text)
        raw = {
            "date": event_date,
            "title": title,
            "type": classify_type(text, canonical_host(site_url)),
            "location": "VERA Groningen",
            "website": href,
            "description": compact(text, 420),
            "image": first_image(soup, page_url),
            "time": time_value,
            "cost": first_price(text),
            "discoverySource": discovery_source,
        }
        event = normalize_event(raw, site_url)
        if event:
            events.append(event)
            add_raw(discovery_source, site_url, event["title"], event["date"], event["website"], "VERA agenda-item", text)

    if events:
        add_raw("Parser", site_url, "VERA-agendapagina", "", page_url, "gespecialiseerde parser", f"{len(events)} VERA-agenda-items uit eventlinks")
    return dedupe_events(events)


def extract_uitloper_events(html, page_url, site_url, discovery_source):
    """Parse Uitloper's day-based Groningen agenda, where event cards inherit the selected page date."""
    if canonical_host(site_url) != "groningen.uitloper.nu":
        return []

    soup = BeautifulSoup(html, "html.parser")
    page_text = " ".join(split_lines(soup)[:8])
    event_date = parse_date_text(page_text)
    if not event_date:
        add_raw("Parser", site_url, "Uitloper datum niet gevonden", "", page_url, "waarschuwing", page_text[:240])
        return []

    events = []
    seen = set()
    category_paths = (
        "muziek", "party", "theater", "kunst", "jeugd", "diversen",
        "regio", "filmhuis", "quizzen", "literair", "jamsessie",
    )
    for anchor in soup.find_all("a", href=True):
        url = normalized_url(anchor.get("href"), page_url)
        if not url or not same_host(url, site_url) or BAD_URL_RE.search(url):
            continue
        path_parts = [part for part in urlparse(url).path.strip("/").split("/") if part]
        if len(path_parts) < 2 or path_parts[0].lower() not in category_paths:
            continue

        text = clean_text(anchor.get_text(" ", strip=True) or anchor.get("aria-label") or anchor.get("title"))
        if len(text) < 3 or text.lower() in {"overzicht", "tickets & info"}:
            continue

        title = node_title(anchor)
        if not title_is_usable(title):
            title = clean_title(re.split(r"\s+\d{1,2}[:.]\d{2}\s*(?:uur)?\b", text, maxsplit=1, flags=re.I)[0])
        title = re.sub(r"\s*\d{1,2}[:.]\d{2}\s*(?:uur)?\b.*$", "", title, flags=re.I).strip()
        if not title_is_usable(title) or len(title) < 3:
            continue

        location = ""
        for heading in anchor.find_all_previous(["h3", "h4"]):
            candidate = clean_text(heading.get_text(" ", strip=True))
            if candidate and not re.search(r"\d", candidate) and candidate.lower() not in category_paths:
                location = candidate
                break
        card_text = clean_text(anchor.parent.get_text(" ", strip=True) if anchor.parent else text)
        description = card_text
        if title:
            description = re.sub(re.escape(title), " ", description, count=1, flags=re.I)
        description = re.sub(r"\b(?:[01]?\d|2[0-3])[:.]\d{2}\s*(?:uur)?\b", " ", description, flags=re.I)
        description = compact(description, 420)
        key = (event_date, re.sub(r"[^a-z0-9]+", " ", title.lower()).strip(), url.lower())
        if key in seen:
            continue
        seen.add(key)

        event = normalize_event({
            "date": event_date,
            "title": title,
            "type": classify_type(" ".join([title, text, location]), canonical_host(site_url)),
            "location": location or "Groningen",
            "website": url,
            "description": description,
            "image": first_image(anchor, page_url),
            "time": first_time(text),
            "cost": first_price(text),
            "discoverySource": discovery_source,
        }, site_url)
        if event:
            events.append(event)
            add_raw(discovery_source, site_url, event["title"], event["date"], event["website"], "Uitloper agenda-item", text)

    add_raw("Parser", site_url, "Uitloper-dagpagina", event_date, page_url, "gespecialiseerde parser", f"{len(events)} agenda-items op {event_date}")
    return dedupe_events(events)


def extract_venue_agenda_events(html, page_url, site_url, discovery_source):
    """Extract dated event cards from supported venue programme listings."""
    host = canonical_host(site_url)
    if host not in {"martiniplaza.nl", "spotgroningen.nl", "simplon.nl", "zwolsetheaters.nl"}:
        return []

    soup = BeautifulSoup(html, "html.parser")
    events = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        url = normalized_url(anchor.get("href"), page_url)
        if not url or not same_host(url, site_url) or BAD_URL_RE.search(url):
            continue
        if host == "zwolsetheaters.nl" and not re.fullmatch(r"/(?:programma/.+|voorstellingen?/[^/]+|events?/[^/]+|agenda/[^/]+)/?", urlparse(url).path, re.I):
            continue
        if host == "martiniplaza.nl" and not re.fullmatch(r"/nl/agenda/[^/]+/?", urlparse(url).path, re.I):
            # Only a single detail slug below /nl/agenda is an event. Never emit the
            # overview page itself or deeper nested/non-event URLs as event records.
            continue
        title = clean_title(node_title(anchor) or anchor.get_text(" ", strip=True) or anchor.get("aria-label") or anchor.get("title") or "")
        if host == "simplon.nl":
            # Simplon cards start with compact dates such as "vr 23.10"; these are not part of the title.
            title = re.sub(r"^(?:" + WEEKDAY_PATTERN + r")\.?\s*\d{1,2}\.\d{1,2}(?:\.20\d{2})?\s*", "", title, flags=re.I)
            title = re.sub(r"\s+(?:concert|dance|up)\s*$", "", title, flags=re.I)
        if not title_is_usable(title) or len(title) < 3:
            continue

        card = anchor
        card_text = clean_text(anchor.get_text(" ", strip=True))
        event_date = parse_date_text(card_text)
        for parent in anchor.parents:
            if getattr(parent, "name", None) in {"body", "html"}:
                break
            candidate_text = clean_text(parent.get_text(" ", strip=True))
            candidate_date = parse_date_text(candidate_text)
            if host == "martiniplaza.nl" and candidate_text and title:
                # Homepage cards can share a parent with neighboring cards. Use the
                # date closest to this event title rather than the first date in the group.
                title_position = candidate_text.lower().find(title.lower())
                date_matches = list(re.finditer(r"\b\d{1,2}\s+(?:" + MONTH_PATTERN + r")(?:\s+20\d{2})?\b", candidate_text, re.I))
                if title_position >= 0 and date_matches:
                    closest_date = min(date_matches, key=lambda match: abs(match.start() - title_position))
                    candidate_date = parse_date_text(closest_date.group(0)) or candidate_date
            if not candidate_date:
                compact_pattern = r"\b(?:" + WEEKDAY_PATTERN + r")\.?\s*(\d{1,2})\s*(" + MONTH_PATTERN + r")(?:\s*(20\d{2}))?\b"
                compact_date = re.search(compact_pattern, candidate_text, re.I)
                if compact_date:
                    month_num = MONTH_NUMBERS.get(compact_date.group(2).lower())
                    candidate_date = iso_from_parts(compact_date.group(1), month_num, compact_date.group(3)) if month_num else ""
                # Simplon formats programme dates compactly as "do 29.10" rather than "29 oktober".
                if not candidate_date and host == "simplon.nl":
                    numeric_pattern = r"\b(?:" + WEEKDAY_PATTERN + r")\.?\s*(\d{1,2})\.(\d{1,2})(?:\.(20\d{2}))?\b"
                    numeric_date = re.search(numeric_pattern, candidate_text, re.I)
                    if numeric_date:
                        candidate_date = iso_from_parts(numeric_date.group(1), numeric_date.group(2), numeric_date.group(3))
            max_card_text = 280 if host == "martiniplaza.nl" else 1800
            if candidate_date and len(candidate_text) <= max_card_text:
                card, card_text, event_date = parent, candidate_text, candidate_date
                break

        if not event_date or len(card_text) > 1800:
            continue
        description = description_from_lines(split_lines(card), title)
        time_text = card_text
        if host == "simplon.nl":
            time_text = re.sub(r"\b(?:" + WEEKDAY_PATTERN + r")\.?\s*\d{1,2}\.\d{1,2}(?:\.20\d{2})?\b", " ", time_text, flags=re.I)
        raw = {
            "date": event_date,
            "title": title,
            "type": classify_type(card_text, host),
            "location": host_default_location(site_url),
            "website": url,
            "description": description,
            "image": first_image(card, page_url),
            "time": first_time(time_text),
            "cost": first_price(card_text),
            "discoverySource": discovery_source,
        }
        event = normalize_event(raw, site_url)
        if not event:
            continue
        key = (event["date"], re.sub(r"[^a-z0-9]+", " ", event["title"].lower()).strip(), event["website"])
        if key in seen:
            continue
        seen.add(key)
        events.append(event)
        add_raw(discovery_source, site_url, event["title"], event["date"], event["website"], "venue-agenda kaart", card_text)

    add_raw("Parser", site_url, "Agenda geanalyseerd", "", page_url, "gespecialiseerde parser", f"{len(events)} agenda-items gevonden")
    return dedupe_events(events)


def parse_page_events(html, page_url, site_url, discovery_source):
    soup = BeautifulSoup(html, "html.parser")
    events = []
    jsonld_events = parse_jsonld_events(soup, page_url, site_url, discovery_source)
    anchor_events = extract_anchor_events(soup, page_url, site_url, discovery_source)
    inline_events = extract_inline_dated_events(soup, page_url, site_url, discovery_source)
    card_events = extract_card_events(soup, page_url, site_url, discovery_source)
    events.extend(jsonld_events)
    events.extend(anchor_events)
    events.extend(inline_events)
    events.extend(card_events)
    detail = extract_detail_event(soup, page_url, site_url, discovery_source)
    if detail:
        events.append(detail)
        add_raw(discovery_source, site_url, detail["title"], detail["date"], detail["website"], "event uit detailpagina", soup.get_text(" ", strip=True)[:420])
    line_events = extract_line_events(soup, page_url, site_url, discovery_source)
    events.extend(line_events)
    add_raw(
        "Parser",
        site_url,
        "Pagina geteld",
        "",
        page_url,
        "analyse",
        f"jsonld:{len(jsonld_events)}, links:{len(anchor_events)}, datumblokken:{len(inline_events)}, kaarten:{len(card_events)}, regels:{len(line_events)}, detail:{1 if detail else 0}",
    )
    return dedupe_events(events)


def link_score(anchor, url, text):
    score = 0
    combined = f"{url} {text}"
    if parse_date_text(text):
        score += 12
    if EVENT_WORDS_RE.search(combined):
        score += 8
    if re.search(r"/(event|events|programma|agenda|concert|voorstelling|film|activiteit|tickets?)/", url, re.I):
        score += 6
    if title_is_usable(text):
        score += 2
    if BAD_URL_RE.search(combined):
        score -= 50
    if len(text) > 220:
        score -= 4
    return score


def collect_event_links(html, page_url, site_url):
    soup = BeautifulSoup(html, "html.parser")
    scored = []
    is_kultuuragenda = "kultuuragenda.nl" in canonical_host(site_url)
    is_martiniplaza = canonical_host(site_url) == "martiniplaza.nl"
    is_visitgroningen = canonical_host(site_url) == "visitgroningen.nl"
    for anchor in soup.find_all("a", href=True):
        url = normalized_url(anchor.get("href"), page_url)
        if not url or not same_host(url, site_url):
            continue
        text = clean_text(anchor.get_text(" ", strip=True) or anchor.get("title") or anchor.get("aria-label"))
        # Martiniplaza event cards link to /nl/agenda/<slug>. Ignore all other
        # pages (magazine, navigation, contact forms) even if their text contains dates.
        if is_martiniplaza:
            if re.search(r"/nl/agenda/[^/?#]+/?$", urlparse(url).path, re.I):
                scored.append((1000, url, text))
            continue
        # Visit Groningen: alleen echte evenement-detailpagina's onder /evenementen/<slug>.
        # De overzichtspagina is een linkdirectory; algemene kaarten/tekst zijn geen events.
        if is_visitgroningen:
            if re.search(r"/evenementen/[^/?#]+/?$", urlparse(url).path, re.I):
                scored.append((1000, url, text))
            continue
        # Prioriteer echte detailpagina's; /agenda zelf is alleen de overzichtspagina.
        if is_kultuuragenda and re.search(r"/agenda/\d+/[^/?#]+", url, re.I):
            scored.append((1000, url, text))
            continue
        if is_kultuuragenda and urlparse(url).path.rstrip("/") == "/agenda":
            continue
        score = link_score(anchor, url, text)
        if score > 0:
            scored.append((score, url, text))
    scored.sort(key=lambda item: (-item[0], item[1]))
    result = []
    seen = set()
    for _, url, text in scored:
        key = url.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        result.append(url)
        add_raw("Website", site_url, text or "link", "", url, "detail-link gevonden", text)
        if len(result) >= max(80, MAX_EVENTS_PER_SITE * 4):
            break
    return result


def start_urls_for_site(site_url):
    exact = normalized_url(site_url)
    if not exact:
        return []
    parsed = urlparse(exact)
    host = canonical_host(exact)
    if "vera-groningen.nl" in host:
        # The programme listing shows only the next ~20 events. The homepage also
        # exposes separately confirmed events further ahead; read both without
        # crawling news articles, which can contain historical dates.
        base = f"{parsed.scheme}://{parsed.netloc}"
        return [
            urljoin(base, "/programma/?category=all&history=0&lang=nl"),
            urljoin(base, "/?lang=nl"),
        ]
    if host == "visitgroningen.nl":
        base = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.lower().rstrip("/")
        if path == "/nl/doen/wandelen":
            return unique_urls([
                exact,
                urljoin(base, "/nl/doen/wandelkaart-groningen"),
                urljoin(base, "/nl/doen/wandelen"),
            ])
        if path == "/nl/doen/evenementen":
            return unique_urls([
                exact,
                urljoin(base, "/nl/doen/uitgaan"),
                urljoin(base, "/nl/doen/cultuur/evenementen"),
            ])
    if host == "groningen.uitloper.nu":
        # Uitloper shows one selected date per page. Start on the requested date and
        # advance day by day until the configured event limit is reached.
        first_day = date.fromisoformat(INPUT_DATE_FROM) if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", INPUT_DATE_FROM) else TODAY
        urls = []
        for offset in range(14):
            day = first_day + timedelta(days=offset)
            timestamp = int(datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc).timestamp())
            urls.append(f"{exact.rstrip('/')}?date={timestamp}")
        return urls
    if host == "martiniplaza.nl":
        # Use the agenda only to discover event detail URLs.
        base = f"{parsed.scheme}://{parsed.netloc}"
        return [urljoin(base, "/nl/agenda")]
    if host == "paradiso.nl":
        # The concertagenda landing page is an overview, not an event.
        # Discover links there and parse only individual /programma/<slug>/<id> pages.
        base = f"{parsed.scheme}://{parsed.netloc}"
        return [urljoin(base, "/landing/concertagenda-paradiso/2069817")]
    if host == "spotgroningen.nl":
        return [urljoin(exact, "/programma")]
    if host == "socialdeal.nl":
        base = f"{parsed.scheme}://{parsed.netloc}"
        # Different public listing pages expose complementary batches of deals.
        return [
            urljoin(base, "/dichtbij/groningen/"),
            urljoin(base, "/ontdek/groningen/"),
            urljoin(base, "/deals/groningen/"),
        ]
    if host == "groupon.nl":
        base = f"{parsed.scheme}://{parsed.netloc}"
        return unique_urls([
            urljoin(base, "/"), urljoin(base, "/kortingsbonnen"), urljoin(base, "/deal/"),
            urljoin(base, "/best-deals/"), urljoin(base, "/local/"),
        ])
    if host == "simplon.nl":
        return [urljoin(exact, "/programma/")]
    urls = [exact]
    if "ibood.com" in host:
        # The current Dutch storefront uses this path; the root redirects here.
        urls.extend([urljoin(exact, "/nl/s-nl"), urljoin(exact, "/offers/nl/s-nl")])
    if parsed.path in {"", "/"}:
        base = f"{parsed.scheme}://{parsed.netloc}"
        urls.extend(urljoin(base, path) for path in COMMON_PATHS_FOR_ROOT_SITES)
    return unique_urls(urls)


def unique_urls(urls):
    result = []
    seen = set()
    for url in urls:
        normalized = normalized_url(url)
        key = normalized.lower().rstrip("/")
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def serpapi_links_for_site(site_url):
    candidates = []
    for item in parse_json_list(INPUT_SERPAPI_LINKS_RAW):
        url = normalized_url(item)
        if url and same_host(url, site_url):
            candidates.append(url)
    return unique_urls(candidates)


def serpapi_events_for_site(site_url):
    events = []
    for item in [*parse_json_list(INPUT_SERPAPI_EVENTS_RAW), *parse_json_list(INPUT_AI_EVENTS_RAW)]:
        if not isinstance(item, dict):
            continue
        item_site = item.get("site") or item.get("url") or item.get("website") or ""
        item_url = item.get("url") or item.get("website") or ""
        if not (same_host(item_site, site_url) or same_host(item_url, site_url)):
            continue
        source_name = clean_text(item.get("source") or item.get("discoverySource") or "SerpAPI")
        date_blob = " ".join(clean_text(item.get(key)) for key in ("date", "dateText", "time", "title", "description", "rawText") if item.get(key))
        title = clean_title(item.get("title") or "")
        description = compact(item.get("description") or item.get("rawText") or "", 420)
        raw = {
            "date": parse_date_text(date_blob),
            "title": title,
            "type": item.get("type") or classify_type(" ".join([title, description]), canonical_host(site_url)),
            "location": item.get("location") or host_default_location(site_url),
            "website": item_url,
            "description": description,
            "image": "",
            "time": first_time(item.get("time") or date_blob),
            "cost": first_price(description),
            "discoverySource": source_name,
        }
        event = normalize_event(raw, site_url)
        if event:
            events.append(event)
            add_raw(source_name, site_url, event["title"], event["date"], event["website"], f"event direct uit {source_name}", description or date_blob)
        else:
            add_raw(source_name, site_url, title or "Event-kandidaat genegeerd", parse_date_text(date_blob), item_url, "onvolledig", description or date_blob)
    return dedupe_events(events)


def event_source_counts(events):
    counts = {}
    for event in events:
        source = clean_text(event.get("discoverySource") or "AI")
        counts[source] = counts.get(source, 0) + 1
    return counts


def import_serpapi_raw_log():
    for row in parse_json_list(INPUT_SERPAPI_RAW_LOG):
        if not isinstance(row, dict):
            continue
        add_raw(
            "SerpAPI",
            row.get("site") or "",
            row.get("title") or "",
            row.get("date") or "",
            row.get("url") or row.get("link") or "",
            row.get("status") or "zoekresultaat",
            row.get("rawText") or row.get("snippet") or "",
        )


def remaining_timeout(deadline):
    # Respect short per-site budgets; never add a full extra second when only a
    # fraction of the configured time remains.
    return max(0.2, min(7.0, deadline - time.monotonic()))


def fetch_html(session, url, deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("tijdslimiet bereikt")
    response = session.get(url, headers=REQUEST_HEADERS, timeout=remaining_timeout(deadline), allow_redirects=True)
    if response.status_code >= 400:
        raise requests.HTTPError(f"HTTP {response.status_code}")
    content_type = response.headers.get("content-type", "")
    if content_type and "html" not in content_type and "xml" not in content_type and "text" not in content_type:
        raise ValueError(f"geen HTML ({content_type})")
    return response.text, response.url


def ibood_offer_price(value):
    """Pak de actuele dealprijs, niet de adviesprijs die er vaak vóór staat."""
    text = clean_text(value)
    after_advice = re.search(r"adviesprijs(?:\s+van\s+de\s+fabrikant)?", text, re.I)
    if after_advice:
        tail = text[after_advice.end():]
        amounts = list(re.finditer(r"(?:€\s*)?\d{1,4}[,.]\s*\d{2}", tail))
        if len(amounts) >= 2:
            amount = amounts[1].group(0)
        elif amounts:
            amount = amounts[0].group(0)
        else:
            amount = ""
    else:
        amounts = list(re.finditer(r"€\s*\d{1,4}[,.]\s*\d{2}", text))
        amount = amounts[-1].group(0) if amounts else ""
    if not amount:
        return first_price(text)
    amount = re.sub(r"^€\s*", "", amount).replace(" ", "").replace(",", ".")
    try:
        number = float(amount)
        return f"€ {number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except ValueError:
        return first_price(text)


def extract_visitgroningen_walks(html, page_url, site_url):
    """Haal concrete wandelroutes/ideeën van Visit Groningen op i.p.v. een hardcoded overzicht."""
    if "visitgroningen.nl" not in canonical_host(site_url) or "/wandelen" not in urlparse(page_url).path.lower():
        return []
    soup = BeautifulSoup(html, "html.parser")
    result = []
    seen = set()
    for node in soup.select("article, [class*='card'], [class*='teaser'], [class*='tile'], li")[:500]:
        anchor = node if getattr(node, "name", "") == "a" and node.get("href") else node.find("a", href=True)
        if not anchor:
            continue
        url = normalized_url(anchor.get("href"), page_url)
        title = clean_title(anchor.get_text(" ", strip=True) or node_title(node))
        text = clean_text(node.get_text(" ", strip=True))
        if not url or not same_host(url, site_url) or "/doen/wandelen" in urlparse(url).path.lower():
            continue
        if not title_is_usable(title) or title.lower() in seen:
            continue
        seen.add(title.lower())
        result.append({
            "date": TODAY.isoformat(),
            "title": title,
            "type": "Wandelen",
            "location": "Groningen",
            "website": url,
            "description": compact(text or f"Wandelroute of wandelidee van Visit Groningen: {title}", 420),
            "cost": "Gratis",
            "discoverySource": "Website",
        })
        add_raw("Website", site_url, title, TODAY.isoformat(), url, "wandelroute gevonden", text)
        if len(result) >= MAX_EVENTS_PER_SITE:
            break
    return result



def extract_socialdeal_offers(html, page_url, site_url):
    """Lees echte Social Deal-kaarten; sla navigatie, blog en categoriepagina's over."""
    if "socialdeal.nl" not in canonical_host(site_url):
        return []
    soup = BeautifulSoup(html, "html.parser")
    offers = []
    seen = set()
    deal_limit = MAX_EVENTS_PER_SITE
    city_pattern = re.compile(
        r"\b(Groningen|Veendam|Drachten|Assen|Haren|Hoogezand|Stadskanaal|Zuidlaren|"
        r"Appingedam|Delfzijl|Winschoten|Leek|Marum|Roden|Norg|Oldekerk|Zuidbroek|"
        r"Midwolda|Anjum|Eanjum|Drouwen|Gasselte|Paterswolde|Amsterdam|Utrecht|"
        r"Rotterdam|Den Haag|Zwolle|Leeuwarden|Online|Bij jou thuis)\b",
        re.I,
    )
    excluded_path = re.compile(r"^/(?:blog|inloggen|account|restaurants|hotels|dichtbij|ontdek|theme|categorie|bedrijf/social-deal-giftcard)(?:/|$)", re.I)
    for anchor in soup.find_all("a", href=True):
        href = normalized_url(anchor.get("href"), page_url)
        parsed_path = urlparse(href).path
        if not href or canonical_host(href) != canonical_host(site_url) or excluded_path.search(parsed_path):
            continue
        if parsed_path.rstrip("/") in {"/", "/deals", "/deals/groningen", "/ontdek/groningen", "/dichtbij/groningen"}:
            continue
        # Select only anchors inside a real offer card with sales and price data.
        ancestors = [anchor]
        parent = anchor.parent
        for _ in range(5):
            if not parent or not hasattr(parent, "get_text"):
                break
            ancestors.append(parent)
            parent = parent.parent
        card = None
        for candidate in ancestors[1:]:
            candidate_text = clean_text(candidate.get_text(" ", strip=True))
            if (
                len(candidate_text) >= 35
                and len(candidate_text) <= 1200
                and re.search(r"\bVerkocht\s*:", candidate_text, re.I)
                and re.search(r"€\s*[\d.,]+", candidate_text)
            ):
                card = candidate
                break
        if card is None:
            continue
        card_text = clean_text(card.get_text(" ", strip=True))
        title_node = card.select_one("h1, h2, h3, h4, [class*='deal-title'], [class*='title'], [class*='name']")
        title = clean_title(title_node.get_text(" ", strip=True) if title_node else anchor.get_text(" ", strip=True))
        if not title_is_usable(title):
            title = clean_title(node_title(card))
        if not title_is_usable(title) or title.lower() in {"bekijk deal", "meer informatie", "lees meer", "favorieten"}:
            continue
        key = href.lower().split("?")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        location = ""
        for match in city_pattern.finditer(card_text):
            candidate = clean_text(match.group(1))
            location = candidate
            if candidate.lower() in {"online", "bij jou thuis"}:
                break
        if not location:
            location = "Locatie niet vermeld"
        description = card_text
        description = re.sub(re.escape(title), " ", description, count=1, flags=re.I)
        description = re.sub(r"\b(?:verkocht|sold out)\s*:?\s*[\d.,]*", " ", description, flags=re.I)
        description = compact(description, 420)
        if not description:
            description = f"Social Deal-aanbieding: {title}. Bekijk de dealpagina voor voorwaarden en beschikbaarheid."
        date_text = parse_date_text(card_text) or TODAY.isoformat()
        image_node = card.select_one("img[src], img[data-src]")
        raw = {
            "date": date_text,
            "title": title,
            "type": "Aanbiedingen",
            "location": location,
            "website": href,
            "description": description,
            "cost": first_price(card_text),
            "image": normalized_url((image_node.get("src") or image_node.get("data-src") or ""), page_url) if image_node else "",
            "discoverySource": "Website",
            "siteUrl": site_url,
        }
        event = normalize_event(raw, site_url)
        if event:
            offers.append(event)
            add_raw("Website", site_url, title, date_text, href, "deal gevonden", f"Locatie: {location}; {description[:180]}")
        if len(offers) >= deal_limit:
            break
    add_raw("Parser", site_url, "Social Deal-telling", "", page_url, "ok", f"{len(offers)} aanbiedingen gevonden (limiet {deal_limit})")
    return dedupe_events(offers)



def extract_biosagenda_events(html, page_url, site_url, discovery_source="Website"):
    """Extract upcoming Groningen film showings from BiosAgenda film cards only."""
    soup = BeautifulSoup(html, "html.parser")
    events = []
    seen = set()
    cinemas = {
        "kinepolis groningen": "Kinepolis Groningen",
        "pathé groningen": "Pathé Groningen",
        "pathe groningen": "Pathé Groningen",
        "rkz-bios": "RKZ-Bios Groningen",
        "vera zienema": "Vera Zienema Groningen",
    }
    cinema = "Groningen"
    for heading in soup.find_all(["h2", "h3", "h4"]):
        title_text = clean_title(heading.get_text(" ", strip=True))
        cinema_key = title_text.lower()
        if cinema_key in cinemas:
            cinema = cinemas[cinema_key]
            continue
        if not title_is_usable(title_text) or len(title_text) > 120:
            continue
        if re.search(r"bioscoopagenda|word lid|lees meer|top filmnieuws|nieuwsbrief|films in groningen", title_text, re.I):
            continue
        card = None
        card_lines = []
        for parent in heading.parents:
            if getattr(parent, "name", None) in {"body", "html"}:
                break
            lines = [clean_text(line) for line in parent.get_text("\n", strip=True).splitlines()]
            text = clean_text(" ".join(lines))
            if len(text) > 3200:
                break
            has_showtime = any(
                parse_date_text(line) and (
                    TIME_RE.search(line)
                    or any(TIME_RE.search(next_line) for next_line in lines[line_index + 1:line_index + 3])
                )
                for line_index, line in enumerate(lines)
            )
            if has_showtime:
                card = parent
                card_lines = lines
                break
        if card is None:
            continue
        schedule_lines = []
        for line_index, line in enumerate(card_lines):
            event_day = parse_date_text(line)
            if not event_day:
                continue
            schedule_text = line
            for next_line in card_lines[line_index + 1:line_index + 3]:
                if parse_date_text(next_line):
                    break
                if TIME_RE.search(next_line):
                    schedule_text += " " + next_line
            times = list(dict.fromkeys(match.group(0).replace(".", ":") for match in TIME_RE.finditer(schedule_text)))
            if times:
                schedule_lines.append((event_day, times))
        if not schedule_lines:
            continue
        title_anchor = heading.find_parent("a") or heading.find("a")
        film_url = normalized_url(title_anchor.get("href"), page_url) if title_anchor and title_anchor.get("href") else page_url
        card_text = clean_text(card.get_text(" ", strip=True))
        description = compact(" ".join(line for line in card_lines if not parse_date_text(line) and not TIME_RE.search(line) and clean_text(line) != title_text), 420)
        image = first_image(card, page_url)
        for event_day, times in schedule_lines:
            time_text = ", ".join(times)
            event = normalize_event({
                "title": title_text,
                "date": event_day,
                "time": time_text,
                "type": "Film",
                "location": cinema,
                "website": film_url,
                "description": description or f"Filmvertoningen in {cinema}. Speeltijden: {time_text}.",
                "cost": first_price(card_text),
                "image": image,
                "discoverySource": discovery_source,
            }, site_url)
            if event:
                key = event_key(event)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
    add_raw("Parser", site_url, "BiosAgenda speeldata", "", page_url, "gespecialiseerde parser", f"{len(events)} filmvermeldingen met speeldata gevonden")
    return dedupe_events(events)

def extract_date_listing_events(html, page_url, site_url, discovery_source="Website"):
    """Site-specific card parser for VVV Ameland and Concertgebouw listings."""
    host = canonical_host(site_url)
    if host not in {"vvvameland.nl", "concertgebouw.nl", "biosagenda.nl"}:
        return []
    soup = BeautifulSoup(html, "html.parser")
    events = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = normalized_url(anchor.get("href"), page_url)
        if not href or not same_host(href, site_url) or BAD_URL_RE.search(href):
            continue
        # Climb only until a reasonably small card with a date is found.
        card = anchor
        card_text = clean_text(anchor.get_text(" ", strip=True))
        event_date = parse_date_text(card_text)
        for parent in anchor.parents:
            if getattr(parent, "name", None) in {"body", "html"}:
                break
            candidate = clean_text(parent.get_text(" ", strip=True))
            if len(candidate) > 1800:
                break
            candidate_date = parse_date_text(candidate)
            if candidate_date:
                card, card_text, event_date = parent, candidate, candidate_date
                # Prefer the smallest ancestor containing a date and a usable title.
                if node_title(parent) or anchor.get_text(" ", strip=True):
                    break
        if not event_date or not card_text or len(card_text) > 1800:
            continue
        title_node = card.select_one("h1, h2, h3, h4, [itemprop='name'], [class*='title'], [class*='titel']")
        title = clean_title(title_node.get_text(" ", strip=True) if title_node else anchor.get_text(" ", strip=True))
        if not title_is_usable(title):
            title = node_title(card)
        if not title_is_usable(title):
            continue
        path = urlparse(href).path.lower()
        if host == "concertgebouw.nl" and not re.search(r"/concerten/|/concert/", path):
            # The listing's real concert links use concert detail paths; skip filters/navigation.
            if not re.search(r"/concerten-en-tickets/", path):
                continue
        key = (event_date, title.lower(), href.split("?")[0].rstrip("/"))
        if key in seen:
            continue
        seen.add(key)
        description = description_from_lines(split_lines(card), title)
        image_node = card.select_one("img[src], img[data-src]")
        image = normalized_url((image_node.get("src") or image_node.get("data-src") or "") if image_node else "", page_url)
        location = "Ameland" if host == "vvvameland.nl" else "Concertgebouw Amsterdam"
        event = normalize_event({
            "date": event_date,
            "title": title,
            "type": classify_type(card_text, host),
            "location": location,
            "website": href,
            "description": description or compact(card_text, 420),
            "image": image,
            "time": first_time(card_text),
            "cost": first_price(card_text),
            "discoverySource": discovery_source,
        }, site_url)
        if event:
            events.append(event)
            add_raw(discovery_source, site_url, title, event_date, href, "agenda-kaart", card_text)
        if len(events) >= MAX_EVENTS_PER_SITE:
            break
    add_raw("Parser", site_url, "Agenda-kaarten geteld", "", page_url, "gespecialiseerde parser", f"{len(events)} resultaten")
    return dedupe_events(events)


def extract_groupon_offers(html, page_url, site_url):
    """Extract Groupon product/deal cards without requiring an event date."""
    if canonical_host(site_url) != "groupon.nl":
        return []
    soup = BeautifulSoup(html, "html.parser")
    offers = []
    seen = set()
    selectors = [
        "article", "[data-testid*='deal']", "[data-testid*='product']",
        "[class*='deal-card']", "[class*='dealCard']", "[class*='tile']",
        "[class*='product-card']", "[class*='productCard']",
    ]
    for selector in selectors:
        for card in soup.select(selector)[:500]:
            anchor = card if getattr(card, "name", "") == "a" and card.get("href") else card.select_one("a[href]")
            if not anchor:
                continue
            href = normalized_url(anchor.get("href"), page_url)
            if not href or canonical_host(href) != "groupon.nl" or BAD_URL_RE.search(href):
                continue
            text = clean_text(card.get_text(" ", strip=True))
            if len(text) < 20 or len(text) > 1600 or not re.search(r"(?:€\s*\d|\d+[,.]\d{2}\s*€)", text):
                continue
            title_node = card.select_one("h1, h2, h3, h4, [class*='title'], [class*='name']")
            title = clean_title(title_node.get_text(" ", strip=True) if title_node else anchor.get_text(" ", strip=True))
            if not title_is_usable(title) or title.lower() in {"bekijk deal", "meer informatie", "lees meer"}:
                continue
            key = href.split("?")[0].rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            image_node = card.select_one("img[src], img[data-src]")
            image = normalized_url((image_node.get("src") or image_node.get("data-src") or "") if image_node else "", page_url)
            description = compact(re.sub(re.escape(title), " ", text, count=1, flags=re.I), 420)
            offers.append({
                "date": TODAY.isoformat(), "time": "", "title": title,
                "type": "Aanbiedingen", "location": "Online",
                "locationLink": "", "website": href, "source": "Groupon",
                "discoverySource": "Website",
                "description": description or "Groupon-aanbieding; bekijk de dealpagina voor voorwaarden en beschikbaarheid.",
                "cost": first_price(text), "image": image,
            })
            add_raw("Website", site_url, title, TODAY.isoformat(), href, "Groupon-deal gevonden", text)
            if len(offers) >= MAX_EVENTS_PER_SITE:
                add_raw("Parser", site_url, "Groupon-telling", "", page_url, "ok", f"{len(offers)} aanbiedingen; limiet {MAX_EVENTS_PER_SITE}")
                return offers
    add_raw("Parser", site_url, "Groupon-telling", "", page_url, "ok", f"{len(offers)} aanbiedingen gevonden; limiet {MAX_EVENTS_PER_SITE}")
    return dedupe_events(offers)


def extract_ibood_offers(html, page_url, site_url):
    """Haal productaanbiedingen van iBOOD op als doorlopende aanbiedingen."""
    if "ibood.com" not in canonical_host(site_url):
        return []
    soup = BeautifulSoup(html, "html.parser")
    offers = []
    seen = set()
    selectors = ["article", "[class*='product-card']", "[class*='productCard']", "[class*='product-item']", "[class*='deal-card']", "[data-testid*='product']", "li"]
    for selector in selectors:
        for node in soup.select(selector):
            title_node = node.select_one("h1, h2, h3, h4, [class*='title'], [class*='name']")
            link_node = node.select_one("a[href]")
            if not title_node or not link_node:
                continue
            title = clean_title(title_node.get_text(" ", strip=True))
            href = normalized_url(link_node.get("href"), page_url)
            text = clean_text(node.get_text(" ", strip=True))
            if not title or len(title) < 8 or not href or "ibood.com" not in canonical_host(href):
                continue
            offer_price = ibood_offer_price(text)
            if not offer_price or re.search(r"\b(voorwaarden|klantenservice|inloggen|mijn account|nieuwsbrief)\b", title, re.I):
                continue
            key = href.lower().split("?")[0].rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            image_node = node.select_one("img[src], img[data-src]")
            image = normalized_url((image_node.get("src") or image_node.get("data-src")) if image_node else "", page_url)
            offers.append({
                "date": TODAY.isoformat(),
                "time": "",
                "title": title,
                "type": "Aanbiedingen",
                "location": "Online",
                "locationLink": "",
                "website": href,
                "source": "iBOOD",
                "discoverySource": "Website",
                "description": compact(text.replace(title, "", 1), 320) or "Online aanbieding op iBOOD.",
                "cost": offer_price,
                "image": image,
            })
            if len(offers) >= MAX_EVENTS_PER_SITE:
                return offers
    return offers


def discover_feed_urls(html, page_url, site_url):
    """Vind openbare RSS/Atom-feeds op de geselecteerde website."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel", [])).lower()
        content_type = str(link.get("type", "")).lower()
        if "alternate" in rel and ("rss" in content_type or "atom" in content_type or "xml" in content_type):
            candidates.append(urljoin(page_url, link["href"]))
    base = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"
    candidates.extend(urljoin(base, path) for path in ("/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/events/feed/"))
    result = []
    seen = set()
    for candidate in candidates:
        url = normalized_url(candidate)
        if not url or not same_host(url, site_url) or url.lower() in seen:
            continue
        seen.add(url.lower())
        result.append(url)
    return result[:8]


def parse_feed_events(feed_text, feed_url, site_url):
    """Lees eenvoudige RSS/Atom-feeds zonder externe API of betaalde dienst."""
    try:
        root = ET.fromstring(feed_text)
    except ET.ParseError:
        return []
    events = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag not in {"item", "entry"}:
            continue
        fields = {}
        for child in list(node):
            key = child.tag.rsplit("}", 1)[-1].lower()
            value = "".join(child.itertext()).strip()
            if key == "link" and not value:
                value = child.attrib.get("href", "")
            if value and key not in fields:
                fields[key] = value
        title = clean_text(fields.get("title", ""))
        link = normalized_url(fields.get("link", ""), feed_url)
        if not link:
            for child in list(node):
                if child.tag.rsplit("}", 1)[-1].lower() == "link" and child.attrib.get("href"):
                    link = normalized_url(child.attrib["href"], feed_url)
                    if link:
                        break
        if not link or not same_host(link, site_url) or not title:
            continue
        combined = f"{title} {link}"
        if not EVENT_WORDS_RE.search(combined) and not parse_date_text(title):
            continue
        raw_date = fields.get("startdate") or fields.get("pubdate") or fields.get("published") or fields.get("updated") or fields.get("date") or ""
        try:
            parsed = parsedate_to_datetime(raw_date)
            raw_date = parsed.date().isoformat()
        except (TypeError, ValueError, OverflowError):
            pass
        description = clean_text(fields.get("description") or fields.get("summary") or fields.get("content", ""))
        event = normalize_event({
            "title": title,
            "date": raw_date or title,
            "website": link,
            "description": description,
            "location": "",
            "discoverySource": "RSS/Atom"
        }, site_url)
        if event:
            events.append(event)
    return dedupe_events(events)


def scrape_site(site_url, site_index=0, site_total=0, progress_callback=None, existing_event_keys=None):
    existing_event_keys = set(existing_event_keys or set())

    def new_event_count(candidates):
        return sum(1 for event in dedupe_events(candidates) if event_key(event) not in existing_event_keys)

    original_site = clean_text(site_url)
    normalized_site = normalized_url(original_site)
    started = time.monotonic()
    deadline = started + SITE_TIME_LIMIT_SECONDS
    session = requests.Session()
    events = []
    detail_links = []
    feed_urls = []
    timed_out = False

    progress = f"site {site_index}/{site_total}" if site_index and site_total else "site"
    add_raw("Website", original_site, "Start", "", normalized_site, "start", f"{progress}: max {MAX_EVENTS_PER_SITE} events, max {SITE_TIME_LIMIT_SECONDS} seconden")
    add_raw("Parser", original_site, "Scrapertype vastgesteld", "", normalized_site, "strategie", detect_scraper_type(normalized_site))

    serpapi_events = serpapi_events_for_site(normalized_site)
    if serpapi_events:
        events.extend(serpapi_events)
        for source_name, count in event_source_counts(serpapi_events).items():
            add_raw(source_name, original_site, "Eventvelden gebruikt", "", normalized_site, "ok", f"{count} event-kandidaten direct uit {source_name}")

    for page_url in start_urls_for_site(normalized_site):
        if progress_callback:
            progress_callback(dedupe_events(events), stage="Websitepagina ophalen")
        if (new_event_count(events) >= MAX_EVENTS_PER_SITE and "vera-groningen.nl" not in canonical_host(normalized_site)) or time.monotonic() >= deadline:
            timed_out = time.monotonic() >= deadline
            break
        try:
            html, final_url = fetch_html(session, page_url, deadline)
            add_raw("Website", original_site, "Pagina gelezen", "", final_url, "ok", f"{len(html)} tekens HTML")
            if canonical_host(normalized_site) == "zwolsetheaters.nl":
                diagnostic_soup = BeautifulSoup(html, "html.parser")
                script_info = [
                    f"type={clean_text(script.get('type') or '')} src={clean_text(script.get('src') or '')} len={len(script.string or script.get_text() or '')}"
                    for script in diagnostic_soup.find_all("script")
                ]
                add_raw("Diagnose", original_site, "Zwolse scripts", "", final_url, "diagnose", " | ".join(script_info[:12]))
                anchor_info = [
                    f"{clean_text(anchor.get_text(' ', strip=True))[:60]} => {clean_text(anchor.get('href') or '')[:140]}"
                    for anchor in diagnostic_soup.find_all("a", href=True)
                ]
                add_raw("Diagnose", original_site, "Zwolse links", "", final_url, "diagnose", " | ".join(anchor_info[:18]))
                embedded_json = [
                    clean_text(script.string or script.get_text() or "")[:300]
                    for script in diagnostic_soup.find_all("script")
                    if "json" in clean_text(script.get("type") or "").lower() or "application/ld+json" in clean_text(script.get("type") or "").lower()
                ]
                add_raw("Diagnose", original_site, "Zwolse JSON", "", final_url, "diagnose", " | ".join(embedded_json[:4]))
                for asset in [script.get("src") for script in diagnostic_soup.find_all("script", src=True) if any(name in script.get("src", "") for name in ("main.js", "helpers.js"))]:
                    try:
                        asset_url = normalized_url(asset, final_url)
                        asset_response = session.get(asset_url, headers=REQUEST_HEADERS, timeout=remaining_timeout(deadline))
                        asset_text = asset_response.text
                        # Find likely API routes and request code rather than generic words in minified bundles.
                        matches = []
                        url_pattern = r"""["'`]((?:https?://|/)[^"'`\s]{1,180})["'`]"""
                        for match in re.finditer(url_pattern, asset_text, re.I):
                            candidate = match.group(1)
                            if re.search(r"(?:api|graphql|agenda|programma|event|performance|voorstelling|show|calendar)", candidate, re.I):
                                matches.append(candidate)
                        request_pattern = r".{0,100}(?:baseURL|axios\.create|fetch\(|graphql|/api/|api/).{0,180}"
                        matches.extend(match.group(0) for match in re.finditer(request_pattern, asset_text, re.I))
                        add_raw("Diagnose", original_site, "Zwolse JS " + urlparse(asset_url).path.rsplit("/", 1)[-1], "", asset_url, "diagnose", " | ".join(list(dict.fromkeys(matches))[:18]))
                    except Exception as asset_error:
                        add_raw("Diagnose", original_site, "Zwolse JS fout", "", asset, "fout", str(asset_error))
        except Exception as exc:
            add_raw("Website", original_site, "Pagina mislukt", "", page_url, "fout", str(exc))
            continue

        if "vera-groningen.nl" in canonical_host(normalized_site):
            # Avoid generic/news parsing for VERA: it can mistake old news articles for events.
            page_events = extract_vera_programme_events(html, final_url, normalized_site, "Website")
        elif canonical_host(normalized_site) == "groningen.uitloper.nu":
            page_events = extract_uitloper_events(html, final_url, normalized_site, "Website")
        elif canonical_host(normalized_site) == "martiniplaza.nl":
            # Only accept small, event-card ancestors so the homepage cannot assign
            # one card's date to a neighboring event or navigation link.
            page_events = extract_venue_agenda_events(html, final_url, normalized_site, "Website")
        elif canonical_host(normalized_site) == "spotgroningen.nl":
            # Try the universal parser first (JSON-LD/Event schema, dated links,
            # event cards and inline date blocks). SPOT's programme page is a
            # modern listing where the venue-specific parser can miss events.
            page_events = parse_page_events(html, final_url, normalized_site, "Website")
            if not page_events:
                add_raw("Parser", original_site, "Universele parser zonder resultaat", "", final_url, "fallback", "Geen events herkend; probeer de SPOT-kaartenparser")
                page_events = extract_venue_agenda_events(html, final_url, normalized_site, "Website")
        elif canonical_host(normalized_site) == "forum.nl":
            # Forum's universal line parser mistakes genres, locations and footer
            # labels for events. Use the overview only to discover actual agenda
            # detail URLs, then parse each detail page.
            page_events = []
            add_raw("Parser", original_site, "Forum detailstrategie", "", final_url, "strategie", "Overzicht overslaan; alleen /nl/agenda/<event> detailpagina's verwerken")
        elif canonical_host(normalized_site) == "kultuuragenda.nl" and urlparse(final_url).path.rstrip("/").lower() == "/agenda":
            # De agenda is alleen een linkdirectory: echte activiteiten staan op /agenda/<id>/<slug>.
            # Voorkom dat algemene labels/kaarttekst als losse evenementen worden opgeslagen.
            page_events = []
            add_raw("Parser", original_site, "Kultuuragenda detailstrategie", "", final_url, "strategie",
                    "Overzicht overslaan; alleen echte /agenda/<id>/<slug> activiteitspagina’s verwerken")
        elif canonical_host(normalized_site) == "paradiso.nl":
            # The landing/agenda page is only a link directory. Never turn its
            # generic heading/summary into an event; individual concert pages are parsed below.
            page_events = []
            add_raw("Parser", original_site, "Paradiso detailstrategie", "", final_url, "strategie", "Agenda-overzicht overslaan; alleen individuele /programma/<slug>/<id> pagina's verwerken")
        elif canonical_host(normalized_site) == "socialdeal.nl":
            page_events = extract_socialdeal_offers(html, final_url, normalized_site)
            if not page_events:
                add_raw("Parser", original_site, "Social Deal-kaarten zonder resultaat", "", final_url, "fallback", "Geen kaartendeals herkend; controleer HTML/kaartstructuur")
        elif canonical_host(normalized_site) == "groupon.nl":
            page_events = extract_groupon_offers(html, final_url, normalized_site)
            if not page_events:
                add_raw("Parser", original_site, "Groupon-kaarten zonder resultaat", "", final_url, "fallback", "Geen deals herkend; controleer HTTP-status, HTML en eventuele blokkade")
        elif canonical_host(normalized_site) == "biosagenda.nl":
            page_events = extract_biosagenda_events(html, final_url, normalized_site, "Website")
        elif canonical_host(normalized_site) in {"vvvameland.nl", "concertgebouw.nl"}:
            page_events = extract_date_listing_events(html, final_url, normalized_site, "Website")
            if not page_events:
                page_events = parse_page_events(html, final_url, normalized_site, "Website")
        elif canonical_host(normalized_site) == "visitgroningen.nl" and "/evenementen" in urlparse(final_url).path.lower():
            # Het overzicht is uitsluitend een bron voor detail-URL's. Alleen pagina's
            # met /evenementen/<slug> worden als echte evenementen verwerkt.
            page_events = []
            add_raw("Parser", original_site, "Visit Groningen detailstrategie", "", final_url, "strategie",
                    "Overzicht overslaan; alleen detail-URL's met /evenementen/<slug> verwerken")
        elif canonical_host(normalized_site) == "zwolsetheaters.nl":
            # Zwolse's listing uses nested /programma/<season>/<slug> detail URLs
            # and date text in the surrounding card, not reliable Event JSON-LD.
            # Parse the listing cards directly, then crawl discovered detail pages.
            page_events = extract_venue_agenda_events(html, final_url, normalized_site, "Website")
            if not page_events:
                page_events = parse_page_events(html, final_url, normalized_site, "Website")
            add_raw("Parser", original_site, "Zwolse programma-parser", "", final_url, "strategie",
                    "Programma-kaarten met datums + geneste detail-URL's; JSON-LD/generieke parser als fallback")
        elif canonical_host(normalized_site) == "simplon.nl":
            page_events = extract_venue_agenda_events(html, final_url, normalized_site, "Website")
        else:
            page_events = parse_page_events(html, final_url, normalized_site, "Website")
        page_events = dedupe_events(page_events)
        if "vera-groningen.nl" not in canonical_host(normalized_site):
            feed_urls.extend(discover_feed_urls(html, final_url, normalized_site))
        if "visitgroningen.nl" in canonical_host(normalized_site) and any(
            part in urlparse(final_url).path.lower() for part in ("/wandelen", "/wandelkaart-groningen")
        ):
            walks = extract_visitgroningen_walks(html, final_url, normalized_site)
            page_events.extend(walks)
            add_raw("Parser", original_site, "Visit Groningen wandelroutes", "", final_url, "gespecialiseerde parser",
                    f"{len(walks)} wandelroutes/activiteiten gevonden")
        if "ibood.com" in canonical_host(normalized_site):
            offers = extract_ibood_offers(html, final_url, normalized_site)
            page_events.extend(offers)
            if offers:
                add_raw("Website", original_site, "iBOOD-aanbiedingen gevonden", TODAY.isoformat(), final_url, "ok", f"{len(offers)} online aanbiedingen")
        events.extend(page_events)
        add_raw("Website", original_site, "Pagina geanalyseerd", "", final_url, "ok", f"{len(page_events)} events/aanbiedingen op deze pagina")
        if progress_callback:
            progress_callback(dedupe_events(events), stage="Pagina gelezen; evenementen verwerken")
        # VERA's programme parser already returns one clean record per event card.
        # Crawling every linked detail page with generic heuristics creates false events.
        if canonical_host(normalized_site) not in {"vera-groningen.nl", "groningen.uitloper.nu", "simplon.nl", "socialdeal.nl"}:
            found_links = collect_event_links(html, final_url, normalized_site)
            if canonical_host(normalized_site) == "forum.nl":
                found_links = [
                    link for link in found_links
                    if re.match(r"^/nl/agenda/(?!$)(?:film/)?[^/]+/?$", urlparse(link).path, re.I)
                    or re.match(r"^/agenda/[^/]+/?$", urlparse(link).path, re.I)
                ]
            if canonical_host(normalized_site) == "paradiso.nl":
                found_links = [
                    link for link in found_links
                    if re.match(r"^/programma/[^/]+/\d+/?$", urlparse(link).path, re.I)
                ]
            detail_links.extend(found_links)

    # Verwerk gevonden openbare feeds rechtstreeks; dit vereist geen API-key of AI.
    for feed_url in unique_urls(feed_urls):
        if time.monotonic() >= deadline or new_event_count(events) >= MAX_EVENTS_PER_SITE:
            break
        try:
            feed_response = session.get(feed_url, headers=REQUEST_HEADERS, timeout=remaining_timeout(deadline), allow_redirects=True)
            if feed_response.status_code >= 400:
                add_raw("RSS/Atom", original_site, "Feed mislukt", "", feed_url, "fout", f"HTTP {feed_response.status_code}")
                continue
            feed_events = parse_feed_events(feed_response.text, feed_response.url, normalized_site)
            events.extend(feed_events)
            add_raw("RSS/Atom", original_site, "Feed geanalyseerd", "", feed_response.url, "ok", f"{len(feed_events)} events uit openbare feed")
            detail_links.extend(event.get("website", "") for event in feed_events)
        except Exception as exc:
            add_raw("RSS/Atom", original_site, "Feed mislukt", "", feed_url, "fout", str(exc))

    serp_links = serpapi_links_for_site(normalized_site)
    for link in serp_links:
        add_raw("SerpAPI", original_site, "Detail-link", "", link, "ingepland", "SerpAPI-link wordt alleen als detailpagina gecontroleerd")

    detail_queue = unique_urls(detail_links + serp_links)
    is_kultuuragenda = "kultuuragenda.nl" in canonical_host(normalized_site)
    is_visitgroningen = canonical_host(normalized_site) == "visitgroningen.nl"
    visited = set()
    for detail_url in detail_queue:
        # Eerst echte activiteitspagina's uitlezen; de overzichtskaarten bevatten vaak te weinig details.
        detail_path = urlparse(detail_url).path
        is_activity_detail = is_kultuuragenda and bool(re.search(r"/agenda/\d+/[^/?#]+", detail_path, re.I))
        is_visitgroningen_detail = is_visitgroningen and bool(re.search(r"/evenementen/[^/?#]+/?$", detail_path, re.I))
        if new_event_count(events) >= MAX_EVENTS_PER_SITE and not is_activity_detail and not is_visitgroningen_detail and "vera-groningen.nl" not in canonical_host(normalized_site):
            break
        if is_kultuuragenda and not is_activity_detail:
            continue
        if is_visitgroningen and not is_visitgroningen_detail:
            continue
        if time.monotonic() >= deadline:
            timed_out = True
            break
        key = detail_url.lower().rstrip("/")
        if key in visited:
            continue
        visited.add(key)
        discovery_source = "SerpAPI" if detail_url in serp_links else "Website"
        try:
            html, final_url = fetch_html(session, detail_url, deadline)
            if canonical_host(normalized_site) == "martiniplaza.nl":
                # Detail pages contain unrelated dates in breadcrumbs, schedules and
                # footer links. Trust structured Event data only; overview cards supply
                # the fallback when a detail page has no Event schema.
                detail_soup = BeautifulSoup(html, "html.parser")
                detail_events = parse_jsonld_events(detail_soup, final_url, normalized_site, discovery_source)
            elif canonical_host(normalized_site) == "forum.nl":
                # Avoid the generic line parser on Forum detail pages: its genres,
                # labels and navigation text are not events.
                detail_soup = BeautifulSoup(html, "html.parser")
                detail_events = parse_jsonld_events(detail_soup, final_url, normalized_site, discovery_source)
                detail_event = extract_detail_event(detail_soup, final_url, normalized_site, discovery_source)
                if detail_event:
                    detail_events.append(detail_event)
            elif canonical_host(normalized_site) == "kultuuragenda.nl":
                # A detail page describes exactly one activity. The generic listing
                # parser can mistake footer/newsletter headings for additional events.
                detail_soup = BeautifulSoup(html, "html.parser")
                detail_event = extract_detail_event(detail_soup, final_url, normalized_site, discovery_source)
                detail_events = [detail_event] if detail_event else []
            else:
                detail_events = parse_page_events(html, final_url, normalized_site, discovery_source)
            before = len(dedupe_events(events))
            events.extend(detail_events)
            after = len(dedupe_events(events))
            add_raw(discovery_source, original_site, "Detailpagina geanalyseerd", "", final_url, "ok", f"{max(0, after - before)} extra events")
        except Exception as exc:
            add_raw(discovery_source, original_site, "Detailpagina mislukt", "", detail_url, "fout", str(exc))

    unique = dedupe_events(events)
    # Treat the selected start date as an inclusive lower bound. If no date was selected,
    # default to today so historical editorial posts are never shown as upcoming events.
    try:
        date_from = date.fromisoformat(INPUT_DATE_FROM[:10]) if INPUT_DATE_FROM else TODAY
    except ValueError:
        date_from = TODAY
    try:
        date_to = date.fromisoformat(INPUT_DATE_TO[:10]) if INPUT_DATE_TO and INPUT_DATE_TO[:10] != "9999-12-31" else None
    except ValueError:
        date_to = None
    before_date_filter = len(unique)
    filtered = []
    for event in unique:
        try:
            event_day = date.fromisoformat(str(event.get("date", ""))[:10])
        except ValueError:
            continue
        if event_day < date_from or (date_to and event_day > date_to):
            continue
        filtered.append(event)
    if before_date_filter != len(filtered):
        add_raw("Filter", original_site, "Datumfilter toegepast", date_from.isoformat(), normalized_site, "ok",
                f"{before_date_filter} gevonden; {len(filtered)} binnen {date_from.isoformat()} t/m {date_to.isoformat() if date_to else 'onbeperkt'}")
    # Count the cap against events not already saved. Existing matches do not consume
    # this scan's allowance, so each source can contribute up to MAX_EVENTS_PER_SITE
    # genuinely new events even when its previous collection is already large.
    unique = dedupe_events([
        event for event in filtered
        if event_key(event) not in existing_event_keys
    ])[:MAX_EVENTS_PER_SITE]
    timed_out = timed_out or time.monotonic() >= deadline
    duration = round(time.monotonic() - started, 2)
    SITE_RESULTS.append(
        {
            "site": original_site,
            "count": len(unique),
            "newCount": 0,
            "durationSeconds": duration,
            "timedOut": timed_out,
        }
    )
    add_raw("Website", original_site, "Klaar", "", normalized_site, "klaar", f"{progress}: {len(unique)} unieke events in {duration} sec")
    return unique


def valid_site_list(values):
    result = []
    seen = set()
    for item in values:
        original = clean_text(item)
        normalized = normalized_url(original)
        key = normalized.lower().rstrip("/")
        if original and normalized and key not in seen:
            seen.add(key)
            result.append(original)
    return result


def input_sites():
    selected = valid_site_list(parse_json_list(INPUT_SITES_RAW))
    if selected:
        return selected
    try:
        with open(SITES_FILE, "r", encoding="utf-8") as handle:
            configured = json.load(handle)
    except (OSError, json.JSONDecodeError):
        configured = []
    return valid_site_list(configured)


def load_previous_payload():
    if not os.path.exists(DATA_FILE) or INPUT_CLEAR_ARCHIVE:
        return {"events": [], "archive": []}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"events": [], "archive": []}
    if isinstance(data, list):
        return {"events": data, "archive": []}
    if isinstance(data, dict):
        return {
            "events": data.get("events") if isinstance(data.get("events"), list) else [],
            "archive": data.get("archive") if isinstance(data.get("archive"), list) else [],
        }
    return {"events": [], "archive": []}


def write_log():
    lines = [
        f"{row.get('source','')}\t{row.get('site','')}\t{row.get('status','')}\t{row.get('title','')}\t{row.get('date','')}\t{row.get('url','')}\t{row.get('rawText','')}"
        for row in RAW_DATA_ROWS
    ]
    with open(LOG_FILE, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + ("\n" if lines else ""))


def save_payload(events, archive, previous_all_keys):
    active_keys = {event_key(event) for event in events}
    for event in events:
        event["isNew"] = event_key(event) not in previous_all_keys

    site_new_counts = {}
    for event in events:
        if event_key(event) in previous_all_keys:
            continue
        host = canonical_host(event.get("website", ""))
        site_new_counts[host] = site_new_counts.get(host, 0) + 1

    for result in SITE_RESULTS:
        result["newCount"] = site_new_counts.get(canonical_host(result.get("site", "")), 0)

    payload = {
        "schemaVersion": 2,
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "events": events,
        "archive": archive,
        "siteResults": SITE_RESULTS,
        "rawLog": RAW_DATA_ROWS[:MAX_RAW_ROWS],
    }
    with open(DATA_FILE, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    write_log()
    print(f"events:{len(events)} archive:{len(archive)} new:{sum(1 for event in events if event.get('isNew'))}")
    return active_keys


SCRAPE_PROGRESS_FILE = "scrape-progress.json"


def publish_scrape_progress(progress):
    """Publish per-site progress so the website can show finished sources before the full scrape ends."""
    with open(SCRAPE_PROGRESS_FILE, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(progress, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        subprocess.run(["git", "add", SCRAPE_PROGRESS_FILE], check=True, capture_output=True, text=True)
        changed = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
        if changed.returncode == 0:
            return
        subprocess.run(["git", "commit", "-m", "Update per-site scrape progress"], check=True, capture_output=True, text=True)
        subprocess.run(["git", "push", "origin", "HEAD:main"], check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        # Progress is best-effort; the final events.json save must still complete.
        detail = getattr(exc, "stderr", "") or str(exc)
        print(f"Could not publish scrape progress: {detail[:500]}")


def main():
    import_serpapi_raw_log()
    sites = input_sites()
    previous = load_previous_payload()
    previous_active = dedupe_events(previous.get("events", []))
    previous_archive = dedupe_events(previous.get("archive", []))
    previous_all = dedupe_events(previous_active + previous_archive)
    previous_all_keys = {event_key(event) for event in previous_all}

    all_events = []
    total_sites = len(sites)
    progress = {
        "runId": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "completedCount": 0,
        "totalSites": total_sites,
        "currentSite": "",
        "currentSiteIndex": 0,
        "completedSites": [],
        "events": [],
        "siteResults": [],
        "rawLog": [],
        "status": "running"
    }
    publish_scrape_progress(progress)
    for index, site in enumerate(sites, start=1):
        before_count = len(all_events)
        progress["currentSite"] = site
        progress["currentSiteIndex"] = index
        progress["stage"] = "Websitepagina ophalen"
        progress["completedCount"] = index - 1
        progress["status"] = "running"
        progress["lastUpdatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        publish_scrape_progress(progress)

        def publish_partial_site_progress(partial_events, stage="Evenementen verwerken"):
            progress["stage"] = stage
            progress["events"] = dedupe_events([*all_events, *partial_events])
            progress["siteResults"] = list(SITE_RESULTS)
            progress["rawLog"] = RAW_DATA_ROWS[-MAX_RAW_ROWS:]
            progress["currentSite"] = site
            progress["currentSiteIndex"] = index
            progress["lastSiteEventCount"] = len(dedupe_events(partial_events))
            progress["lastUpdatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            progress["status"] = "running"
            publish_scrape_progress(progress)

        try:
            all_events.extend(scrape_site(site, index, total_sites, progress_callback=publish_partial_site_progress, existing_event_keys=previous_all_keys))
        except Exception as exc:
            add_raw("Website", site, "Site mislukt", "", site, "fout", str(exc))
            SITE_RESULTS.append({"site": site, "count": 0, "newCount": 0, "durationSeconds": 0, "timedOut": False})
        site_events = dedupe_events(all_events[before_count:])
        progress["completedCount"] = index
        progress["lastCompletedSite"] = site
        progress["stage"] = "Website klaar; volgende website voorbereiden"
        progress["completedSites"].append(site)
        progress["events"] = dedupe_events(all_events)
        progress["siteResults"] = list(SITE_RESULTS)
        progress["rawLog"] = RAW_DATA_ROWS[-MAX_RAW_ROWS:]
        progress["lastCompletedSite"] = site
        progress["lastCompletedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        progress["lastSiteEventCount"] = len(site_events)
        progress["status"] = "running" if index < total_sites else "finishing"
        publish_scrape_progress(progress)

    scraped_events = dedupe_events(all_events)
    selected_hosts = {canonical_host(site) for site in sites if canonical_host(site)}
    successful_hosts = {
        canonical_host(row.get("site", ""))
        for row in RAW_DATA_ROWS
        if row.get("status") == "ok" and row.get("title") == "Pagina gelezen"
    }
    # Refresh adds newly discovered events instead of replacing the existing
    # per-site collection. MAX_EVENTS_PER_SITE limits each new scan; it does not
    # cap the total number of saved events for a site. Keep previous events unless
    # the user explicitly requested clearing the archive/data.
    retained_previous = []
    if not INPUT_CLEAR_ARCHIVE:
        retained_previous = list(previous_all)
        # Remove the first, generic-parser BiosAgenda results after a successful
        # specialized scan; those rows may be news snippets rather than screenings.
        if "biosagenda.nl" in selected_hosts and "biosagenda.nl" in successful_hosts:
            retained_previous = [
                event for event in retained_previous
                if canonical_host(event.get("website", "")) != "biosagenda.nl"
            ]
    active_events = dedupe_events([*retained_previous, *scraped_events])
    active_keys = {event_key(event) for event in active_events}

    if INPUT_CLEAR_ARCHIVE:
        archive = []
    else:
        preserved = [
            event for event in previous_all
            if event_key(event) not in active_keys
            and not (
                canonical_host(event.get("website", "")) in selected_hosts
                and canonical_host(event.get("website", "")) in successful_hosts
            )
        ]
        archive = dedupe_events(preserved)[:1000]

    save_payload(active_events, archive, previous_all_keys)
    progress["status"] = "completed"
    progress["completedCount"] = total_sites
    progress["events"] = dedupe_events(scraped_events)
    progress["siteResults"] = list(SITE_RESULTS)
    progress["rawLog"] = RAW_DATA_ROWS[-MAX_RAW_ROWS:]
    progress["finishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    publish_scrape_progress(progress)


if __name__ == "__main__":
    main()
