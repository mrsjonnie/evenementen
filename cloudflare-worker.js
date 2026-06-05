// CORS headers voor alle responses
function corsHeaders(origin = '*') {
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Max-Age': '86400' // Cache CORS preflight voor 24 uur
  };
}

// Helper functie om JSON responses te returnen
function json(data, status = 200, origin = '*') {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json',
      ...corsHeaders(origin)
    }
  });
}

// Valideer en sanitize input
function validateInput(input, maxLength) {
  if (typeof input !== 'string') return input.toString();
  return input.slice(0, maxLength).trim();
}

// Main fetch handler
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // CORS preflight handling
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders() });
    }

    // Weather endpoint (voorbeeld: /weather?region=Groningen&from=2026-06-05&to=2026-06-07)
    if (path === '/weather' && request.method === 'GET') {
      try {
        const region = url.searchParams.get('region') || 'Groningen';
        const from = url.searchParams.get('from') || '';
        const to = url.searchParams.get('to') || '';

        // Hier zou je een weerservice kunnen aanroepen (bv. OpenWeatherMap API)
        // Voor nu returnen we dummy data voor testdoeleinden
        const weatherData = {
          ok: true,
          summary: "Zonnig met af en toe een wolk",
          days: 3,
          tempMin: 15,
          tempMax: 22,
          rainChance: 20,
          rainTotalMM: 5,
          windMax: 15,
          uvMax: 6,
          weatherCode: 0 // 0 = zonnig
        };

        return json(weatherData, 200);
      } catch (e) {
        return json({ error: 'Fout bij ophalen weerdata', details: e.message }, 500);
      }
    }

    // Clear endpoint (voor /clear)
    if (path === '/clear' && request.method === 'POST') {
      try {
        // Hier zou je een GitHub Actions workflow kunnen triggeren om events.json leeg te maken
        // Voor nu returnen we een dummy response
        return json({ ok: true, message: 'Alle evenementen zijn verwijderd (simulatie)' }, 200);
      } catch (e) {
        return json({ error: 'Fout bij verwijderen', details: e.message }, 500);
      }
    }

    // Main POST endpoint voor scraping
    if (request.method !== 'POST') {
      return json({ error: 'Method not allowed. Use POST.' }, 405);
    }

    try {
      // Lees en valideer de body
      const body = await request.json();
      if (!body) {
        return json({ error: 'Ongeldige JSON body' }, 400);
      }

      // Valideer en sanitize inputs
      const payload = {
        ref: 'main',
        inputs: {
          region: validateInput(body.region || 'Groningen', 100),
          date: validateInput(body.date || '', 40),
          radius: validateInput(body.radius || '140', 10),
          category: validateInput(body.category || 'all', 100),
          query: validateInput(body.query || '', 200),
          // Voeg nieuwe velden toe voor compatibiliteit
          minResults: validateInput(body.minResults || '20', 10),
          sites: Array.isArray(body.sites) ? body.sites.slice(0, 50) : [],
          providers: body.providers || {}
        }
      };

      // Trigger GitHub Actions workflow
      const ghUrl = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW_FILE}/dispatches`;
      const ghResponse = await fetch(ghUrl, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${env.GITHUB_TOKEN}`,
          'Accept': 'application/vnd.github+json',
          'Content-Type': 'application/json',
          'User-Agent': 'evenementen-worker'
        },
        body: JSON.stringify(payload)
      });

      if (ghResponse.status !== 204) {
        const errorText = await ghResponse.text();
        return json({
          error: 'GitHub dispatch mislukt',
          status: ghResponse.status,
          details: errorText
        }, 500);
      }

      // Return success response
      return json({
        ok: true,
        message: 'Workflow gestart',
        scrapedCount: 0, // Dit zou je uit de workflow response kunnen halen
        totalSaved: 0    // Dit zou je uit de workflow response kunnen halen
      }, 200);

    } catch (e) {
      return json({
        error: e.message || 'Onbekende fout',
        details: e.stack || ''
      }, 500);
    }
  }
};
