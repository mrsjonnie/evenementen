import json
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

DATA_FILE = 'events.json'
LOG_FILE = 'scrape_log.txt'
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
EMAIL_FROM = os.getenv('EMAIL_FROM', '')
EMAIL_TO = [x.strip() for x in os.getenv('EMAIL_TO', '').split(',') if x.strip()]
INPUT_REGION = os.getenv('INPUT_REGION', 'Groningen').strip() or 'Groningen'
INPUT_DATE = os.getenv('INPUT_DATE', '').strip()
INPUT_RADIUS = os.getenv('INPUT_RADIUS', '140').strip() or '140'
INPUT_CATEGORY = os.getenv('INPUT_CATEGORY', 'all').strip().lower() or 'all'
INPUT_QUERY = os.getenv('INPUT_QUERY', '').strip().lower()
BASE_HEADERS = {'User-Agent': 'Mozilla/5.0'}
KNOWN_DISTANCES = {'groningen':{'groningen':0,'haren':10,'leek':22,'slochteren':18,'bourtange':58,'oude pekela':35,'bellingwolde':48,'eenrum':25,'lauwersoog':53,'beerta':42,'assen':32,'drachten':38},'assen':{'groningen':32,'haren':28,'leek':34,'slochteren':46,'bourtange':84,'oude pekela':64,'bellingwolde':76,'eenrum':56,'lauwersoog':84,'beerta':72,'assen':0,'drachten':36},'drachten':{'groningen':38,'haren':44,'leek':20,'slochteren':55,'bourtange':95,'oude pekela':72,'bellingwolde':86,'eenrum':49,'lauwersoog':61,'beerta':82,'assen':36,'drachten':0}}
MONTHS = 'januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december'
DATE_RE = re.compile(rf'^\d{{1,2}}(?:\s+t/m\s+\d{{1,2}})?\s+(?:{MONTHS})$', re.I)
SCORE_RE = re.compile(r'^\d,\d$')

def log(message):
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")

def get_distance(region, location):
    region = (region or '').lower().strip()
    location = (location or '').lower().strip()
    if region in KNOWN_DISTANCES and location in KNOWN_DISTANCES[region]: return KNOWN_DISTANCES[region][location]
    if region == location: return 0
    return None

def normalize_event(event):
    title = event.get('title', '').strip()
    location = event.get('location', '').strip()
    return {'title': title,'type': event.get('type', 'evenement'),'date': event.get('date', ''),'time': event.get('time', ''),'location': location,'locationLink': event.get('locationLink', ''),'cost': event.get('cost', 'Zie website'),'description': event.get('description', ''),'image': event.get('image', f'https://picsum.photos/seed/{quote_plus(title or location)}/800/500'),'website': event.get('website', '#'),'source': event.get('source', 'Onbekend'),'isPermanent': event.get('isPermanent', False)}

def scrape_uitzinnig(region='Groningen'):
    events = []
    urls = [f'https://www.uitzinnig.nl/evenement/{quote_plus(region.lower())}.aspx','https://www.uitzinnig.nl/evenement/5/groningen.aspx']
    for try_url in urls:
        try:
            response = requests.get(try_url, headers=BASE_HEADERS, timeout=20)
            soup = BeautifulSoup(response.text, 'html.parser')
            lines = [x.strip() for x in soup.stripped_strings if x.strip()]
            for i, line in enumerate(lines):
                if i + 3 < len(lines) and lines[i+1].startswith('Provincie Groningen,') and SCORE_RE.match(lines[i+2]) and DATE_RE.match(lines[i+3]):
                    title = line
                    location = lines[i+1].replace('Provincie Groningen,', '').strip()
                    date = lines[i+3].strip()
                    j = i + 4
                    extra = ''
                    if j < len(lines) and lines[j] == 'meerdere data': extra, j = ' (meerdere data)', j + 1
                    description = lines[j].replace('.. »', '').strip() if j < len(lines) else ''
                    if title.lower() in {'uitgelicht','toon info','bezoek website','populair','mei','juni','juli','maart / april','augustus en verder'}: continue
                    item = normalize_event({'title': title,'type': 'evenement','date': date + extra,'location': location,'description': description,'website': try_url,'source': 'Uitzinnig'})
                    if item['title'] and item['title'] not in [e['title'] for e in events]: events.append(item)
            if events:
                log(f'Gevonden: {len(events)} evenementen op {try_url}')
                return events
        except Exception as e:
            log(f'Fout bij scrapen Uitzinnig ({try_url}): {e}')
    return events

