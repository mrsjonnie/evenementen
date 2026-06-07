function corsHeaders(origin = "*") {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Max-Age": "86400"
  };
}

function json(data, status = 200, origin = "*") {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...corsHeaders(origin)
    }
  });
}

function validateInput(input, maxLength) {
  if (input === undefined || input === null) return "";
  return String(input).slice(0, maxLength).trim();
}

function validSites(value) {
  if (!Array.isArray(value)) return [];

  return value
    .map((site) => validateInput(site, 300))
    .filter((site) => {
      try {
        const url = new URL(site);
        return ["http:", "https:"].includes(url.protocol) && url.hostname.includes(".");
      } catch {
        return false;
      }
    })
    .slice(0, 30);
}

function workflowInputs(body, overrides = {}) {
  return {
    region: validateInput(body.region || "Groningen", 100),
    radius: validateInput(body.radius || "200", 10),
    category: validateInput(body.category || "all", 100),
    query: validateInput(body.query || "", 200),
    dateFrom: validateInput(body.dateFrom || "", 40),
    dateTo: validateInput(body.dateTo || "", 40),
    minResults: validateInput(body.minResults || "20", 10),
    sites: JSON.stringify(validSites(body.sites)),
    providers: JSON.stringify(body.providers || {}),
    clearArchive: overrides.clearArchive ? "true" : "false"
  };
}

async function dispatchWorkflow(env, inputs) {
  const workflowFile = env.GITHUB_WORKFLOW_FILE || "update-events.yml";
  const ghUrl = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${workflowFile}/dispatches`;
  const ghResponse = await fetch(ghUrl, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "Content-Type": "application/json",
      "User-Agent": "evenementen-worker"
    },
    body: JSON.stringify({
      ref: "main",
      inputs
    })
  });

  if (ghResponse.status !== 204) {
    const errorText = await ghResponse.text();
    return {
      ok: false,
      status: ghResponse.status,
      details: errorText
    };
  }

  return { ok: true };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }

    if (path === "/weather" && request.method === "GET") {
      try {
        return json({
          ok: true,
          summary: "Zonnig met af en toe een wolk",
          days: 3,
          tempMin: 15,
          tempMax: 22,
          rainChance: 20,
          rainTotalMM: 5,
          windMax: 15,
          uvMax: 6,
          weatherCode: 0
        }, 200);
      } catch (e) {
        return json({ error: "Fout bij ophalen weerdata", details: e.message }, 500);
      }
    }

    if (path === "/clear" && request.method === "POST") {
      try {
        const body = await request.json().catch(() => ({}));
        const inputs = workflowInputs(body || {}, { clearArchive: true });
        const result = await dispatchWorkflow(env, inputs);
        if (!result.ok) {
          return json({
            error: "GitHub dispatch voor wissen mislukt",
            status: result.status,
            details: result.details
          }, 500);
        }

        return json({
          ok: true,
          message: "Wissen en opnieuw verversen gestart",
          scrapedCount: 0,
          totalSaved: 0
        }, 200);
      } catch (e) {
        return json({ error: "Fout bij verwijderen", details: e.message }, 500);
      }
    }

    if (request.method !== "POST") {
      return json({ error: "Method not allowed. Use POST." }, 405);
    }

    try {
      const body = await request.json();
      if (!body) {
        return json({ error: "Ongeldige JSON body" }, 400);
      }

      const result = await dispatchWorkflow(env, workflowInputs(body));
      if (!result.ok) {
        return json({
          error: "GitHub dispatch mislukt",
          status: result.status,
          details: result.details
        }, 500);
      }

      return json({
        ok: true,
        message: "Workflow gestart",
        scrapedCount: 0,
        totalSaved: 0
      }, 200);
    } catch (e) {
      return json({
        error: e.message || "Onbekende fout",
        details: e.stack || ""
      }, 500);
    }
  }
};
