import html as html_lib
import json
import os
import re
import time
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

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Evenementen Scraper; +https://github.com/mrsjonnie/evenementen)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.7",
}


def env_int(name, default, minimum, maximum):
    try:
        value = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


MAX_EVENTS_PER_SITE = env_int("INPUT_MAX_EVENTS_PER_SITE", 20, 20, 100)
SITE_TIME_LIMIT_SECONDS = env_int("INPUT_SITE_TIME_LIMIT_SECONDS", 20, 20, 60)
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
    "hedon-zwolle.nl": "Hedon Zwolle",
    "paradiso.nl": "Paradiso Amsterdam",
    "vvvameland.nl": "Ameland",
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
    if len(tail) > 95:
        words = tail.split()
        tail = " ".join(words[: min(7, len(words))])
    return clean_title(tail)


def classify_type(text, host=""):
    lower = clean_text(text).lower()
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
    for anchor in links:
        url = normalized_url(anchor.get("href"), page_url)
        if url and same_host(url, site_url) and not BAD_URL_RE.search(url):
            return url
    return normalized_url(page_url)


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


def normalize_event(raw, site_url):
    website = normalized_url(raw.get("website"), site_url)
    title = clean_title(raw.get("title") or "")
    event_date = parse_date_text(raw.get("date") or raw.get("dateText") or "")
    if not event_date and raw.get("contextDate"):
        event_date = parse_date_text("", raw.get("contextDate"))
    if not title_is_usable(title) or not event_date or not website:
        return None

    location = clean_text(raw.get("location") or "") or host_default_location(site_url)
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
    if event.get("discoverySource") == "Website":
        score += 1
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


def extract_detail_event(soup, page_url, site_url, discovery_source):
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


def parse_page_events(html, page_url, site_url, discovery_source):
    soup = BeautifulSoup(html, "html.parser")
    events = []
    events.extend(parse_jsonld_events(soup, page_url, site_url, discovery_source))
    events.extend(extract_anchor_events(soup, page_url, site_url, discovery_source))
    events.extend(extract_card_events(soup, page_url, site_url, discovery_source))
    detail = extract_detail_event(soup, page_url, site_url, discovery_source)
    if detail:
        events.append(detail)
        add_raw(discovery_source, site_url, detail["title"], detail["date"], detail["website"], "event uit detailpagina", soup.get_text(" ", strip=True)[:420])
    events.extend(extract_line_events(soup, page_url, site_url, discovery_source))
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
    for anchor in soup.find_all("a", href=True):
        url = normalized_url(anchor.get("href"), page_url)
        if not url or not same_host(url, site_url):
            continue
        text = clean_text(anchor.get_text(" ", strip=True) or anchor.get("title") or anchor.get("aria-label"))
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
    urls = [exact]
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
    return max(1.0, min(7.0, deadline - time.monotonic()))


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


def scrape_site(site_url):
    original_site = clean_text(site_url)
    normalized_site = normalized_url(original_site)
    started = time.monotonic()
    deadline = started + SITE_TIME_LIMIT_SECONDS
    session = requests.Session()
    events = []
    detail_links = []
    timed_out = False

    add_raw("Website", original_site, "Start", "", normalized_site, "start", f"max {MAX_EVENTS_PER_SITE} events, max {SITE_TIME_LIMIT_SECONDS} seconden")

    for page_url in start_urls_for_site(normalized_site):
        if len(dedupe_events(events)) >= MAX_EVENTS_PER_SITE or time.monotonic() >= deadline:
            timed_out = time.monotonic() >= deadline
            break
        try:
            html, final_url = fetch_html(session, page_url, deadline)
            add_raw("Website", original_site, "Pagina gelezen", "", final_url, "ok", f"{len(html)} tekens HTML")
        except Exception as exc:
            add_raw("Website", original_site, "Pagina mislukt", "", page_url, "fout", str(exc))
            continue

        page_events = parse_page_events(html, final_url, normalized_site, "Website")
        events.extend(page_events)
        add_raw("Website", original_site, "Pagina geanalyseerd", "", final_url, "ok", f"{len(page_events)} events op deze pagina")
        detail_links.extend(collect_event_links(html, final_url, normalized_site))

    serp_links = serpapi_links_for_site(normalized_site)
    for link in serp_links:
        add_raw("SerpAPI", original_site, "Detail-link", "", link, "ingepland", "SerpAPI-link wordt alleen als detailpagina gecontroleerd")

    detail_queue = unique_urls(detail_links + serp_links)
    visited = set()
    for detail_url in detail_queue:
        if len(dedupe_events(events)) >= MAX_EVENTS_PER_SITE:
            break
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
            detail_events = parse_page_events(html, final_url, normalized_site, discovery_source)
            before = len(dedupe_events(events))
            events.extend(detail_events)
            after = len(dedupe_events(events))
            add_raw(discovery_source, original_site, "Detailpagina geanalyseerd", "", final_url, "ok", f"{max(0, after - before)} extra events")
        except Exception as exc:
            add_raw(discovery_source, original_site, "Detailpagina mislukt", "", detail_url, "fout", str(exc))

    unique = dedupe_events(events)[:MAX_EVENTS_PER_SITE]
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
    add_raw("Website", original_site, "Klaar", "", normalized_site, "klaar", f"{len(unique)} unieke events in {duration} sec")
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


def main():
    import_serpapi_raw_log()
    sites = input_sites()
    previous = load_previous_payload()
    previous_active = dedupe_events(previous.get("events", []))
    previous_archive = dedupe_events(previous.get("archive", []))
    previous_all = dedupe_events(previous_active + previous_archive)
    previous_all_keys = {event_key(event) for event in previous_all}

    all_events = []
    for site in sites:
        try:
            all_events.extend(scrape_site(site))
        except Exception as exc:
            add_raw("Website", site, "Site mislukt", "", site, "fout", str(exc))
            SITE_RESULTS.append({"site": site, "count": 0, "newCount": 0, "durationSeconds": 0, "timedOut": False})

    active_events = dedupe_events(all_events)
    active_keys = {event_key(event) for event in active_events}

    if INPUT_CLEAR_ARCHIVE:
        archive = []
    else:
        preserved = [event for event in previous_all if event_key(event) not in active_keys]
        archive = dedupe_events(preserved)[:1000]

    save_payload(active_events, archive, previous_all_keys)


if __name__ == "__main__":
    main()
