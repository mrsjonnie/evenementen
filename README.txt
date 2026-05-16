Bestanden in deze map:
- index.html -> website met knop die een worker aanroept
- cloudflare-worker.js -> veilige API-laag voor workflow_dispatch
- daily_update.yml -> GitHub Actions workflow met inputs
- scrape_and_email.py -> scraper die inputs gebruikt en events.json bijwerkt

Instellen:
1. Zet index.html in je repo.
2. Zet .github/workflows/daily_update.yml in je repo.
3. Vervang scrape_and_email.py in je repo.
4. Maak een Cloudflare Worker aan met cloudflare-worker.js.
5. Voeg Worker secrets toe:
   - GITHUB_TOKEN
   - GITHUB_OWNER = mrsjonnie
   - GITHUB_REPO = evenementen
   - GITHUB_WORKFLOW_FILE = daily_update.yml
6. Zet in index.html de WORKER_URL naar jouw worker URL.
7. Voeg in GitHub Secrets optioneel SMTP secrets toe als je e-mail wilt houden.
