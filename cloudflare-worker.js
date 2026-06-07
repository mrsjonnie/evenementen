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

function clean(value) {
  return String(value || "")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\u00c3\u00a2\u00c2\u0082\u00c2\u00ac|\u00e2\u0082\u00ac|\u20ac/g, "\u20ac")
    .replace(/\bEUR\s*(?=\d)/gi, "\u20ac ")
    .replace(/\bEUR\b/gi, "\u20ac")
    .replace(/\u20ac\s*(?=\d)/g, "\u20ac ")
    .replace(/&nbsp;|&#160;/g, " ")
    .replace(/&euro;|&#8364;/gi, "\u20ac")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, "\"")
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\s+/g, " ")
    .trim();
}

function validateInput(input, maxLength) {
  if (input === undefined || input === null) return "";
  return String(input).slice(0, maxLength).trim();
}

function validSites(value) {
  if (!Array.isArray(value)) return [];

  return value
    .map((site) => validateInput(site, 300))
    .map((site) => (/^[a-z][a-z0-9+.-]*:/i.test(site) ? site : `https://${site}`))
    .filter((site) => {
      try {
        const url = new URL(site);
        return ["http:", "https:"].includes(url.protocol) && url.hostname.includes(".");
      } catch {
        return false;
      }
    })
    .slice(0, 20);
}

function sameHostUrl(candidate, site) {
  try {
    const candidateUrl = new URL(candidate);
    const siteUrl = new URL(site);
    return candidateUrl.hostname.replace(/^www\./, "") === siteUrl.hostname.replace(/^www\./, "");
  } catch {
    return false;
  }
}

function mergeSites(...groups) {
  const seen = new Set();
  const merged = [];
  for (const group of groups) {
    for (const site of validSites(group || [])) {
      const key = site.toLowerCase().replace(/\/$/, "");
      if (!seen.has(key)) {
        seen.add(key);
        merged.push(site);
      }
    }
  }
  return merged.slice(0, 20);
}

function serpApiEnabled(env, body = {}) {
  const providers = body.providers && typeof body.providers === "object" ? body.providers : {};
  return Boolean(env.SERPAPI_TOKEN) && providers.serpapi !== false;
}

function serpSearchQuery(site, body = {}) {
  const url = new URL(site);
  const region = validateInput(body.region || "Groningen", 80);
  const dateHint = [body.dateFrom, body.dateTo].map((value) => validateInput(value || "", 20)).filter(Boolean).join(" ");
  const pathHint = url.pathname && url.pathname !== "/" ? url.pathname.replace(/[/-]+/g, " ") : "agenda programma";
  return `site:${url.hostname} ${pathHint} evenement ${region} ${dateHint}`.trim();
}

async function serpApiLinksForSite(env, site, body = {}) {
  const token = validateInput(env.SERPAPI_TOKEN || "", 500);
  if (!token) return [];
  const requestedHits = Math.max(20, Math.min(100, Number(body.maxEventsPerSite || body.minResults) || 20));

  const params = new URLSearchParams({
    engine: "google",
    q: serpSearchQuery(site, body),
    num: String(requestedHits),
    api_key: token
  });
  const response = await fetch(`https://serpapi.com/search.json?${params.toString()}`, {
    headers: { "Accept": "application/json" }
  });
  if (!response.ok) return [];

  const data = await response.json().catch(() => ({}));
  const results = Array.isArray(data.organic_results) ? data.organic_results : [];
  return results
    .map((item) => validateInput(item.link || "", 300))
    .filter((link) => link && sameHostUrl(link, site) && !badWords.test(link))
    .slice(0, Math.min(requestedHits, 20));
}

