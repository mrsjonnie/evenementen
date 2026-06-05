import json
import os
import re
import time
from datetime import datetime
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

DATA_FILE = 'events.json'
LOG_FILE = 'scrape_log.txt'
INPUT_REGION = os.getenv('INPUT_REGION', 'Groningen').strip() or 'Groningen'
BASE_HEADERS = {'User-Agent': 'Mozilla/5.0'}
KNOWN_DISTANCES = {
    'groningen': {'groningen': 0, 'haren': 10, 'leek': 22, 'slochteren': 18, 'bourtange': 58, 'oude pekela': 35, 'bellingwolde': 48, 'eenrum': 25, 'lauwersoog': 53, 'beerta': 42, 'assen': 32, 'drachten': 38},
    'assen': {'groningen': 32, 'haren': 28, 'leek': 34, 'slochteren': 46, 'bourtange': 84, 'oude pekela': 64, 'bellingwolde': 76, 'eenrum': 56, 'lauwersoog': 84, 'beerta': 72, 'assen': 0, 'drachten': 36},
    'drachten': {'groningen': 38, 'haren': 44, 'leek': 20, 'slochteren': 55, 'bourtange': 95, 'oude pekela': 72, 'bellingwolde': 86, 'eenrum': 49, 'lauwersoog': 61, 'beerta': 82, 'assen': 36, 'drachten': 0}
}
MONTHS = 'januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december'
DATE_RE = re.compile(rf'^\d{{1,2}}(?:\s+t/m\s+\d{{1,2}})?\s+(?:{MONTHS})$', re.I)
SCORE_RE = re.compile(r'^\d,\d$')

def log(message):
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")

def get_coordinates(location):
    if not location:
        return None, None

    try:
        url = f"https://nominatim.openstreetmap.org/search?format=json&q={quote_plus(location)}"
        headers = {'User-Agent': 'Mozilla/5.0 (Evenementen Scraper)'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except Exception as e:
        log(f"Fout bij geocoding voor {location}: {e}")

    return None, None

def normalize_event(event):
    title = event.get('title', '').strip()
    location = event.get('location', '').strip()

    # Voeg coördinaten toe als ze niet bestaan
    lat = event.get('lat')
    lon = event.get('lon')
    if lat is None or lon is None:
        lat, lon = get_coordinates(location)
        time.sleep(1)  # Rate limiting voor Nominatim
        if lat is not None and lon is not None:
            event['lat'] = lat
            event['lon'] = lon

    # Voeg locationLink toe
    location_link = event.get('locationLink', '')
    if not location_link and lat and lon:
        location_link = f'https://www.google.com/maps?q={lat},{lon}'

    # Voeg image toe
    image = event.get('image', '')
    if not image:
        image = f'https://picsum.photos/seed/{quote_plus(title or location)}/800/500'

    # Voeg periodLabel toe
    date = event.get('date', '')
    if date == 'Hele jaar':
        period_label = 'Hele jaar'
    else:
        period_label = date

    return {
        'title': title,
        'type': event.get('type', 'evenement'),
        'date': date,
        'time': event.get('time', ''),
        'location': location,
        'lat': lat,
        'lon': lon,
        'locationLink': location_link,
        'cost': event.get('cost', 'Zie website'),
        'description': event.get('description', ''),
        'image': image,
        'website': event.get('website', '#'),
        'source': event.get('source', 'Onbekend'),
        'periodLabel': period_label,
        'isPermanent': event.get('isPermanent', False)
    }

def scrape_uitzinnig(region='Groningen'):
    events = []
    urls = [
        f'https://www.uitzinnig.nl/evenement/{quote_plus(region.lower())}.aspx',
        'https://www.uitzinnig.nl/evenement/5/groningen.aspx'
    ]

    for try_url in urls:
        try:
            response = requests.get(try_url, headers=BASE_HEADERS, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            lines = [x.strip() for x in soup.stripped_strings if x.strip()]

            for i, line in enumerate(lines):
                if i + 3 < len(lines) and lines[i+1].startswith('Provincie Groningen,') and SCORE_RE.match(lines[i+2]) and DATE_RE.match(lines[i+3]):
                    title = line
                    location = lines[i+1].replace('Provincie Groningen,', '').strip()
                    date = lines[i+3].strip()
                    j = i + 4
                    extra = ''
                    time = ""

                    if j < len(lines) and lines[j] == 'meerdere data':
                        extra = ' (meerdere data)'
                        j += 1

                    # Zoek naar tijd
                    k = i + 4
                    while k < len(lines) and k < i + 10:
                        if re.match(r'^\d{1,2}:\d{2}', lines[k]):
                            time = lines[k].strip()
                            break
                        k += 1

                    description = lines[j].replace('.. »', '').strip() if j < len(lines) else ''

                    if title.lower() in {'uitgelicht', 'toon info', 'bezoek website', 'populair', 'mei', 'juni', 'juli', 'maart / april', 'augustus en verder'}:
                        continue

                    log(f'Gevonden evenement: {title} in {location} op {date}')

                    item = normalize_event({
                        'title': title,
                        'type': 'evenement',
                        'date': date + extra,
                        'time': time,
                        'location': location,
                        'description': description,
                        'website': try_url,
                        'source': 'Uitzinnig'
                    })

                    if item['title'] and item['title'] not in [e['title'] for e in events]:
                        events.append(item)

            if events:
                log(f'Gevonden: {len(events)} evenementen op {try_url}')
                return events

        except requests.exceptions.RequestException as e:
            log(f'Fout bij HTTP-verzoek voor {try_url}: {e}')
            continue
        except Exception as e:
            log(f'Onverwachte fout bij scrapen Uitzinnig ({try_url}): {e}')
            continue

    return events

def manual_events():
    return [
        normalize_event({
            'title': 'Groninger Museum',
            'type': 'museum',
            'date': 'Hele jaar',
            'location': 'Groningen',
            'lat': 53.2193,
            'lon': 6.5671,
            'description': 'Moderne kunst en wisselende tentoonstellingen.',
            'website': 'https://www.groningermuseum.nl',
            'source': 'Manual',
            'isPermanent': True
        }),
        normalize_event({
            'title': 'Fort Bourtange',
            'type': 'historie',
            'date': 'Hele jaar',
            'location': 'Bourtange',
            'lat': 53.0167,
            'lon': 7.1833,
            'description': 'Historische vesting met demonstraties en musea.',
            'website': 'https://www.bourtange.nl',
            'source': 'Manual',
            'isPermanent': True
        })
    ]

def save_events_to_json(events):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    log(f'Opgeslagen: {len(events)} evenementen in {DATA_FILE}')

def main():
    log('=== Start scraping ===')
    log(f'Input region={INPUT_REGION}')

    all_events = scrape_uitzinnig(INPUT_REGION) + manual_events()

    unique = []
    seen = set()
    for event in all_events:
        key = (
            event.get('title', '').strip().lower(),
            event.get('location', '').strip().lower(),
            event.get('date', '').strip().lower()
        )
        if key not in seen:
            seen.add(key)
            unique.append(event)

    save_events_to_json(unique)
    log('=== Scraping voltooid ===')

if __name__ == '__main__':
    main()
