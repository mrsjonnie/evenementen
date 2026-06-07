import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DATA_FILE = "events.json"
LOG_FILE = "scrape_log.txt"
INPUT_REGION = os.getenv("INPUT_REGION", "Groningen").strip() or "Groningen"
INPUT_SITES_RAW = os.getenv("INPUT_SITES", "[]").strip() or "[]"
INPUT_CLEAR_ARCHIVE = os.getenv("INPUT_CLEAR_ARCHIVE", "").strip().lower() in {"1", "true", "yes", "ja"}
BASE_HEADERS = {"User-Agent": "Mozilla/5.0"}
MONTHS = "januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december"
DATE_RE = re.compile(rf"^\d{{1,2}}(?:\s+t/m\s+\d{{1,2}})?\s+(?:{MONTHS})$", re.I)
DATE_IN_TEXT_RE = re.compile(rf"\b\d{{1,2}}(?:\s+t/m\s+\d{{1,2}})?\s+(?:{MONTHS})(?:\s+20\d{{2}})?\b", re.I)
ISO_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
SCORE_RE = re.compile(r"^\d,\d$")
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


def is_valid_event(event):
    title = clean_text(event.get("title"))
    title_norm = normalize_title(title)
    date = clean_text(event.get("date"))
    location = clean_text(event.get("location"))

    if not title or len(title_norm) < 4:
        return False
    if title.lower() in BLOCKED_TITLES:
        return False
    if DATE_RE.match(title) or SCORE_RE.match(title):
        return False
    if not date:
        return False
    if not location or normalize_location(location) in {"provincie groningen", "groningen provincie"}:
        return False
    if not is_valid_web_url(event.get("website")):
        return False

    return True


def parse_input_sites():
    try:
        raw_sites = json.loads(INPUT_SITES_RAW)
        if not isinstance(raw_sites, list):
            raw_sites = []
    except Exception:
        raw_sites = [part.strip() for part in INPUT_SITES_RAW.split(",")]

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
    title = clean_text(event.get("title"))
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
        "website": clean_text(event.get("website")) or "#",
        "source": clean_text(event.get("source")) or "Onbekend",
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


def first_meta(soup, names):
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and clean_text(tag.get("content")):
            return clean_text(tag.get("content"))
    return ""


def fetch_soup(page_url, timeout=18):
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


def has_event_signal(text, href=""):
    haystack = f"{text} {href}"
    return bool(EVENT_WORDS_RE.search(haystack) or DATE_IN_TEXT_RE.search(haystack) or ISO_DATE_RE.search(haystack))


def is_bad_link(text, href):
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return True
    if BAD_LINK_WORDS_RE.search(f"{text} {href}"):
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


def event_from_detail_page(detail_url, fallback_title):
    try:
        response = requests.get(detail_url, headers=BASE_HEADERS, timeout=15)
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
            location = urlparse(detail_url).netloc
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
                "location": urlparse(site_url).netloc,
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
    events = []
    link_candidates = []

    for page_url in site_seed_urls(site_url):
        try:
            soup = fetch_soup(page_url)
        except requests.exceptions.RequestException as exc:
            log(f"Websitepagina niet bereikbaar {page_url}: {exc}")
            continue
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
                if len(events) >= 40:
                    break
            if len(events) >= 40:
                break

        events.extend(events_from_listing_text(soup, page_url))
        link_candidates.extend(candidate_event_links(soup, page_url))

        added = len(events) - before
        if added:
            log(f"{added} kandidaat-evenementen gevonden op {page_url}")
        if len(events) >= 40:
            break

    seen_links = set()
    unique_links = []
    for title, detail_url in link_candidates:
        key = detail_url.lower().rstrip("/")
        if key not in seen_links:
            seen_links.add(key)
            unique_links.append((title, detail_url))

    for title, detail_url in unique_links[:50]:
        if len(events) >= 40:
            break
        item = event_from_detail_page(detail_url, title)
        if item and is_valid_event(item):
            events.append(item)

    events = dedupe_events(events)[:40]
    if events:
        log(f"Gevonden op extra website {site_url}: {len(events)}")
    else:
        log(f"Geen betrouwbare evenementen gevonden op {site_url}")

    return events


def scrape_extra_sites(sites):
    events = []
    for site_url in sites:
        events.extend(scrape_structured_site(site_url))
    return dedupe_events(events)


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
                "source": "Manual",
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
                "source": "Manual",
                "isPermanent": True,
            }
        ),
    ]


def archive_old_events(previous_active, previous_archive, new_active):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    active_keys = {event_key(event) for event in new_active}
    archived_by_key = {}

    for event in previous_archive:
        archived_by_key[event_key(event)] = event

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


def save_events_to_json(active_events, archive):
    payload = {
        "schemaVersion": 2,
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "events": active_events,
        "archive": archive,
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
    scraped = scrape_uitzinnig(INPUT_REGION) + scrape_extra_sites(extra_sites)
    active = dedupe_events(scraped + manual_events())
    archive = archive_old_events(previous_active, previous_archive, active)

    save_events_to_json(active, archive)
    log("=== Scraping voltooid ===")


if __name__ == "__main__":
    main()
