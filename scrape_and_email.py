import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DATA_FILE = "events.json"
LOG_FILE = "scrape_log.txt"
INPUT_REGION = os.getenv("INPUT_REGION", "Groningen").strip() or "Groningen"
INPUT_SITES_RAW = os.getenv("INPUT_SITES", "[]").strip() or "[]"
INPUT_DATE_FROM = os.getenv("INPUT_DATE_FROM", "").strip()
INPUT_DATE_TO = os.getenv("INPUT_DATE_TO", "").strip()
INPUT_CLEAR_ARCHIVE = os.getenv("INPUT_CLEAR_ARCHIVE", "").strip().lower() in {"1", "true", "yes", "ja"}
BASE_HEADERS = {"User-Agent": "Mozilla/5.0"}


def env_int(name, default, minimum, maximum):
    try:
        value = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


MAX_EVENTS_PER_SITE = env_int("INPUT_MAX_EVENTS_PER_SITE", 20, 20, 100)
SITE_TIME_LIMIT_SECONDS = env_int("INPUT_SITE_TIME_LIMIT_SECONDS", 20, 20, 60)
SITE_RESULTS = []
MONTHS = "januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december|jan|feb|mrt|apr|jun|jul|aug|sep|sept|okt|nov|dec|january|february|march|april|may|june|july|august|september|october|november|december"
DATE_RE = re.compile(rf"^\d{{1,2}}(?:\s+t/m\s+\d{{1,2}})?\s+(?:{MONTHS})$", re.I)
DATE_IN_TEXT_RE = re.compile(rf"\b\d{{1,2}}(?:\s+t/m\s+\d{{1,2}})?\s+(?:{MONTHS})(?:\s+20\d{{2}})?\b", re.I)
ISO_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
SCORE_RE = re.compile(r"^\d,\d$")
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
TIME_RANGE_RE = re.compile(r"^\d{1,2}:\d{2}(?:\s*[-–]\s*\d{1,2}:\d{2})?$")
WEEKDAY_RE = re.compile(r"^(maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
DATE_TITLE_RE = re.compile(rf"^(?:maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag|monday|tuesday|wednesday|thursday|friday|saturday|sunday)?\s*\d{{1,2}}(?:\s+t/m\s+\d{{1,2}})?\s+(?:{MONTHS})(?:\s+20\d{{2}})?$", re.I)
PRICE_OR_ACTION_RE = re.compile(r"^(gratis|€\s*\d|eur\s*\d|tickets?|koop ticket|meer info|lees meer|uitverkocht|sold out|reeds gestart)", re.I)
TITLE_NOISE_RE = re.compile(r"^(coming up|highlights|lees meer|koop ticket|sold out|support|friday show|ubbo x zienema|raw postpunk from|in 20\d{2},|this winter)", re.I)
SPOT_LISTING_RE = re.compile(r"^(ma|di|wo|do|vr|za|zo)\s*(\d{1,2})\s*([a-z]{3,9})\b\s+(.+)$", re.I)
VERA_DATE_RE = re.compile(rf"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+\d{{1,2}}\s+(?:{MONTHS})\b", re.I)
VERA_TYPE_RE = re.compile(r"\b(Mainstage|Downstage|Zienema|Dansen)\s*\|", re.I)
COUNTRY_CODE_RE = re.compile(r"\b(CAN|NL|USA|BEL|GRN|INT|UK|DE|FR|IT|ES)\b")
BLOCKED_TITLES = {
    "uitgelicht",
    "toon info",
    "bezoek website",
    "populair",
    "mei",
    "juni",
    "juli",
    "maart / april",
    "augustus en verder",
}
EVENT_WORDS_RE = re.compile(
    r"\b(event|evenement|agenda|programma|concert|festival|theater|film|bioscoop|markt|workshop|lezing|expo|expositie|tentoonstelling|voorstelling|activiteit|activiteiten|tickets|uitgaan|muziek|cabaret|dans|opera|museum|kermis|kinderen)\b",
    re.I,
)
BAD_LINK_WORDS_RE = re.compile(
    r"\b(contact|privacy|cookie|voorwaarden|login|account|nieuwsbrief|facebook|instagram|linkedin|tickets?\s+verkopen)\b",
    re.I,
)
COMMON_EVENT_PATHS = [
    "/agenda",
    "/agenda/agenda-overzicht",
    "/nl/agenda",
    "/nl/agenda/agenda-overzicht",
    "/evenementen",
    "/evenement",
    "/events",
    "/events/all",
    "/event",
    "/programma",
    "/program",
    "/activiteiten",
    "/activiteiten/agenda",
    "/activiteiten-en-evenementen",
    "/calendar",
    "/kalender",
    "/whats-on",
    "/wat-te-doen",
    "/nl/doen",
    "/nl/doen/uitgaan",
]
MONTH_NUMBERS = {
    "januari": 1,
    "februari": 2,
    "maart": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "augustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mrt": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "okt": 10,
    "nov": 11,
    "dec": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "april": 4,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def log(message):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")


def clean_text(value):
    text = (
        str(value or "")
        .replace("\u00c3\u00a2\u00c2\u0082\u00c2\u00ac", "\u20ac")
        .replace("\u00e2\u0082\u00ac", "\u20ac")
        .replace("\u20ac", "\u20ac")
        .replace("&euro;", "\u20ac")
        .replace("&#8364;", "\u20ac")
        .replace("\u00c2\u00b7", "-")
        .replace("\u00b7", "-")
    )
    text = re.sub(r"\bEUR\s*(?=\d)", "\u20ac ", text, flags=re.I)
    text = re.sub(r"\bEUR\b", "\u20ac", text, flags=re.I)
    text = re.sub(r"\u20ac\s*(?=\d)", "\u20ac ", text)
    return text.strip()


def format_event_title(value):
    title = clean_text(value)
    title = re.sub(r"\s+", " ", title).strip(" -|")
    title = COUNTRY_CODE_RE.sub("", title)
    title = re.sub(r"\s{2,}", " ", title).strip(" -|")
    if not title:
        return ""

    small_words = {"de", "het", "een", "en", "van", "voor", "met", "the", "and", "of", "in", "on", "to"}
    words = []
    for index, word in enumerate(title.split(" ")):
        if not word:
            continue
        if re.search(r"[A-Z]{2,}|[-/]", word):
            words.append(word)
            continue
        lower = word.lower()
        if index > 0 and lower in small_words:
            words.append(lower)
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def normalize_date_value(value):
    text = clean_text(value)
    if not text:
        return ""

    iso = ISO_DATE_RE.search(text)
    if iso:
        return iso.group(0)

    match = re.search(
        rf"\b(\d{{1,2}})(?:\s+t/m\s+\d{{1,2}})?\s+({MONTHS})(?:\s+(20\d{{2}}))?\b",
        text,
        re.I,
    )
    if not match:
        return text

    day = int(match.group(1))
    month = MONTH_NUMBERS.get(match.group(2).lower())
    year = int(match.group(3) or datetime.now().year)
    if not month:
        return text

    try:
        candidate = datetime(year, month, day)
        today = datetime.now()
        if not match.group(3) and candidate.date() < today.date():
            candidate = datetime(year + 1, month, day)
        return candidate.strftime("%Y-%m-%d")
    except ValueError:
        return text


def event_date_sort_value(event):
    date = clean_text(event.get("date"))
    if not ISO_DATE_RE.fullmatch(date):
        return "9999-12-31"
    return date


def event_in_requested_period(event):
    date = event_date_sort_value(event)
    if date == "9999-12-31":
        return False
    if INPUT_DATE_FROM and date < INPUT_DATE_FROM:
        return False
    if INPUT_DATE_TO and date > INPUT_DATE_TO:
        return False
    return True


def today_iso():
    return datetime.now().date().isoformat()


def is_past_event(event):
    date = event_date_sort_value(event)
    return date != "9999-12-31" and date < today_iso()


def active_copy(event):
    copy = dict(event)
    copy.pop("archivedAt", None)
    return copy


def sort_events_for_request(events):
    return sorted(
        events,
        key=lambda event: (
            0 if event_in_requested_period(event) else 1,
            event_date_sort_value(event),
            clean_text(event.get("title")).lower(),
        ),
    )


def should_geocode(location):
    text = clean_text(location)
    if not text:
        return False
    if re.match(r"^[\w.-]+\.[a-z]{2,}$", text, re.I):
        return False
    if len(text) > 120:
        return False
    return True


def normalize_title(value):
    text = clean_text(value).lower()
    text = re.sub(rf"^(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)\s+\d{{1,2}}\s+(?:{MONTHS})\s+", "", text)
    text = re.split(r"\b(mainstage|downstage|zienema|dansen)\b|\bticket\b|\bdoors\b|\bstart\b|\bsold out\b|\bkoop ticket\b", text, 1)[0]
    text = re.sub(r"\b20\d{2}\b", "", text)
    text = re.sub(r"^(concert|theater|film|bioscoop|markt|workshop|event|evenement):\s*", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def normalize_location(value):
    text = clean_text(value).lower()
    text = re.sub(r"\b(start|startpunt|bij|diverse|locaties|locatie)\b", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def event_key(event):
    title = normalize_title(event.get("title"))
    date = clean_text(event.get("date"))
    location = normalize_location(event.get("location"))
    if len(title) > 8:
        return f"{date}|{title}"
    return f"{date}|{title}|{location}"


def event_quality(event):
    score = 0
    title = clean_text(event.get("title"))
    if event.get("website") and event.get("website") != "#":
        score += 12
    if event.get("image"):
        score += 4
    if len(clean_text(event.get("description"))) > 60:
        score += 4
    if event.get("time"):
        score += 2
    if event.get("lat") and event.get("lon"):
        score += 2
    if event.get("source") and event.get("source") not in {"Onbekend", "Manual"}:
        score += 1
    if title and len(title) <= 90:
        score += 3
    if re.search(r"\b(ticket|doors|start|koop ticket|sold out)\b", title, re.I):
        score -= 5
    return score


def is_valid_web_url(value):
    url = clean_text(value)
    if not url or url == "#":
        return True

    try:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and "." in parsed.netloc
    except Exception:
        return False


def event_date_conflicts(event):
    date = clean_text(event.get("date"))
    if not ISO_DATE_RE.fullmatch(date):
        return False

    haystack = clean_text(f"{event.get('title')} {event.get('description')}")
    for match in DATE_IN_TEXT_RE.finditer(haystack):
        explicit = normalize_date_value(match.group(0))
        if ISO_DATE_RE.fullmatch(explicit) and explicit != date:
            return True
    return False


def is_valid_event(event):
    title = clean_text(event.get("title"))
    title_norm = normalize_title(title)
    date = clean_text(event.get("date"))
    location = clean_text(event.get("location"))

    if not title or len(title_norm) < 4:
        return False
    if title.lower() in BLOCKED_TITLES:
        return False
    if TITLE_NOISE_RE.search(title):
        return False
    if DATE_RE.match(title) or SCORE_RE.match(title):
        return False
    if DATE_TITLE_RE.match(title):
        return False
    if len(title) > 140 and re.search(r"\b(lees meer|koop ticket|sold out)\b", title, re.I):
        return False
    if not date:
        return False
    if event_date_conflicts(event):
        return False
    if not location or normalize_location(location) in {"provincie groningen", "groningen provincie"}:
        return False
    if not is_valid_web_url(event.get("website")):
        return False

    return True


def configured_sites_from_file():
    try:
        with open("sites.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        log(f"Kon sites.json niet lezen: {exc}")
        return []

    if not isinstance(data, list):
        log("sites.json bevat geen lijst met websites.")
        return []

    return data


def normalize_site_list(raw_sites):
    sites = []
    seen = set()
    for site in raw_sites:
        url = clean_text(site)
        if not url:
            continue
        if not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I) and re.match(r"^[\w.-]+\.[a-z]{2,}", url, re.I):
            url = f"https://{url}"
        if not is_valid_web_url(url):
            log(f"Extra website overgeslagen door ongeldig adres: {site}")
            continue
        key = url.lower().rstrip("/")
        if key not in seen:
            seen.add(key)
            sites.append(url)
    return sites[:30]


def parse_input_sites():
    try:
        raw_sites = json.loads(INPUT_SITES_RAW)
        if not isinstance(raw_sites, list):
            raw_sites = []
    except Exception:
        raw_sites = [part.strip() for part in INPUT_SITES_RAW.split(",")]

    sites = normalize_site_list(raw_sites)
    if sites:
        return sites

    log("Geen losse websites meegegeven; ik gebruik alle websites uit sites.json.")
    return normalize_site_list(configured_sites_from_file())


def dedupe_events(events):
    by_key = {}
    duplicates = 0
    rejected = 0
    for event in events:
        if not is_valid_event(event):
            rejected += 1
            continue

        key = event_key(event)
        current = by_key.get(key)
        if current is None:
            by_key[key] = event
            continue
        duplicates += 1
        if event_quality(event) > event_quality(current):
            by_key[key] = event
    if duplicates:
        log(f"Dubbele evenementen samengevoegd: {duplicates}")
    if rejected:
        log(f"Onbetrouwbare of onvolledige regels overgeslagen: {rejected}")
    return list(by_key.values())


def load_existing_events():
    if not os.path.exists(DATA_FILE):
        return [], []

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        log(f"Kon bestaande events.json niet lezen: {exc}")
        return [], []

    if isinstance(data, list):
        return data, []

    if isinstance(data, dict):
        active = data.get("events") if isinstance(data.get("events"), list) else []
        archive = data.get("archive") if isinstance(data.get("archive"), list) else []
        return active, archive

    return [], []


def get_coordinates(location):
    if not location:
        return None, None

    try:
        url = f"https://nominatim.openstreetmap.org/search?format=json&q={quote_plus(location)}"
        headers = {"User-Agent": "Mozilla/5.0 (Evenementen Scraper)"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as exc:
        log(f"Fout bij geocoding voor {location}: {exc}")

    return None, None


def normalize_event(event):
    title = format_event_title(event.get("title"))
    location = clean_text(event.get("location"))
    lat = event.get("lat")
    lon = event.get("lon")

    if (lat is None or lon is None) and should_geocode(location):
        lat, lon = get_coordinates(location)
        time.sleep(1)

    location_link = clean_text(event.get("locationLink"))
    if not location_link and lat and lon:
        location_link = f"https://www.google.com/maps?q={lat},{lon}"

    image = clean_text(event.get("image"))
    if not image:
        image = f"https://picsum.photos/seed/{quote_plus(title or location)}/800/500"

    website = clean_text(event.get("website")) or "#"
    source = clean_text(event.get("source"))
    if not source or source.lower() in {"manual", "onbekend", "bron onbekend"}:
        source = urlparse(website).netloc.replace("www.", "") if is_valid_web_url(website) and website != "#" else ""

    date = normalize_date_value(event.get("date"))
    return {
        "title": title,
        "type": clean_text(event.get("type")) or "evenement",
        "date": date,
        "time": clean_text(event.get("time")),
        "location": location,
        "lat": lat,
        "lon": lon,
        "locationLink": location_link,
        "cost": clean_text(event.get("cost")) or "Zie website",
        "description": clean_text(event.get("description")),
        "image": image,
        "website": website,
        "source": source or "Onbekend",
        "periodLabel": clean_text(event.get("periodLabel")) or date,
        "isPermanent": bool(event.get("isPermanent", False)),
    }


def collect_title_links(soup, base_url):
    links = {}
    for anchor in soup.find_all("a", href=True):
        title = clean_text(anchor.get_text(" ", strip=True))
        key = normalize_title(title)
        if len(key) < 8:
            continue

        href = clean_text(anchor.get("href"))
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue

        links.setdefault(key, urljoin(base_url, href))
    return links


def default_location_for_site(site_url):
    host = urlparse(site_url).netloc.lower().replace("www.", "")
    known = {
        "forum.nl": "Forum Groningen",
        "vera-groningen.nl": "VERA Groningen",
        "spotgroningen.nl": "SPOT Groningen",
        "simplon.nl": "Simplon Groningen",
        "groningermuseum.nl": "Groninger Museum",
        "martiniplaza.nl": "Martiniplaza Groningen",
        "hedon-zwolle.nl": "Hedon Zwolle",
        "paradiso.nl": "Paradiso Amsterdam",
        "concertgebouw.nl": "Het Concertgebouw Amsterdam",
    }
    return known.get(host, urlparse(site_url).netloc)


def site_host(site_url):
    return urlparse(site_url).netloc.lower().replace("www.", "")


def slug_title_from_url(url):
    path = urlparse(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    slug = re.sub(r"-\d+$", "", slug)
    slug = re.sub(r"[-_]+", " ", slug)
    slug = re.sub(r"\b\d{1,2}\b", "", slug)
    return format_event_title(slug)


def is_detail_event_url(page_url):
    parsed = urlparse(page_url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if "spotgroningen.nl" in host:
        return path.startswith("/programma/") and path != "/programma"
    if "vera-groningen.nl" in host:
        return "post_type=events" in parsed.query or "/events/" in path
    return False


def link_for_title(title_links, title, fallback_url):
    key = normalize_title(title)
    if key in title_links:
        return title_links[key]
    for link_key, url in title_links.items():
        if key and (key in link_key or link_key in key):
            return url
    return fallback_url


def first_meta(soup, names):
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and clean_text(tag.get("content")):
            return clean_text(tag.get("content"))
    return ""


def seconds_left(deadline):
    return max(0, deadline - time.monotonic())


def enough_time_left(deadline):
    return seconds_left(deadline) > 0.25


def timeout_for(deadline, default_timeout):
    if deadline is None:
        return default_timeout
    remaining = seconds_left(deadline)
    if remaining <= 0.25:
        raise TimeoutError("Tijdslimiet voor deze website bereikt")
    return min(default_timeout, max(0.5, remaining))


def fetch_soup(page_url, timeout=18, deadline=None):
    timeout = timeout_for(deadline, timeout)
    response = requests.get(page_url, headers=BASE_HEADERS, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "application/xhtml" not in content_type and content_type:
        raise ValueError(f"Geen HTML: {content_type}")
    return BeautifulSoup(response.text, "html.parser")


def site_seed_urls(site_url):
    parsed = urlparse(site_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    urls = [site_url.rstrip("/")]
    urls.extend(f"{base}{path}" for path in COMMON_EVENT_PATHS)

    seen = set()
    result = []
    for candidate in urls:
        key = candidate.lower().rstrip("/")
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def page_title(soup):
    heading = soup.find(["h1", "h2"])
    if heading:
        return clean_text(heading.get_text(" ", strip=True))
    title = soup.find("title")
    return clean_text(title.get_text(" ", strip=True)) if title else ""


def first_date_in_text(text):
    match = ISO_DATE_RE.search(text)
    if match:
        return match.group(0)
    match = DATE_IN_TEXT_RE.search(text)
    return clean_text(match.group(0)) if match else ""


def date_from_section_label(text):
    value = clean_text(text).lower()
    today = datetime.now().date()
    if value == "vandaag":
        return today.isoformat()
    if value == "morgen":
        return (today + timedelta(days=1)).isoformat()

    value = WEEKDAY_RE.sub("", value).strip(" ,:-")
    date_text = first_date_in_text(value)
    if date_text:
        return normalize_date_value(date_text)
    return ""


def has_event_signal(text, href=""):
    haystack = f"{text} {href}"
    return bool(EVENT_WORDS_RE.search(haystack) or DATE_IN_TEXT_RE.search(haystack) or ISO_DATE_RE.search(haystack))


def is_bad_link(text, href):
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return True
    if BAD_LINK_WORDS_RE.search(f"{text} {href}"):
        return True
    if TITLE_NOISE_RE.search(clean_text(text)) or DATE_TITLE_RE.match(clean_text(text)):
        return True
    if len(normalize_title(text)) < 4:
        return True
    return False


def candidate_event_links(soup, site_url):
    candidates = []
    seen = set()
    base_host = urlparse(site_url).netloc.lower()
    page_path = urlparse(site_url).path.replace("-", " ").replace("_", " ").replace("/", " ")
    page_has_event_context = bool(EVENT_WORDS_RE.search(page_path))

    for anchor in soup.find_all("a", href=True):
        text = clean_text(anchor.get_text(" ", strip=True))
        href = clean_text(anchor.get("href"))
        if is_bad_link(text, href):
            continue

        absolute = urljoin(site_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc.lower() != base_host:
            continue
        key = absolute.split("#", 1)[0]
        if key.rstrip("/") == site_url.rstrip("/"):
            continue
        signal = f"{text} {parsed.path.replace('-', ' ').replace('_', ' ').replace('/', ' ')}"
        if not page_has_event_context and not has_event_signal(signal, absolute):
            continue
        if page_has_event_context and not (has_event_signal(signal, absolute) or len(normalize_title(text)) >= 4):
            continue

        if key in seen:
            continue
        seen.add(key)
        candidates.append((text, key))
        if len(candidates) >= 24:
            break

    return candidates


def event_from_detail_page(detail_url, fallback_title, deadline=None):
    try:
        timeout = timeout_for(deadline, 3)
        response = requests.get(detail_url, headers=BASE_HEADERS, timeout=timeout)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        text = clean_text(soup.get_text(" ", strip=True))
        title = first_meta(soup, ["og:title", "twitter:title"]) or page_title(soup) or fallback_title
        title = re.sub(r"\s+[|-]\s+.*$", "", title).strip()
        date = first_meta(soup, ["event:start_time", "article:published_time"])[:10]
        if not date:
            date = first_date_in_text(text)
        location = first_meta(soup, ["event:location"])
        if not location:
            address = soup.find(attrs={"itemprop": "address"})
            location = clean_text(address.get_text(" ", strip=True)) if address else ""
        if not location:
            location = default_location_for_site(detail_url)
        description = first_meta(soup, ["og:description", "description", "twitter:description"])
        if not description:
            description = text[:220]
        image = first_meta(soup, ["og:image", "twitter:image"])

        return normalize_event(
            {
                "title": title,
                "type": "evenement",
                "date": date,
                "location": location,
                "description": description,
                "image": urljoin(detail_url, image) if image else "",
                "website": detail_url,
                "cost": "Zie website",
                "source": urlparse(detail_url).netloc or "Extra website",
                "periodLabel": date,
            }
        )
    except requests.exceptions.RequestException as exc:
        log(f"Detailpagina overgeslagen {detail_url}: {exc}")
    except Exception as exc:
        log(f"Fout bij detailpagina {detail_url}: {exc}")

    return None


def events_from_listing_text(soup, site_url):
    events = []
    blocks = soup.find_all(["article", "li", "section", "div"], limit=600)

    for block in blocks:
        text = clean_text(block.get_text(" ", strip=True))
        if len(text) < 25 or len(text) > 700:
            continue
        date = first_date_in_text(text)
        if not date:
            continue
        link = block.find("a", href=True)
        title = clean_text(link.get_text(" ", strip=True)) if link else ""
        if not title:
            heading = block.find(["h1", "h2", "h3", "h4"])
            title = clean_text(heading.get_text(" ", strip=True)) if heading else text[:90]
        if BAD_LINK_WORDS_RE.search(title) or len(normalize_title(title)) < 4:
            continue
        website = urljoin(site_url, link.get("href")) if link else site_url
        item = normalize_event(
            {
                "title": title,
                "type": "evenement",
                "date": date,
                "location": default_location_for_site(site_url),
                "description": text[:260],
                "website": website,
                "cost": "Zie website",
                "source": urlparse(site_url).netloc or "Extra website",
                "periodLabel": date,
            }
        )
        if is_valid_event(item):
            events.append(item)
        if len(events) >= 12:
            break

    return events


def spot_title_from_text(value, url=""):
    slug_title = slug_title_from_url(url)
    if len(normalize_title(slug_title)) >= 5:
        return slug_title

    title = clean_text(value)
    title = re.sub(r"\b(laatste kaarten|uitverkocht|net bevestigd|bijna uitverkocht|extra show)\b.*$", "", title, flags=re.I).strip(" -|")
    title = re.sub(r"\s{2,}", " ", title)
    return format_event_title(title[:170])


def events_from_spot_listing(soup, site_url):
    host = urlparse(site_url).netloc.lower()
    if "spotgroningen.nl" not in host:
        return []

    events = []
    for anchor in soup.find_all("a", href=True):
        href = clean_text(anchor.get("href"))
        absolute = urljoin(site_url, href).split("#", 1)[0]
        parsed = urlparse(absolute)
        if "spotgroningen.nl" not in parsed.netloc.lower() or "/programma/" not in parsed.path:
            continue

        text = clean_text(anchor.get_text(" ", strip=True))
        match = SPOT_LISTING_RE.match(text)
        if not match:
            continue

        date = normalize_date_value(f"{match.group(2)} {match.group(3)}")
        title = spot_title_from_text(match.group(4), absolute)
        if not title:
            continue

        item = normalize_event(
            {
                "title": title,
                "type": "evenement",
                "date": date,
                "location": "SPOT Groningen",
                "description": text[:260],
                "website": absolute,
                "cost": "Zie website",
                "source": "www.spotgroningen.nl",
                "periodLabel": date,
            }
        )
        if is_valid_event(item):
            events.append(item)
        if len(events) >= MAX_EVENTS_PER_SITE:
            break

    return dedupe_events(events)


def vera_title_from_text(value):
    title = clean_text(value)
    type_match = VERA_TYPE_RE.search(title)
    if type_match:
        title = title[:type_match.start()]
    title = re.split(r"\b(Koop ticket|Sold out|Gratis|Ticket:|doors:|start:)\b", title, 1, flags=re.I)[0]
    title = title.strip(" -|")
    return format_event_title(title[:180])


def events_from_vera_listing(soup, site_url):
    if "vera-groningen.nl" not in site_host(site_url):
        return []

    events = []
    for anchor in soup.find_all("a", href=True):
        href = clean_text(anchor.get("href"))
        absolute = urljoin(site_url, href).split("#", 1)[0]
        parsed = urlparse(absolute)
        if "vera-groningen.nl" not in parsed.netloc.lower():
            continue

        text = clean_text(anchor.get_text(" ", strip=True))
        date_match = VERA_DATE_RE.search(text)
        if not date_match:
            continue

        date = normalize_date_value(date_match.group(0))
        rest = text[date_match.end():].strip()
        title = vera_title_from_text(rest)
        if not title:
            continue

        type_match = VERA_TYPE_RE.search(rest)
        cost_match = re.search(r"Ticket:\s*([^|]+)", rest, re.I)
        start_match = re.search(r"start:\s*(\d{1,2}:\d{2})", rest, re.I)
        item = normalize_event(
            {
                "title": title,
                "type": clean_text(type_match.group(1)) if type_match else "evenement",
                "date": date,
                "time": start_match.group(1) if start_match else "",
                "location": "VERA Groningen",
                "description": text[:320],
                "website": absolute,
                "cost": clean_text(cost_match.group(1)) if cost_match else "Zie website",
                "source": "www.vera-groningen.nl",
                "periodLabel": date,
            }
        )
        if is_valid_event(item):
            events.append(item)
        if len(events) >= MAX_EVENTS_PER_SITE:
            break

    return dedupe_events(events)


def context_type(lines):
    for line in lines:
        if TIME_RE.search(line) or PRICE_OR_ACTION_RE.search(line):
            continue
        if EVENT_WORDS_RE.search(line):
            return clean_text(line)[:60]
    return "evenement"


def context_cost(lines):
    for line in lines:
        value = clean_text(line)
        if PRICE_OR_ACTION_RE.search(value):
            if re.search(r"(ticket|meer info|uitverkocht|reeds gestart)", value, re.I):
                continue
            return value.replace("EUR", "€").replace("eur", "€")
    return "Zie website"


def context_time(lines):
    for line in lines:
        match = TIME_RE.search(line)
        if match:
            return match.group(0)
    return ""


def is_context_noise(line):
    value = clean_text(line)
    lowered = value.lower()
    if lowered in BLOCKED_TITLES:
        return True
    if TITLE_NOISE_RE.search(value):
        return True
    if lowered in {"filter", "datum", "soort", "locatie", "sluiten", "tickets", "meer info"}:
        return True
    if TIME_RANGE_RE.match(value) or PRICE_OR_ACTION_RE.search(value):
        return True
    if DATE_RE.match(value) or DATE_TITLE_RE.match(value) or SCORE_RE.match(value):
        return True
    if len(normalize_title(value)) < 4:
        return True
    return False


def events_from_contextual_lines(soup, site_url):
    events = []
    title_links = collect_title_links(soup, site_url)
    lines = [clean_text(item) for item in soup.stripped_strings if clean_text(item)]
    current_date = ""

    for index, line in enumerate(lines):
        section_date = date_from_section_label(line)
        if section_date:
            current_date = section_date
            continue

        if not current_date or is_context_noise(line):
            continue

        lookahead = lines[index + 1:index + 7]
        has_context = any(
            TIME_RE.search(item) or PRICE_OR_ACTION_RE.search(item) or EVENT_WORDS_RE.search(item)
            for item in lookahead
        )
        if not has_context:
            continue

        title = clean_text(line)
        website = link_for_title(title_links, title, site_url)
        item = normalize_event(
            {
                "title": title,
                "type": context_type(lookahead),
                "date": current_date,
                "time": context_time(lookahead),
                "location": default_location_for_site(site_url),
                "description": " ".join([title, *lookahead[:4]])[:260],
                "website": website,
                "cost": context_cost(lookahead),
                "source": urlparse(site_url).netloc or "Extra website",
                "periodLabel": current_date,
            }
        )
        if is_valid_event(item):
            events.append(item)
        if len(events) >= 20:
            break

    return dedupe_events(events)


def jsonld_nodes(data):
    if isinstance(data, list):
        for item in data:
            yield from jsonld_nodes(item)
        return

    if not isinstance(data, dict):
        return

    yield data
    graph = data.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            yield from jsonld_nodes(item)


def node_type_includes(node, expected):
    value = node.get("@type")
    if isinstance(value, list):
        return any(str(item).lower() == expected for item in value)
    return str(value or "").lower() == expected


def first_value(value):
    if isinstance(value, list):
        return first_value(value[0]) if value else ""
    return value


def extract_image(value):
    image = first_value(value)
    if isinstance(image, dict):
        return clean_text(image.get("url") or image.get("contentUrl"))
    return clean_text(image)


def extract_location(value):
    value = first_value(value)
    if isinstance(value, str):
        return clean_text(value)
    if not isinstance(value, dict):
        return ""

    parts = [clean_text(value.get("name"))]
    address = value.get("address")
    if isinstance(address, str):
        parts.append(clean_text(address))
    elif isinstance(address, dict):
        parts.extend(
            clean_text(address.get(key))
            for key in ["streetAddress", "addressLocality", "addressRegion"]
        )

    return ", ".join(part for part in parts if part)


def extract_price(value):
    offer = first_value(value)
    if not isinstance(offer, dict):
        return ""
    price = clean_text(offer.get("price"))
    currency = clean_text(offer.get("priceCurrency"))
    if price and currency:
        return f"{currency} {price}"
    return price


def split_start_date(value):
    start = clean_text(value)
    if not start:
        return "", ""
    if "T" not in start:
        return start[:10], ""
    date_part, time_part = start.split("T", 1)
    return date_part[:10], time_part[:5]


def event_from_jsonld(node, site_url):
    if not node_type_includes(node, "event"):
        return None

    title = clean_text(node.get("name") or node.get("headline"))
    date, event_time = split_start_date(node.get("startDate"))
    location = extract_location(node.get("location"))
    website = clean_text(node.get("url")) or site_url

    return normalize_event(
        {
            "title": title,
            "type": "evenement",
            "date": date,
            "time": event_time,
            "location": location,
            "description": clean_text(node.get("description")),
            "image": extract_image(node.get("image")),
            "website": urljoin(site_url, website),
            "cost": extract_price(node.get("offers")) or "Zie website",
            "source": urlparse(site_url).netloc or "Extra website",
            "periodLabel": date,
        }
    )


def scrape_structured_site(site_url):
    started = time.monotonic()
    deadline = started + SITE_TIME_LIMIT_SECONDS
    events = []
    link_candidates = []
    timed_out = False

    for page_url in site_seed_urls(site_url):
        if not enough_time_left(deadline):
            timed_out = True
            break
        try:
            soup = fetch_soup(page_url, timeout=2.5, deadline=deadline)
        except requests.exceptions.RequestException as exc:
            log(f"Websitepagina niet bereikbaar {page_url}: {exc}")
            continue
        except TimeoutError as exc:
            timed_out = True
            log(f"Website overgeslagen door tijdslimiet {page_url}: {exc}")
            break
        except Exception as exc:
            log(f"Websitepagina overgeslagen {page_url}: {exc}")
            continue

        before = len(events)
        for script in soup.find_all("script", type=lambda value: value and "ld+json" in value):
            try:
                data = json.loads(script.string or script.get_text() or "{}")
            except Exception:
                continue

            for node in jsonld_nodes(data):
                item = event_from_jsonld(node, site_url)
                if item and is_valid_event(item):
                    events.append(item)
                if len(events) >= MAX_EVENTS_PER_SITE:
                    break
            if len(events) >= MAX_EVENTS_PER_SITE:
                break

        host = site_host(page_url)
        if "spotgroningen.nl" in host:
            source_events = events_from_spot_listing(soup, page_url)
            events.extend(source_events)
            if not source_events and is_detail_event_url(page_url):
                item = event_from_detail_page(page_url, slug_title_from_url(page_url), deadline=deadline)
                if item and is_valid_event(item):
                    events.append(item)
        elif "vera-groningen.nl" in host:
            source_events = events_from_vera_listing(soup, page_url)
            events.extend(source_events)
            if not source_events and is_detail_event_url(page_url):
                item = event_from_detail_page(page_url, slug_title_from_url(page_url), deadline=deadline)
                if item and is_valid_event(item):
                    events.append(item)
        else:
            events.extend(events_from_listing_text(soup, page_url))
            events.extend(events_from_contextual_lines(soup, page_url))
            link_candidates.extend(candidate_event_links(soup, page_url))

        added = len(events) - before
        if added:
            log(f"{added} kandidaat-evenementen gevonden op {page_url}")
        if len(events) >= MAX_EVENTS_PER_SITE:
            break

    seen_links = set()
    unique_links = []
    for title, detail_url in link_candidates:
        key = detail_url.lower().rstrip("/")
        if key not in seen_links:
            seen_links.add(key)
            unique_links.append((title, detail_url))

    for title, detail_url in unique_links[:24]:
        if len(events) >= MAX_EVENTS_PER_SITE:
            break
        if not enough_time_left(deadline):
            timed_out = True
            break
        item = event_from_detail_page(detail_url, title, deadline=deadline)
        if item and is_valid_event(item):
            events.append(item)

    events = sort_events_for_request(dedupe_events(events))[:MAX_EVENTS_PER_SITE]
    SITE_RESULTS.append(
        {
            "site": site_url,
            "count": len(events),
            "newCount": 0,
            "durationSeconds": round(time.monotonic() - started, 2),
            "timedOut": timed_out,
            "eventKeys": [event_key(event) for event in events],
        }
    )
    if events:
        log(f"Gevonden op extra website {site_url}: {len(events)}")
    else:
        log(f"Geen betrouwbare evenementen gevonden op {site_url}")

    return events


def scrape_extra_sites(sites):
    events = []
    for site_url in sites:
        events.extend(scrape_structured_site(site_url))
    return sort_events_for_request(dedupe_events(events))


def scrape_uitzinnig(region="Groningen"):
    events = []
    urls = [
        f"https://www.uitzinnig.nl/evenement/{quote_plus(region.lower())}.aspx",
        "https://www.uitzinnig.nl/evenement/5/groningen.aspx",
    ]

    for try_url in urls:
        try:
            response = requests.get(try_url, headers=BASE_HEADERS, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            title_links = collect_title_links(soup, try_url)
            lines = [x.strip() for x in soup.stripped_strings if x.strip()]

            for i, line in enumerate(lines):
                if (
                    i + 3 < len(lines)
                    and lines[i + 1].startswith("Provincie Groningen,")
                    and SCORE_RE.match(lines[i + 2])
                    and DATE_RE.match(lines[i + 3])
                ):
                    title = clean_text(line)
                    location = clean_text(lines[i + 1].replace("Provincie Groningen,", ""))
                    date = clean_text(lines[i + 3])
                    j = i + 4
                    extra = ""
                    event_time = ""

                    if j < len(lines) and lines[j] == "meerdere data":
                        extra = " (meerdere data)"
                        j += 1

                    for k in range(i + 4, min(len(lines), i + 10)):
                        if re.match(r"^\d{1,2}:\d{2}", lines[k]):
                            event_time = clean_text(lines[k])
                            break

                    description = clean_text(lines[j].replace(".. \u00bb", "")) if j < len(lines) else ""

                    if title.lower() in BLOCKED_TITLES:
                        continue

                    website = title_links.get(normalize_title(title), try_url)

                    item = normalize_event(
                        {
                            "title": title,
                            "type": "evenement",
                            "date": date + extra,
                            "time": event_time,
                            "location": location,
                            "description": description,
                            "website": website,
                            "source": "Uitzinnig",
                        }
                    )

                    if item["title"]:
                        events.append(item)

            if events:
                log(f"Gevonden: {len(events)} evenementen op {try_url}")
                return dedupe_events(events)

        except requests.exceptions.RequestException as exc:
            log(f"Fout bij HTTP-verzoek voor {try_url}: {exc}")
        except Exception as exc:
            log(f"Onverwachte fout bij scrapen Uitzinnig ({try_url}): {exc}")

    return events


def manual_events():
    return [
        normalize_event(
            {
                "title": "Groninger Museum",
                "type": "museum",
                "date": "Hele jaar",
                "location": "Groningen",
                "lat": 53.2193,
                "lon": 6.5671,
                "description": "Moderne kunst en wisselende tentoonstellingen.",
                "website": "https://www.groningermuseum.nl",
                "source": "groningermuseum.nl",
                "isPermanent": True,
            }
        ),
        normalize_event(
            {
                "title": "Fort Bourtange",
                "type": "historie",
                "date": "Hele jaar",
                "location": "Bourtange",
                "lat": 53.0167,
                "lon": 7.1833,
                "description": "Historische vesting met demonstraties en musea.",
                "website": "https://www.bourtange.nl",
                "source": "bourtange.nl",
                "isPermanent": True,
            }
        ),
    ]


def archive_old_events(previous_active, previous_archive, new_active):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    active_keys = {event_key(event) for event in new_active}
    archived_by_key = {}

    for event in previous_archive:
        key = event_key(event)
        if key not in active_keys:
            archived_by_key[key] = event

    for event in previous_active:
        key = event_key(event)
        if key not in active_keys and key not in archived_by_key:
            archived = dict(event)
            archived["archivedAt"] = now
            archived_by_key[key] = archived

    archive = sorted(
        archived_by_key.values(),
        key=lambda item: clean_text(item.get("archivedAt")) or clean_text(item.get("date")),
        reverse=True,
    )
    return archive[:500]


def public_site_results():
    result = []
    for item in SITE_RESULTS:
        result.append(
            {
                "site": item.get("site"),
                "count": item.get("count", 0),
                "newCount": item.get("newCount", 0),
                "durationSeconds": item.get("durationSeconds", 0),
                "timedOut": item.get("timedOut", False),
            }
        )
    return result


def event_from_selected_site(event, selected_hosts):
    if not selected_hosts:
        return True
    host = site_host(clean_text(event.get("website")))
    source = clean_text(event.get("source")).lower().replace("www.", "")
    return host in selected_hosts or source in selected_hosts


def save_events_to_json(active_events, archive):
    payload = {
        "schemaVersion": 2,
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "events": active_events,
        "archive": archive,
        "siteResults": public_site_results(),
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"Opgeslagen: {len(active_events)} actief, {len(archive)} gearchiveerd in {DATA_FILE}")


def main():
    log("=== Start scraping ===")
    log(f"Input region={INPUT_REGION}")
    log("AI-bronnen worden in deze automatische workflow niet gebruikt.")
    if INPUT_CLEAR_ARCHIVE:
        log("Clear archive gevraagd: bestaande actieve lijst en archief worden genegeerd.")

    previous_active, previous_archive = ([], []) if INPUT_CLEAR_ARCHIVE else load_existing_events()
    extra_sites = parse_input_sites()
    if extra_sites:
        log(f"Alleen geselecteerde websites worden gescand: {len(extra_sites)}")
        scraped = scrape_extra_sites(extra_sites)
    else:
        scraped = scrape_uitzinnig(INPUT_REGION)
    selected_hosts = {site_host(site) for site in extra_sites}
    keep_existing = [
        active_copy(event)
        for event in previous_active
        if not is_past_event(event) and event_from_selected_site(event, selected_hosts)
    ]
    fixed_events = [] if extra_sites else manual_events()
    active = sort_events_for_request(dedupe_events(scraped + keep_existing + fixed_events))
    archive = archive_old_events(previous_active, previous_archive, active)
    previous_keys = {event_key(event) for event in previous_active}
    for result in SITE_RESULTS:
        result["newCount"] = sum(1 for key in result.get("eventKeys", []) if key not in previous_keys)

    save_events_to_json(active, archive)
    log("=== Scraping voltooid ===")


if __name__ == "__main__":
    main()