def manual_events():
    return [normalize_event({'title':'Groninger Museum','type':'museum','date':'Hele jaar','location':'Groningen','description':'Moderne kunst en wisselende tentoonstellingen.','website':'https://www.groningermuseum.nl','source':'Manual','isPermanent':True}), normalize_event({'title':'Fort Bourtange','type':'historie','date':'Hele jaar','location':'Bourtange','description':'Historische vesting met demonstraties en musea.','website':'https://www.bourtange.nl','source':'Manual','isPermanent':True})]

def matches_filters(event):
    hay = f"{event.get('title','')} {event.get('description','')} {event.get('location','')}".lower()
    if INPUT_QUERY and INPUT_QUERY not in hay: return False
    if INPUT_CATEGORY != 'all' and event.get('type', '').lower() != INPUT_CATEGORY: return False
    if INPUT_DATE and INPUT_DATE not in str(event.get('date', '')): return False
    try: radius = int(float(INPUT_RADIUS))
    except Exception: radius = 140
    dist = get_distance(INPUT_REGION, event.get('location', ''))
    if INPUT_REGION and dist is not None and dist > radius: return False
    if INPUT_REGION and dist is None and INPUT_REGION.lower() not in hay: return False
    return True

def save_events_to_json(events):
    with open(DATA_FILE, 'w', encoding='utf-8') as f: json.dump(events, f, ensure_ascii=False, indent=2)
    log(f'Opgeslagen: {len(events)} evenementen in {DATA_FILE}')

def generate_email_html(events):
    today = datetime.now().strftime('%A, %d %B %Y')
    html = '<!DOCTYPE html><html><head><style>body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; } .header { background: #2563eb; color: white; padding: 20px; text-align: center; } .event { border-left: 4px solid #2563eb; padding: 15px; margin: 15px 0; background: #f9f9f9; }</style></head><body>'
    html += f'<div class="header"><h1>Evenementen rond {INPUT_REGION} – {today}</h1></div>'
    for event in events[:10]: html += f'<div class="event"><h3>{event['"'"'title'"'"']}</h3><p>📅 {event.get('"'"'date'"'"','"'"'Onbekend'"'"')} | 📍 {event.get('"'"'location'"'"','"'"'Onbekend'"'"')}</p><p>{event.get('"'"'description'"'"','"'"'Geen beschrijving'"'"')}</p><p><a href="{event.get('"'"'website'"'"','"'"'#'"'"')}">🌐 Meer info</a></p></div>'
    return html + '</body></html>'

def send_email(subject, html_content):
    if not (SMTP_USER and SMTP_PASSWORD and EMAIL_FROM and EMAIL_TO):
        log('E-mail overgeslagen: SMTP/EMAIL secrets niet volledig ingesteld')
        return
    try:
        msg = MIMEMultipart(); msg['From'] = EMAIL_FROM; msg['To'] = ', '.join(EMAIL_TO); msg['Subject'] = subject; msg.attach(MIMEText(html_content, 'html'))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(); server.login(SMTP_USER, SMTP_PASSWORD); server.send_message(msg)
        log(f'E-mail verstuurd: {subject}')
    except Exception as e:
        log(f'Fout bij versturen e-mail: {e}')

def main():
    log('=== Start scraping ===')
    log(f'Inputs: region={INPUT_REGION}, date={INPUT_DATE}, radius={INPUT_RADIUS}, category={INPUT_CATEGORY}, query={INPUT_QUERY}')
    all_events = scrape_uitzinnig(INPUT_REGION) + manual_events()
    unique, seen = [], set()
    for event in all_events:
        key = (event.get('title','').strip().lower(), event.get('location','').strip().lower(), event.get('date','').strip().lower())
        if key not in seen: seen.add(key); unique.append(event)
    filtered = [e for e in unique if matches_filters(e)]
    save_events_to_json(filtered)
    today = datetime.now().strftime('%A, %d %B %Y')
    send_email(f'Evenementen rond {INPUT_REGION} – {today}', generate_email_html(filtered))
    log('=== Scraping voltooid ===')

if __name__ == '__main__':
    main()