async function enrichBodyWithSerpApi(env, body = {}) {
  const selected = validSites(body.sites);
  if (!serpApiEnabled(env, body) || !selected.length) {
    return { body, serpApiAddedCount: 0 };
  }

  const priority = [...selected].sort((a, b) => {
    const aScore = /spotgroningen|vera-groningen|forum\.nl/i.test(a) ? 0 : 1;
    const bScore = /spotgroningen|vera-groningen|forum\.nl/i.test(b) ? 0 : 1;
    return aScore - bScore;
  }).slice(0, 4);

  const discovered = [];
  for (const site of priority) {
    try {
      discovered.push(...await serpApiLinksForSite(env, site, body));
    } catch {}
  }

  const merged = mergeSites(selected, discovered);
  return {
    body: { ...body, sites: merged },
    serpApiAddedCount: Math.max(0, merged.length - selected.length)
  };
}

const monthMap = {
  januari: 1,
  februari: 2,
  maart: 3,
  april: 4,
  mei: 5,
  juni: 6,
  juli: 7,
  augustus: 8,
  september: 9,
  oktober: 10,
  november: 11,
  december: 12
};

const monthNames = Object.keys(monthMap).join("|");
const eventWords = /(event|evenement|agenda|programma|concert|festival|theater|film|bioscoop|markt|workshop|lezing|expo|expositie|tentoonstelling|voorstelling|activiteit|activiteiten|tickets|uitgaan|muziek|cabaret|dans|opera|museum|kermis|kinderen)/i;
const badWords = /(contact|privacy|cookie|voorwaarden|login|account|nieuwsbrief|facebook|instagram|linkedin)/i;
const commonPaths = [
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
  "/nl/doen/uitgaan"
];

function normalizeDate(value) {
  const text = clean(value);
  const iso = text.match(/\b20\d{2}-\d{2}-\d{2}\b/);
  if (iso) return iso[0];

  const dutch = text.match(new RegExp(`\\b(\\d{1,2})(?:\\s+t/m\\s+\\d{1,2})?\\s+(${monthNames})(?:\\s+(20\\d{2}))?\\b`, "i"));
  if (!dutch) return "";

  const day = Number(dutch[1]);
  const month = monthMap[dutch[2].toLowerCase()];
  let year = Number(dutch[3] || new Date().getFullYear());
  let date = new Date(Date.UTC(year, month - 1, day));
  const today = new Date();
  if (!dutch[3] && date < new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()))) {
    year += 1;
    date = new Date(Date.UTC(year, month - 1, day));
  }
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString().slice(0, 10);
}

function firstMatch(html, patterns) {
  for (const pattern of patterns) {
    const match = html.match(pattern);
    if (match && match[1]) return clean(match[1]);
  }
  return "";
}

function meta(html, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return firstMatch(html, [
    new RegExp(`<meta[^>]+property=["']${escaped}["'][^>]+content=["']([^"']+)["'][^>]*>`, "i"),
    new RegExp(`<meta[^>]+name=["']${escaped}["'][^>]+content=["']([^"']+)["'][^>]*>`, "i"),
    new RegExp(`<meta[^>]+content=["']([^"']+)["'][^>]+property=["']${escaped}["'][^>]*>`, "i"),
    new RegExp(`<meta[^>]+content=["']([^"']+)["'][^>]+name=["']${escaped}["'][^>]*>`, "i")
  ]);
}

function pageTitle(html) {
  return firstMatch(html, [
    /<h1[^>]*>([\s\S]*?)<\/h1>/i,
    /<h2[^>]*>([\s\S]*?)<\/h2>/i,
    /<title[^>]*>([\s\S]*?)<\/title>/i
  ]);
}

function eventKey(event) {
  return `${event.date || ""}|${(event.title || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim()}`;
}

function validEvent(event) {
  return Boolean(event.title && event.title.length > 3 && event.date && event.website && !badWords.test(event.title));
}

function dedupe(events) {
  const map = new Map();
  for (const event of events) {
    if (!validEvent(event)) continue;
    const key = eventKey(event);
    const existing = map.get(key);
    if (!existing || (event.description || "").length > (existing.description || "").length) {
      map.set(key, event);
    }
  }
  return [...map.values()];
}

