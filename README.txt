# Evenementen pakket

Bestanden:
- `index.html` toont de evenementen uit `events.json`.
- `scrape_and_email.py` scrape alleen en schrijft `events.json` weg.
- `.github/workflows/update-events.yml` ververst automatisch via GitHub Actions.

Gebruik:
1. Zet deze bestanden in je repository.
2. Zorg dat GitHub Pages aan staat op de branch met `index.html`.
3. Run de workflow handmatig of wacht op de scheduler.
4. De site leest bij openen direct `events.json` in.
