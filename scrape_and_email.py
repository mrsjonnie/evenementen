import json
import os
import re
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


def normalize_event(event):
    title = event.get('title', '').strip()
    location = event.get('location', '').strip()
    return {
        'title': title,
        'type': event.get('type', 'evenement'),
        'date': event.get('date', ''),
        'time': event.get('time', ''),
        'location': location,
        'locationLink': event.get('locationLink', ''),
        'cost': event.get('cost', 'Zie website'),
        'description': event.get('description', ''),
        'image': event.get('image', f'https://picsum.photos/seed/{quote_plus(title or location)}/800/500'),
        'website': event.get('website', '#'),
        'source': event.get('source', 'Onbekend'),
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

                    if j < len(lines) and lines[j] == 'meerdere data':
                        extra = ' (meerdere data)'
                        j += 1

                    description = lines[j].replace('.. »', '').strip() if j < len(lines) else ''

                    if title.lower() in {'uitgelicht', 'toon info', 'bezoek website', 'populair', 'mei', 'juni', 'juli', 'maart / april', 'augustus en verder'}:
                        continue

                    item = normalize_event({
                        'title': title,
                        'type': 'evenement',
                        'date': date + extra,
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

        except Exception as e:
            log(f'Fout bij scrapen Uitzinnig ({try_url}): {e}')

    return events


def manual_events():
    return [
        normalize_event({
            'title': 'Groninger Museum',
            'type': 'museum',
            'date': 'Hele jaar',
            'location': 'Groningen',
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