async function fetchHtml(pageUrl) {
  const response = await fetch(pageUrl, {
    headers: {
      "User-Agent": "Mozilla/5.0 (Evenementen Scraper)",
      "Accept": "text/html,application/xhtml+xml"
    }
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const type = response.headers.get("content-type") || "";
  if (type && !type.includes("html")) throw new Error(`Geen HTML: ${type}`);
  return await response.text();
}

function seedUrls(site) {
  const parsed = new URL(site);
  const base = `${parsed.protocol}//${parsed.host}`;
  return [...new Set([site.replace(/\/$/, ""), ...commonPaths.map((path) => `${base}${path}`)])];
}

function jsonldNodes(value) {
  if (Array.isArray(value)) return value.flatMap(jsonldNodes);
  if (!value || typeof value !== "object") return [];
  const graph = Array.isArray(value["@graph"]) ? value["@graph"].flatMap(jsonldNodes) : [];
  return [value, ...graph];
}

function eventFromJsonLd(node, pageUrl) {
  const type = Array.isArray(node["@type"]) ? node["@type"].join(" ") : String(node["@type"] || "");
  if (!/event/i.test(type)) return null;

  const location = typeof node.location === "string"
    ? node.location
    : clean([node.location?.name, node.location?.address?.streetAddress, node.location?.address?.addressLocality].filter(Boolean).join(", "));
  const offer = Array.isArray(node.offers) ? node.offers[0] : node.offers;
  const image = Array.isArray(node.image) ? node.image[0] : node.image;
  const imageUrl = typeof image === "object" ? image?.url : image;

  return {
    title: clean(node.name || node.headline),
    type: "evenement",
    date: normalizeDate(node.startDate),
    time: clean(String(node.startDate || "").split("T")[1] || "").slice(0, 5),
    location: clean(location) || new URL(pageUrl).hostname,
    cost: offer?.price ? `${offer.priceCurrency || ""} ${offer.price}`.trim() : "Zie website",
    description: clean(node.description).slice(0, 360),
    image: imageUrl ? new URL(imageUrl, pageUrl).href : "",
    website: node.url ? new URL(node.url, pageUrl).href : pageUrl,
    source: new URL(pageUrl).hostname,
    periodLabel: normalizeDate(node.startDate)
  };
}

function jsonLdEvents(html, pageUrl) {
  const events = [];
  const scripts = html.matchAll(/<script[^>]+type=["'][^"']*ld\+json[^"']*["'][^>]*>([\s\S]*?)<\/script>/gi);
  for (const script of scripts) {
    try {
      const data = JSON.parse(clean(script[1]).replace(/&quot;/g, "\""));
      for (const node of jsonldNodes(data)) {
        const event = eventFromJsonLd(node, pageUrl);
        if (event) events.push(event);
      }
    } catch {}
  }
  return events;
}

function linksFromPage(html, pageUrl) {
  const baseHost = new URL(pageUrl).host;
  const pagePath = new URL(pageUrl).pathname.replace(/[-_/]+/g, " ");
  const pageLooksLikeAgenda = eventWords.test(pagePath);
  const links = [];
  for (const match of html.matchAll(/<a[^>]+href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi)) {
    const href = clean(match[1]);
    const text = clean(match[2]);
    if (!href || badWords.test(`${href} ${text}`)) continue;
    let absolute;
    try {
      absolute = new URL(href, pageUrl);
    } catch {
      continue;
    }
    if (!["http:", "https:"].includes(absolute.protocol) || absolute.host !== baseHost) continue;
    const linkUrl = absolute.href.split("#")[0];
    if (linkUrl.replace(/\/$/, "") === pageUrl.replace(/\/$/, "")) continue;
    const signal = `${absolute.pathname.replace(/[-_/]+/g, " ")} ${text}`;
    if (!pageLooksLikeAgenda && !eventWords.test(signal) && !normalizeDate(signal)) continue;
    if (pageLooksLikeAgenda && !eventWords.test(signal) && clean(text).length < 4) continue;
    links.push({ title: text, url: absolute.href.split("#")[0] });
  }
  return links;
}

function eventFromPage(html, pageUrl, fallbackTitle = "") {
  const title = (meta(html, "og:title") || pageTitle(html) || fallbackTitle).replace(/\s+[|-]\s+.*$/, "");
  const date = normalizeDate(meta(html, "event:start_time") || html);
  const description = meta(html, "og:description") || meta(html, "description") || clean(html).slice(0, 300);
  const image = meta(html, "og:image") || meta(html, "twitter:image");
  const location = meta(html, "event:location") || new URL(pageUrl).hostname;

  return {
    title: clean(title),
    type: "evenement",
    date,
    time: "",
    location: clean(location),
    cost: "Zie website",
    description: clean(description).slice(0, 360),
    image: image ? new URL(image, pageUrl).href : "",
    website: pageUrl,
    source: new URL(pageUrl).hostname,
    periodLabel: date
  };
}

function eventsFromBlocks(html, pageUrl) {
  const events = [];
  const blocks = html.match(/<(article|li|section|div)[^>]*>[\s\S]{0,1800}?<\/\1>/gi) || [];
  for (const block of blocks.slice(0, 260)) {
    const date = normalizeDate(block);
    if (!date) continue;
    const linkMatch = block.match(/<a[^>]+href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/i);
    const heading = firstMatch(block, [/<h[1-4][^>]*>([\s\S]*?)<\/h[1-4]>/i]);
    const title = heading || (linkMatch ? clean(linkMatch[2]) : clean(block).slice(0, 90));
    let website = pageUrl;
    if (linkMatch) {
      try {
        website = new URL(clean(linkMatch[1]), pageUrl).href;
      } catch {}
    }
    events.push({
      title,
      type: "evenement",
      date,
      time: "",
      location: new URL(pageUrl).hostname,
      cost: "Zie website",
      description: clean(block).slice(0, 300),
      image: "",
      website,
      source: new URL(pageUrl).hostname,
      periodLabel: date
    });
  }
  return events;
}

async function scrapeSite(site) {
  const events = [];
  const linkMap = new Map();
  for (const pageUrl of seedUrls(site)) {
    try {
      const html = await fetchHtml(pageUrl);
      jsonLdEvents(html, pageUrl).forEach((event) => events.push(event));
      eventsFromBlocks(html, pageUrl).forEach((event) => events.push(event));
      linksFromPage(html, pageUrl).forEach((link) => linkMap.set(link.url, link.title));
    } catch {}
    if (events.length >= 40) break;
  }

  for (const [url, title] of [...linkMap.entries()].slice(0, 40)) {
    if (events.length >= 40) break;
    try {
      const html = await fetchHtml(url);
      jsonLdEvents(html, url).forEach((event) => events.push(event));
      events.push(eventFromPage(html, url, title));
    } catch {}
  }

  return dedupe(events).slice(0, 40);
}

async function scrapeSites(sites) {
  const events = [];
  for (const site of sites) {
    const found = await scrapeSite(site);
    found.forEach((event) => events.push(event));
  }
  return dedupe(events);
}

function decodeBase64(content) {
  const binary = atob(content.replace(/\n/g, ""));
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function encodeBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary);
}

function githubConfig(env) {
  const token = validateInput(env.GITHUB_TOKEN || env.GH_TOKEN || env.GITHUB_PAT || "", 300);
  if (!token) {
    throw new Error("GITHUB_TOKEN ontbreekt in Cloudflare Variables and Secrets");
  }
  return {
    owner: validateInput(env.GITHUB_OWNER || "mrsjonnie", 100),
    repo: validateInput(env.GITHUB_REPO || "evenementen", 100),
    workflowFile: validateInput(env.GITHUB_WORKFLOW_FILE || "update-events.yml", 100),
    token
  };
}

async function githubFetch(env, path, init = {}) {
  const config = githubConfig(env);
  return fetch(`https://api.github.com/repos/${config.owner}/${config.repo}${path}`, {
    ...init,
    headers: {
      "Authorization": `Bearer ${config.token}`,
      "Accept": "application/vnd.github+json",
      "Content-Type": "application/json",
      "User-Agent": "evenementen-worker",
      ...(init.headers || {})
    }
  });
}

async function saveEventsToGithub(env, foundEvents, options = {}) {
  let existing = { events: [], archive: [] };
  let sha = null;
  const getResponse = await githubFetch(env, "/contents/events.json?ref=main");
  if (getResponse.ok) {
    const file = await getResponse.json();
    sha = file.sha;
    const decoded = JSON.parse(decodeBase64(file.content));
    if (Array.isArray(decoded)) existing.events = decoded;
    if (decoded && Array.isArray(decoded.events)) existing.events = decoded.events;
    if (decoded && Array.isArray(decoded.archive)) existing.archive = decoded.archive;
  }

  const archive = options.clearArchive ? [] : existing.archive;
  const merged = dedupe([...(foundEvents || []), ...(options.clearArchive ? [] : existing.events)]);
  const payload = {
    schemaVersion: 2,
    updatedAt: new Date().toISOString(),
    events: merged,
    archive,
    siteResults: []
  };

  const putResponse = await githubFetch(env, "/contents/events.json", {
    method: "PUT",
    body: JSON.stringify({
      message: options.clearArchive ? "Clear and refresh events.json" : "Refresh events.json from websites",
      content: encodeBase64(JSON.stringify(payload, null, 2)),
      sha,
      branch: "main"
    })
  });

  if (!putResponse.ok) {
    throw new Error(await putResponse.text());
  }

  return { saved: merged.length, added: foundEvents.length };
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
    maxEventsPerSite: validateInput(body.maxEventsPerSite || body.minResults || "20", 10),
    siteTimeLimitSeconds: validateInput(body.siteTimeLimitSeconds || "20", 10),
    sites: JSON.stringify(validSites(body.sites)),
    providers: "{}",
    clearArchive: overrides.clearArchive ? "true" : "false"
  };
}

async function dispatchWorkflow(env, inputs) {
  const configuredWorkflow = githubConfig(env).workflowFile;
  const workflowFiles = [...new Set([configuredWorkflow, "update-events.yml", "daily_update.yml"].filter(Boolean))];
  const failures = [];

  for (const workflowFile of workflowFiles) {
    const ghResponse = await githubFetch(env, `/actions/workflows/${workflowFile}/dispatches`, {
      method: "POST",
      body: JSON.stringify({
        ref: "main",
        inputs
      })
    });

    if (ghResponse.status === 204) {
      return { ok: true, workflowFile };
    }

    failures.push(`${workflowFile}: status ${ghResponse.status} ${validateInput(await ghResponse.text(), 500)}`);
  }

  return {
    ok: false,
    status: 500,
    details: failures.join(" | ")
  };
}

async function upsertRefreshRequest(env, body, overrides = {}, dispatchFailure = null) {
  const inputs = workflowInputs(body || {}, overrides);
  const path = "/contents/refresh-request.json";
  let sha = null;
  const current = await githubFetch(env, `${path}?ref=main`);
  if (current.ok) {
    const file = await current.json();
    sha = file.sha;
  }

  const request = {
    requestedAt: new Date().toISOString(),
    trigger: overrides.clearArchive ? "clear" : "refresh",
    dispatchFallback: dispatchFailure ? {
      status: dispatchFailure.status,
      details: validateInput(dispatchFailure.details || "", 500)
    } : null,
    ...inputs
  };

  const response = await githubFetch(env, path, {
    method: "PUT",
    body: JSON.stringify({
      message: overrides.clearArchive ? "Request clear and refresh events" : "Request events refresh",
      content: encodeBase64(JSON.stringify(request, null, 2)),
      sha,
      branch: "main"
    })
  });

  if (!response.ok) {
    return {
      ok: false,
      status: response.status,
      details: await response.text()
    };
  }

  return { ok: true };
}

async function startRefresh(env, body, overrides = {}) {
  const enriched = await enrichBodyWithSerpApi(env, body || {});
  const refreshBody = enriched.body;
  const inputs = workflowInputs(refreshBody, overrides);
  const dispatch = await dispatchWorkflow(env, inputs);
  if (dispatch.ok) {
    return { ok: true, method: "workflow_dispatch", workflowFile: dispatch.workflowFile, serpApiAddedCount: enriched.serpApiAddedCount };
  }

  const fallback = await upsertRefreshRequest(env, refreshBody, overrides, dispatch);
  if (fallback.ok) {
    return { ok: true, method: "refresh_request", dispatchFailure: dispatch, serpApiAddedCount: enriched.serpApiAddedCount };
  }

  return {
    ok: false,
    status: fallback.status || dispatch.status,
    details: `Workflow dispatch: ${dispatch.details || dispatch.status}. Refresh-request fallback: ${fallback.details || fallback.status}`
  };
}

function weatherDaily() {
  return [
    { tempMin: 14, tempMax: 21, rainMM: 0.4, icon: "01d" },
    { tempMin: 13, tempMax: 20, rainMM: 1.2, icon: "04d" },
    { tempMin: 15, tempMax: 22, rainMM: 0.0, icon: "01d" },
    { tempMin: 16, tempMax: 23, rainMM: 2.6, icon: "10d" },
    { tempMin: 14, tempMax: 19, rainMM: 3.1, icon: "10d" },
    { tempMin: 13, tempMax: 18, rainMM: 0.8, icon: "04d" },
    { tempMin: 15, tempMax: 21, rainMM: 0.2, icon: "01d" }
  ];
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }

    if (path === "/health" && request.method === "GET") {
      return json({
        ok: true,
        worker: "evenementen-refresh",
        githubTokenConfigured: Boolean(env.GITHUB_TOKEN || env.GH_TOKEN || env.GITHUB_PAT),
        serpApiConfigured: Boolean(env.SERPAPI_TOKEN),
        acceptedTokenNames: ["GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"],
        githubOwner: env.GITHUB_OWNER || "mrsjonnie",
        githubRepo: env.GITHUB_REPO || "evenementen",
        configuredWorkflowFile: env.GITHUB_WORKFLOW_FILE || "update-events.yml",
        fallbackWorkflowFiles: ["update-events.yml", "daily_update.yml"]
      }, 200);
    }

    if (path === "/weather" && request.method === "GET") {
      const daily = weatherDaily();
      return json({
        ok: true,
        summary: "Wisselend weer",
        days: 7,
        tempMin: Math.min(...daily.map((day) => day.tempMin)),
        tempMax: Math.max(...daily.map((day) => day.tempMax)),
        rainChance: 35,
        rainTotalMM: daily.reduce((sum, day) => sum + day.rainMM, 0).toFixed(1),
        windMax: 18,
        uvMax: 5,
        weatherCode: 0,
        daily
      }, 200);
    }

    if (path === "/clear" && request.method === "POST") {
      try {
        const body = await request.json().catch(() => ({}));
        const clearResult = await saveEventsToGithub(env, [], { clearArchive: true });
        let result = null;
        try {
          result = await startRefresh(env, body || {}, { clearArchive: true });
        } catch (error) {
          result = { ok: false, error: validateInput(error.message, 500) };
        }
        return json({
          ok: true,
          message: result.ok
            ? "Alles is verwijderd en opnieuw verversen is gestart"
            : "Alles is verwijderd, maar opnieuw verversen kon niet starten",
          cleared: true,
          clearResult,
          refreshStarted: Boolean(result.ok),
          method: result.method || null,
          workflowFile: result.workflowFile || null,
          serpApiAddedCount: result.serpApiAddedCount || 0,
          scrapedCount: 0,
          totalSaved: 0,
          refreshError: result.ok ? null : (result.error || result.details || "Onbekende fout")
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
      if (!body) return json({ error: "Ongeldige JSON body" }, 400);

      const result = await startRefresh(env, body);
      if (!result.ok) {
        return json({
          error: "GitHub verversverzoek mislukt",
          status: result.status,
          details: result.details
        }, 500);
      }

      return json({
        ok: true,
        message: "Verversing gestart via GitHub Actions",
        method: result.method,
        workflowFile: result.workflowFile || null,
        serpApiAddedCount: result.serpApiAddedCount || 0,
        scrapedCount: null,
        totalSaved: null
      }, 200);
    } catch (e) {
      return json({
        error: e.message || "Onbekende fout",
        details: e.stack || ""
      }, 500);
    }
  }
};
