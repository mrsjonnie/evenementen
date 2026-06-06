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
BASE_HEADERS = {"User-Agent": "Mozilla/5.0"}
MONTHS = "januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december"
DATE_RE = re.compile(rf"^\d{{1,2}}(?:\s+t/m\s+\d{{1,2}})?\s+(?:{MONTHS})$", re.I)
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


def log(message):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")


def clean_text(value):
    return (
        str(value or "")
        .replace("\u00c3\u00a2\u00c2\u0082\u00c2\u00ac", "EUR")
        .replace("\u00e2\u0082\u00ac", "EUR")
        .replace("\u20ac", "EUR")
        .replace("\u00c2\u00b7", "-")
        .replace("\u00b7", "-")
        .strip()
    )


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

    if lat is None or lon is None:
        lat, lon = get_coordinates(location)
        time.sleep(1)

    location_link = clean_text(event.get("locationLink"))
    if not location_link and lat and lon:
        location_link = f"https://www.google.com/maps?q={lat},{lon}"

    image = clean_text(event.get("image"))
    if not image:
        image = f"https://picsum.photos/seed/{quote_plus(title or location)}/800/500"

    date = clean_text(event.get("date"))
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

    previous_active, previous_archive = load_existing_events()
    scraped = scrape_uitzinnig(INPUT_REGION)
    active = dedupe_events(scraped + manual_events())
    archive = archive_old_events(previous_active, previous_archive, active)

    save_events_to_json(active, archive)
    log("=== Scraping voltooid ===")


if __name__ == "__main__":
    main()
