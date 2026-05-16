import requests
from bs4 import BeautifulSoup
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import os

# --- Configuratie ---
# Paden
DATA_FILE = "events.json"
EMAIL_TEMPLATE = "email_template.html"
LOG_FILE = "scrape_log.txt"

# E-mail instellingen (pas aan!)
SMTP_SERVER = "smtp.gmail.com"  # Voor Gmail. Gebruik "smtp.yourprovider.com" voor andere providers.
SMTP_PORT = 587
SMTP_USER = ""
SMTP_PASSWORD = "xxxx xxjj xxxx xxxx"  # Gebruik een App Password voor Gmail!
EMAIL_FROM = ""
EMAIL_TO = ["", ""]  # Meerdere ontvangers mogelijk

# Websites om te scrapen
WEBSITES = {
    "Uitzinnig": "https://www.uitzinnig.nl/groningen/dagje-uit.aspx",
    "Kidsproof": "https://www.kidsproof.nl/groningen/uitjes/uitagenda/",
    "Spot Groningen": "https://www.spotgroningen.nl/programma/",
    "VERA Groningen": "https://www.vera-groningen.nl/programma/",
    "Bioscoop Agenda": "https://www.biosagenda.nl/films-bioscoop-bioscopen_groningen_58.html"
}

# --- Functies ---
def log(message):
    """Log berichten naar een bestand."""
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")

def scrape_uitzinnig():
    """Scrape evenementen van Uitzinnig.nl."""
    events = []
    try:
        url = WEBSITES["Uitzinnig"]
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')

        # Voorbeeld: Zoek naar evenementen in div's met klasse 'event'
        # PAS DIT AAN AAN DE WERKELIJKE STRUCTUUR VAN DE WEBSITE!
        event_divs = soup.find_all("div", class_="event")  # Vervang 'event' door de juiste klasse

        for div in event_divs:
            title = div.find("h2").text.strip() if div.find("h2") else "Onbekend"
            date = div.find("span", class_="date").text.strip() if div.find("span", class_="date") else "Onbekend"
            location = div.find("span", class_="location").text.strip() if div.find("span", class_="location") else "Onbekend"
            description = div.find("p").text.strip() if div.find("p") else "Geen beschrijving"
            link = div.find("a")["href"] if div.find("a") else "#"

            events.append({
                "title": title,
                "date": date,
                "location": location,
                "description": description,
                "website": link,
                "source": "Uitzinnig"
            })
        log(f"Gevonden: {len(events)} evenementen op Uitzinnig.nl")
    except Exception as e:
        log(f"Fout bij scrapen Uitzinnig: {e}")
    return events

def scrape_kidsproof():
    """Scrape evenementen van Kidsproof.nl."""
    events = []
    try:
        url = WEBSITES["Kidsproof"]
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')

        # Voorbeeld: Zoek naar evenementen in li's
        event_items = soup.find_all("li", class_="event-item")  # Vervang door juiste klasse

        for item in event_items:
            title = item.find("h3").text.strip() if item.find("h3") else "Onbekend"
            date = item.find("span", class_="date").text.strip() if item.find("span", class_="date") else "Onbekend"
            location = item.find("span", class_="location").text.strip() if item.find("span", class_="location") else "Onbekend"
            description = item.find("p").text.strip() if item.find("p") else "Geen beschrijving"
            link = item.find("a")["href"] if item.find("a") else "#"

            events.append({
                "title": title,
                "date": date,
                "location": location,
                "description": description,
                "website": link,
                "source": "Kidsproof"
            })
        log(f"Gevonden: {len(events)} evenementen op Kidsproof.nl")
    except Exception as e:
        log(f"Fout bij scrapen Kidsproof: {e}")
    return events

def save_events_to_json(events):
    """Sla evenementen op in een JSON-bestand."""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=4)
        log(f"Opgeslagen: {len(events)} evenementen in {DATA_FILE}")
    except Exception as e:
        log(f"Fout bij opslaan JSON: {e}")

def generate_email_html(events):
    """Genereer HTML voor de e-mail."""
    today = datetime.now().strftime("%A, %d %B %Y")
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; }}
            .header {{ background: #4CAF50; color: white; padding: 20px; text-align: center; }}
            .event {{ border-left: 4px solid #4CAF50; padding: 15px; margin: 15px 0; background: #f9f9f9; }}
            .event h3 {{ margin-top: 0; color: #4CAF50; }}
            a {{ color: #4CAF50; text-decoration: none; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🎉 Evenementen Rond Groningen – {today}</h1>
            <p>Hier zijn de leukste activiteiten voor deze week:</p>
        </div>
    """

    for event in events[:10]:  # Stuur max 10 evenementen in de e-mail
        html += f"""
        <div class="event">
            <h3>{event['title']}</h3>
            <p>📅 {event.get('date', 'Onbekend')} | 📍 {event.get('location', 'Onbekend')}</p>
            <p>{event.get('description', 'Geen beschrijving')}</p>
            <p><a href="{event.get('website', '#')}">🌐 Meer info</a></p>
        </div>
        """

    html += """
    </body>
    </html>
    """
    return html

def send_email(subject, html_content):
    """Verstuur een e-mail met de evenementen."""
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_FROM
        msg['To'] = ", ".join(EMAIL_TO)
        msg['Subject'] = subject

        msg.attach(MIMEText(html_content, 'html'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        log(f"E-mail verstuurd: {subject}")
    except Exception as e:
        log(f"Fout bij versturen e-mail: {e}")

def main():
    """Hoofd functie: scrape, sla op, en verstuur e-mail."""
    log("=== Start scraping ===")

    # Scrape evenementen
    all_events = []
    all_events.extend(scrape_uitzinnig())
    all_events.extend(scrape_kidsproof())
    # Voeg hier meer scrapers toe als nodig

    # Voeg handmatige evenementen toe (voor permanente of specifieke evenementen)
    manual_events = [
        {
            "title": "Groninger Museum",
            "date": "Hele jaar",
            "location": "Groningen",
            "description": "Moderne kunst en wisselende tentoonstellingen.",
            "website": "https://www.groningermuseum.nl",
            "source": "Manual"
        },
        {
            "title": "Fort Bourtange",
            "date": "Hele jaar",
            "location": "Bourtange",
            "description": "Historische vesting met demonstraties en musea.",
            "website": "https://www.bourtange.nl",
            "source": "Manual"
        }
    ]
    all_events.extend(manual_events)

    # Sla op in JSON
    save_events_to_json(all_events)

    # Genereer en verstuur e-mail
    today = datetime.now().strftime("%A, %d %B %Y")
    email_html = generate_email_html(all_events)
    send_email(f"Evenementen Rond Groningen – {today}", email_html)

    log("=== Scraping voltooid ===")

if __name__ == "__main__":
    main()