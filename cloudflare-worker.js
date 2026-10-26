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

function validSites(value, limit = 20) {
  if (!Array.isArray(value)) return [];

  const result = [];
  const seen = new Set();
  for (const item of value) {
    const site = validateInput(item, 300);
    const normalized = /^[a-z][a-z0-9+.-]*:/i.test(site) ? site : `https://${site}`;
    try {
      const url = new URL(normalized);
      if (!["http:", "https:"].includes(url.protocol) || !url.hostname.includes(".")) continue;
      const key = url.href.toLowerCase().replace(/\/$/, "");
      if (seen.has(key)) continue;
      seen.add(key);
      result.push(/^[a-z][a-z0-9+.-]*:/i.test(site) ? site.split("#")[0] : url.href);
      if (result.length >= limit) break;
    } catch {}
  }
  return result;
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

function dutchShortDate(value) {
  const months = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];
  const text = validateInput(value || "", 40);
  const match = text.match(/^(20\d{2})-(\d{2})-(\d{2})$/);
  if (!match) return "";
  const month = months[Math.max(0, Math.min(11, Number(match[2]) - 1))];
  return `${Number(match[3])} ${month}`;
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
  return Boolean(validateInput(env.SERPAPI_TOKEN || "", 500) && providers.serpapi === true);
}

function chatGptEnabled(env, body = {}) {
  const providers = body.providers && typeof body.providers === "object" ? body.providers : {};
  return Boolean(validateInput(env.CHATGPT_API_KEY || "", 500) && (providers.chatgpt === true || providers.openai === true));
}

function mistralEnabled(env, body = {}) {
  const providers = body.providers && typeof body.providers === "object" ? body.providers : {};
  return Boolean(validateInput(env.MISTRAL_API_KEY || "", 500) && providers.mistral === true);
}

function serpSearchQueries(site, body = {}) {
  const url = new URL(site);
  const region = validateInput(body.region || "Groningen", 80);
  const dateHint = [body.dateFrom, body.dateTo].map((value) => validateInput(value || "", 20)).filter(Boolean).join(" ");
  const pathHint = url.pathname && url.pathname !== "/" ? url.pathname.replace(/[/-]+/g, " ") : "agenda programma";
  const hostPath = `${url.hostname}${url.pathname && url.pathname !== "/" ? url.pathname.replace(/\/$/, "") : ""}`;
  const queries = [
    `site:${hostPath} ${pathHint} agenda programma evenement concert ${region} ${dateHint}`,
    `site:${url.hostname} "${region}" agenda programma evenement concert theater film ${dateHint}`
  ];
  if (/spotgroningen\.nl/i.test(url.hostname)) {
    const shortDate = dutchShortDate(body.dateFrom);
    queries.unshift(`site:${hostPath} ${shortDate ? `"${shortDate}"` : ""} agenda programma SPOT Groningen`);
  }
  return [...new Set(queries.map((query) => query.replace(/\s+/g, " ").trim()).filter(Boolean))].slice(0, 3);
}

function cleanSerpTitle(value, site) {
  const host = (() => {
    try {
      return new URL(site).hostname.replace(/^www\./, "");
    } catch {
      return "";
    }
  })();
  return clean(value)
    .replace(new RegExp(`\\s+[-|]\\s+(${host}|SPOT Groningen|Forum|VERA Groningen|Paradiso|Hedon|Concertgebouw).*$`, "i"), "")
    .replace(/\s+\|\s+.*$/, "")
    .trim();
}

function serpApiEventCandidate(site, item = {}, queryIndex = 0, rank = 0, sourceType = "organic") {
  const link = validateInput(item.link || item.url || item.event_location_map?.link || "", 500);
  if (!link || !sameHostUrl(link, site) || badWords.test(link)) return null;
  const snippet = clean(item.snippet || item.description || item.subtitle || item.date || item.address || "");
  const title = cleanSerpTitle(item.title || item.name || "", site);
  const extensions = Array.isArray(item.extensions) ? item.extensions.join(" ") : clean(item.extensions || "");
  const dateText = clean(item.date || item.when || extensions || `${title} ${snippet}`);
  const eventDate = normalizeDate(dateText) || normalizeDate(`${title} ${snippet}`);
  if (!title || title.length < 3 || /^(agenda|programma|tickets?|contact)$/i.test(title)) return null;
  return {
    source: "SerpAPI",
    discoverySource: "SerpAPI",
    sourceType,
    site,
    title,
    date: eventDate,
    dateText,
    location: clean(item.venue?.name || item.address || item.location || ""),
    type: "",
    description: snippet,
    website: link,
    url: link,
    rank,
    query: queryIndex + 1
  };
}

function serpApiEventsFromData(site, data = {}, queryIndex = 0) {
  const candidates = [];
  const organic = Array.isArray(data.organic_results) ? data.organic_results : [];
  organic.forEach((item, index) => {
    const candidate = serpApiEventCandidate(site, item, queryIndex, index + 1, "organic");
    if (candidate) candidates.push(candidate);
  });

  const eventResults = Array.isArray(data.events_results) ? data.events_results : [];
  eventResults.forEach((item, index) => {
    const candidate = serpApiEventCandidate(site, item, queryIndex, index + 1, "events");
    if (candidate) candidates.push(candidate);
  });
  return candidates;
}

function aiPromptForSite(site, body = {}, source = "ChatGPT", pageText = "") {
  const from = validateInput(body.dateFrom || "", 40);
  const to = validateInput(body.dateTo || "", 40);
  const region = validateInput(body.region || "Groningen", 80);
  const maxEvents = Math.max(20, Math.min(80, Number(body.maxEventsPerSite || body.minResults) || 20));
  if (source === "Mistral") {
    return [
      `kun je een overzicht maken in de vorm van een tabel met alle gevonden activiteiten die je kan vinden op de site ${site}, ik wil weten: datum/tijdstip, type (bijvoorbeeld concert of theater), titel/artiest, korte omschrijving, locatie, web url.`,
      `Regio/context: ${region}. Periode: ${from || "vandaag"} t/m ${to || "ongeveer 14 dagen later"}. Maximaal ${maxEvents} evenementen.`,
      "Controleer per activiteit streng of de datum/tijd, titel/artiest, locatie en web url uit de meegegeven websitetekst blijken. Controleer ook dat de web url in de tekst of linklijst voorkomt en op hetzelfde domein staat. Laat een activiteit weg als de url, datum of titel niet te controleren is.",
      "Antwoord niet als Markdown-tabel maar als JSON met deze vorm: {\"events\":[{\"date\":\"YYYY-MM-DD\",\"time\":\"HH:MM\",\"title\":\"...\",\"type\":\"Concert|Theater|Film|Festival|Expositie|Workshop|Lezing|Familie|Activiteit\",\"location\":\"...\",\"website\":\"https://...\",\"description\":\"...\"}]}",
      "Geen fictieve of geschatte events. Geen algemene agenda-pagina als titel. Gebruik alleen de meegegeven websitetekst.",
      pageText ? `Websitetekst en linklijst:\n${pageText.slice(0, 14000)}` : ""
    ].filter(Boolean).join("\n\n");
  }
  const evidenceRule = source === "Mistral"
    ? "Gebruik uitsluitend de meegegeven websitetekst. Als een datum, titel of URL niet uit die tekst blijkt, laat het event weg."
    : "Gebruik web search voor deze exacte website. Gebruik alleen events die je op een echte pagina van deze site kunt koppelen aan een bron-URL.";
  return [
    `Zoek echte evenementen op deze website: ${site}`,
    `Regio/context: ${region}. Periode: ${from || "vandaag"} t/m ${to || "ongeveer 14 dagen later"}.`,
    `Maximaal ${maxEvents} evenementen.`,
    evidenceRule,
    "Geef alleen JSON terug met deze vorm: {\"events\":[{\"date\":\"YYYY-MM-DD\",\"title\":\"...\",\"type\":\"Concert|Theater|Film|Festival|Expositie|Workshop|Lezing|Familie|Activiteit\",\"location\":\"...\",\"website\":\"https://...\",\"description\":\"...\"}]}",
    "Eisen: datum verplicht, titel verplicht, website verplicht, website moet op hetzelfde domein staan, geen algemene agenda-pagina als titel, geen fictieve of geschatte events.",
    pageText ? `Websitetekst:\n${pageText.slice(0, 12000)}` : ""
  ].filter(Boolean).join("\n\n");
}

function parseJsonObject(value) {
  const text = clean(value);
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {}
  const match = text.match(/\{[\s\S]*\}/);
  if (!match) return {};
  try {
    return JSON.parse(match[0]);
  } catch {
    return {};
  }
}

function chatTextFromResponse(data = {}) {
  if (typeof data.output_text === "string") return data.output_text;
  const choiceText = data.choices?.[0]?.message?.content;
  if (typeof choiceText === "string") return choiceText;
  if (Array.isArray(choiceText)) return choiceText.map((part) => part.text || part.content || "").join(" ");
  return "";
}

function normalizeAiEvent(site, item = {}, source = "ChatGPT", rank = 0) {
  const website = validateInput(item.website || item.url || item.link || "", 500);
  if (!website || !sameHostUrl(website, site) || badWords.test(website)) return null;
  const title = cleanSerpTitle(item.title || item.name || "", site);
  const description = clean(item.description || item.summary || item.snippet || "");
  const siteHost = (() => { try { return new URL(site).hostname.toLowerCase().replace(/^www\./, ""); } catch { return ""; } })();
  const isOfferSite = /(^|\.)(socialdeal\.nl|groupon\.nl|ibood\.com)$/.test(siteHost);
  const dateText = clean(item.date || item.dateText || `${title} ${description}`);
  const eventDate = normalizeDate(dateText);
  if (!eventDate || !title || title.length < 3 || /^(agenda|programma|tickets?|contact)$/i.test(title)) return null;
  return {
    source,
    discoverySource: source,
    sourceType: "ai",
    site,
    title,
    date: eventDate,
    dateText,
    location: /(^|\.)paradiso\.nl$/i.test(siteHost) ? "Amsterdam" : clean(item.location || item.venue?.name || item.venue || ""),
    type: isOfferSite ? "Aanbiedingen" : clean(item.type || ""),
    description,
    website,
    url: website,
    rank
  };
}

function slugWords(value) {
  return clean(value)
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter((word) => word.length >= 3);
}

function mistralEvidenceOk(event, evidenceText = "") {
  const evidence = clean(evidenceText).toLowerCase();
  if (!evidence) return false;
  let urlOk = false;
  try {
    const url = new URL(event.url || event.website || "");
    const href = url.href.toLowerCase().replace(/\/$/, "");
    const path = decodeURIComponent(url.pathname || "").toLowerCase().replace(/\/$/, "");
    const pathWords = slugWords(path);
    urlOk = evidence.includes(href) || (path.length > 3 && evidence.includes(path)) || pathWords.some((word) => evidence.includes(word));
  } catch {
    urlOk = false;
  }
  const titleWords = slugWords(event.title).slice(0, 4);
  const titleOk = titleWords.length > 0 && titleWords.some((word) => evidence.includes(word));
  const dateOk = !event.date || evidence.includes(event.date) || evidence.includes(dutchShortDate(event.date));
  return Boolean(urlOk && titleOk && dateOk);
}

function aiEventsFromText(site, text, source = "ChatGPT", evidenceText = "") {
  const parsed = parseJsonObject(text);
  const events = Array.isArray(parsed.events) ? parsed.events : [];
  const normalized = [];
  const seen = new Set();
  events.forEach((item, index) => {
    const event = normalizeAiEvent(site, item, source, index + 1);
    if (!event) return;
    if (source === "Mistral" && evidenceText !== null && !mistralEvidenceOk(event, evidenceText)) return;
    const key = `${event.date}|${event.title.toLowerCase()}|${event.url.toLowerCase().replace(/\/$/, "")}`;
    if (seen.has(key)) return;
    seen.add(key);
    normalized.push(event);
  });
  return normalized;
}

async function callWithTimeout(url, init = {}, timeoutMs = 12000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function chatGptEventsForSite(env, site, body = {}) {
  const token = validateInput(env.CHATGPT_API_KEY || "", 500);
  if (!token) return { events: [], rawLog: [] };
  const rawLog = [{
    source: "ChatGPT",
    site,
    title: "ChatGPT zoekopdracht",
    date: "",
    url: site,
    status: "zoeken",
    rawText: "Website wordt via ChatGPT/webzoeklaag op eventvelden gecontroleerd."
  }];
  const requestBody = {
    model: validateInput(env.CHATGPT_MODEL || "gpt-4o-mini-search-preview", 80),
    messages: [
      { role: "system", content: "Je bent een strenge evenementen-extractor. Geef alleen controleerbare events terug als JSON." },
      { role: "user", content: aiPromptForSite(site, body, "ChatGPT") }
    ],
    web_search_options: { search_context_size: "medium" },
    response_format: { type: "json_object" },
    max_tokens: 1800
  };
  const openAiInit = (payload) => ({
    method: "POST",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
  let response = await callWithTimeout("https://api.openai.com/v1/chat/completions", openAiInit(requestBody), 14000);
  if (!response.ok) {
    const firstError = validateInput(await response.text(), 500);
    if (response.status === 400 && /response_format|json|unsupported|web_search_options/i.test(firstError)) {
      const retryBody = { ...requestBody };
      delete retryBody.response_format;
      if (/web_search_options/i.test(firstError)) delete retryBody.web_search_options;
      rawLog.push({ source: "ChatGPT", site, title: "ChatGPT retry", date: "", url: site, status: "opnieuw", rawText: firstError });
      response = await callWithTimeout("https://api.openai.com/v1/chat/completions", openAiInit(retryBody), 14000);
    }
    if (!response.ok) {
      rawLog.push({ source: "ChatGPT", site, title: "ChatGPT fout", date: "", url: site, status: `status ${response.status}`, rawText: validateInput(await response.text(), 500) });
      return { events: [], rawLog };
    }
  }
  const data = await response.json().catch(() => ({}));
  const text = chatTextFromResponse(data);
  const events = aiEventsFromText(site, text, "ChatGPT");
  rawLog.push({ source: "ChatGPT", site, title: "ChatGPT resultaat", date: "", url: site, status: "klaar", rawText: `${events.length} event-kandidaten` });
  events.slice(0, 20).forEach((event) => rawLog.push({
    source: "ChatGPT",
    site,
    title: event.title,
    date: event.date,
    url: event.url,
    status: "eventvelden gevonden",
    rawText: event.description || event.dateText || ""
  }));
  return { events, rawLog };
}

async function fetchSiteTextForAi(site) {
  try {
    const response = await callWithTimeout(site, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Evenementen AI Extractor)",
        "Accept": "text/html,application/xhtml+xml,text/plain"
      }
    }, 4500);
    if (!response.ok) return "";
    const html = await response.text();
    const linkLines = [];
    const seen = new Set();
    const hrefRe = /href\s*=\s*["']([^"']+)["']/gi;
    let match;
    while ((match = hrefRe.exec(html)) && linkLines.length < 240) {
      try {
        const href = new URL(match[1], site).href.split("#")[0];
        if (!sameHostUrl(href, site) || seen.has(href)) continue;
        seen.add(href);
        linkLines.push(href);
      } catch {}
    }
    return `${clean(html).slice(0, 10000)}\n\nLinks:\n${linkLines.join("\n")}`.slice(0, 14000);
  } catch {
    return "";
  }
}

async function mistralEventsForSite(env, site, body = {}) {
  const token = validateInput(env.MISTRAL_API_KEY || "", 500);
  if (!token) return { events: [], rawLog: [] };
  const rawLog = [{
    source: "Mistral",
    site,
    title: "Mistral analyse",
    date: "",
    url: site,
    status: "zoeken",
    rawText: "Startpagina wordt opgehaald en door Mistral op eventvelden geanalyseerd."
  }];
  const pageText = await fetchSiteTextForAi(site);
  if (!pageText) {
    rawLog.push({ source: "Mistral", site, title: "Geen websitetekst", date: "", url: site, status: "geen data", rawText: "Mistral slaat deze site over om fantasie te voorkomen." });
    return { events: [], rawLog };
  }
  const response = await callWithTimeout("https://api.mistral.ai/v1/chat/completions", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      model: validateInput(env.MISTRAL_MODEL || "mistral-small-latest", 80),
      messages: [
        { role: "system", content: "Je bent een strenge evenementen-extractor. Gebruik alleen de meegegeven tekst en geef JSON terug." },
        { role: "user", content: aiPromptForSite(site, body, "Mistral", pageText) }
      ],
      response_format: { type: "json_object" },
      temperature: 0,
      max_tokens: 1800
    })
  }, 14000);
  if (!response.ok) {
    rawLog.push({ source: "Mistral", site, title: "Mistral fout", date: "", url: site, status: `status ${response.status}`, rawText: validateInput(await response.text(), 400) });
    return { events: [], rawLog };
  }
  const data = await response.json().catch(() => ({}));
  const text = chatTextFromResponse(data);
  const unverified = aiEventsFromText(site, text, "Mistral", null);
  const events = aiEventsFromText(site, text, "Mistral", pageText);
  rawLog.push({ source: "Mistral", site, title: "Mistral resultaat", date: "", url: site, status: "klaar", rawText: `${events.length} geverifieerde event-kandidaten, ${Math.max(0, unverified.length - events.length)} afgewezen` });
  events.slice(0, 20).forEach((event) => rawLog.push({
    source: "Mistral",
    site,
    title: event.title,
    date: event.date,
    url: event.url,
    status: "eventvelden gevonden",
    rawText: event.description || event.dateText || ""
  }));
  return { events, rawLog };
}

async function serpApiLinksForSite(env, site, body = {}) {
  const token = validateInput(env.SERPAPI_TOKEN || "", 500);
  if (!token) return { links: [], rawLog: [], events: [] };
  const requestedHits = Math.max(20, Math.min(100, Number(body.maxEventsPerSite || body.minResults) || 20));
  const hitsPerQuery = Math.max(10, Math.min(50, requestedHits));
  const links = [];
  const rawLog = [];
  const events = [];
  const seenLinks = new Set();
  const seenEvents = new Set();
  const queries = serpSearchQueries(site, body);

  const queryResults = await Promise.all(queries.map(async (query, queryIndex) => {
    const queryLog = [{
      source: "SerpAPI",
      site,
      title: `Zoekopdracht ${queryIndex + 1}/${queries.length}`,
      date: "",
      url: "",
      status: "zoeken",
      rawText: query
    }];

    const params = new URLSearchParams({
      engine: "google",
      q: query,
      num: String(hitsPerQuery),
      api_key: token
    });
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5500);
    let response = null;
    try {
      response = await fetch(`https://serpapi.com/search.json?${params.toString()}`, {
        headers: { "Accept": "application/json" },
        signal: controller.signal
      });
    } catch (error) {
      queryLog.push({
        source: "SerpAPI",
        site,
        title: "SerpAPI timeout/fout",
        date: "",
        url: "",
        status: "fout",
        rawText: validateInput(error.message || "timeout", 300)
      });
      clearTimeout(timer);
      return { links: [], rawLog: queryLog, events: [] };
    }
    clearTimeout(timer);

    if (!response.ok) {
      queryLog.push({
        source: "SerpAPI",
        site,
        title: "SerpAPI fout",
        date: "",
        url: "",
        status: `status ${response.status}`,
        rawText: validateInput(await response.text(), 300)
      });
      return { links: [], rawLog: queryLog, events: [] };
    }

    const data = await response.json().catch(() => ({}));
    const queryLinks = [];
    const queryEvents = serpApiEventsFromData(site, data, queryIndex);
    const results = Array.isArray(data.organic_results) ? data.organic_results : [];
    results.forEach((item, index) => {
      const link = validateInput(item.link || "", 300);
      const usable = link && sameHostUrl(link, site) && !badWords.test(link);
      if (usable) {
        const key = link.toLowerCase().replace(/\/$/, "");
        if (!seenLinks.has(key)) {
          queryLinks.push(link);
        }
      }
      if (index < 12) {
        const candidate = serpApiEventCandidate(site, item, queryIndex, index + 1, "organic");
        queryLog.push({
          source: "SerpAPI",
          site,
          title: validateInput(item.title || "", 160),
          date: candidate?.date || "",
          url: link,
          status: candidate?.date ? "event-kandidaat" : (usable ? "bruikbare link" : "genegeerd"),
          rawText: validateInput(item.snippet || item.displayed_link || "", 300),
          rank: index + 1,
          query: queryIndex + 1
        });
      }
    });
    queryEvents.slice(0, 12).forEach((event) => {
      queryLog.push({
        source: "SerpAPI",
        site,
        title: event.title,
        date: event.date || event.dateText || "",
        url: event.url,
        status: event.date ? "eventvelden gevonden" : "event zonder datum",
        rawText: event.description || event.dateText || "",
        rank: event.rank,
        query: event.query
      });
    });
    return { links: queryLinks, rawLog: queryLog, events: queryEvents };
  }));

  queryResults.forEach((result) => {
    rawLog.push(...result.rawLog);
    result.links.forEach((link) => {
      const key = link.toLowerCase().replace(/\/$/, "");
      if (!seenLinks.has(key)) {
        seenLinks.add(key);
        links.push(link);
      }
    });
    result.events.forEach((event) => {
      const key = `${event.date || ""}|${event.title.toLowerCase()}|${event.url.toLowerCase().replace(/\/$/, "")}`;
      if (!seenEvents.has(key)) {
        seenEvents.add(key);
        events.push(event);
      }
    });
  });

  return {
    links: links.slice(0, Math.min(requestedHits * 2, 60)),
    rawLog,
    events: events.slice(0, requestedHits)
  };
}

async function enrichBodyWithSerpApi(env, body = {}) {
  const selected = validSites(body.sites);
  if (!selected.length) {
    return { body, serpApiAddedCount: 0, serpApiRawCount: 0, serpApiRawLog: [], serpApiEventsCount: 0, aiEventsCount: 0, aiRawCount: 0, aiRawLog: [] };
  }

  const priority = [...selected].sort((a, b) => {
    const aScore = /spotgroningen|vera-groningen|forum\.nl/i.test(a) ? 0 : 1;
    const bScore = /spotgroningen|vera-groningen|forum\.nl/i.test(b) ? 0 : 1;
    return aScore - bScore;
  }).slice(0, 14);

  const discovered = [];
  const serpRawLog = [];
  const serpEventCandidates = [];
  const aiRawLog = [];
  const aiEventCandidates = [];

  if (serpApiEnabled(env, body)) {
    const siteResults = await Promise.all(priority.map((site) => serpApiLinksForSite(env, site, body).catch((error) => ({
      links: [],
      events: [],
      rawLog: [{
        source: "SerpAPI",
        site,
        title: "Site zoeklaag mislukt",
        date: "",
        url: site,
        status: "fout",
        rawText: validateInput(error.message || "", 300)
      }]
    }))));
    siteResults.forEach((result) => {
      discovered.push(...result.links);
      serpRawLog.push(...result.rawLog);
      serpEventCandidates.push(...result.events);
    });
  }

  // Store AI work as functions, not already-running promises. The old code
  // started every request while constructing the array, so the apparent batches
  // did not actually limit concurrency and triggered provider-wide HTTP 429s.
  const aiJobs = [];
  if (chatGptEnabled(env, body)) {
    priority.forEach((site) => aiJobs.push({
      provider: "ChatGPT",
      site,
      run: () => chatGptEventsForSite(env, site, body)
    }));
  }
  if (mistralEnabled(env, body)) {
    priority.forEach((site) => aiJobs.push({
      provider: "Mistral",
      site,
      run: () => mistralEventsForSite(env, site, body)
    }));
  }
  if (aiJobs.length) {
    const disabledProviders = new Set();
    for (let offset = 0; offset < aiJobs.length; offset += 3) {
      const jobs = aiJobs.slice(offset, offset + 3);
      const runnable = jobs.filter((job) => !disabledProviders.has(job.provider));
      const skipped = jobs.filter((job) => disabledProviders.has(job.provider));

      skipped.forEach((job) => {
        aiRawLog.push({
          source: job.provider,
          site: job.site,
          title: `${job.provider} overgeslagen`,
          date: "",
          url: job.site,
          status: "overgeslagen",
          rawText: "Deze AI-bron is na een HTTP 429 voor de rest van deze scan gepauzeerd om herhaalde rate-limitfouten te voorkomen."
        });
      });

      const results = await Promise.all(runnable.map((job) => job.run().catch((error) => ({
        events: [],
        rawLog: [{
          source: job.provider,
          site: job.site,
          title: `${job.provider} mislukt`,
          date: "",
          url: job.site,
          status: "fout",
          rawText: validateInput(error.message || "", 300)
        }]
      }))));

      results.forEach((result, index) => {
        const job = runnable[index];
        aiRawLog.push(...result.rawLog);
        aiEventCandidates.push(...result.events);
        if (job.provider === "Mistral" && result.rawLog.some((row) =>
          /(?:status\s*429|rate.?limit|rate_limited)/i.test(`${row.status || ""} ${row.rawText || ""}`)
        )) {
          disabledProviders.add("Mistral");
        }
      });
    }
  }

  const uniqueDiscovered = validSites(discovered, 200);
  return {
    body: {
      ...body,
      sites: selected,
      serpApiLinks: uniqueDiscovered,
      serpApiRawLog: [...serpRawLog, ...aiRawLog],
      serpApiEvents: serpEventCandidates,
      aiEvents: aiEventCandidates
    },
    serpApiAddedCount: uniqueDiscovered.length,
    serpApiRawCount: serpRawLog.length,
    serpApiRawLog: serpRawLog.slice(0, 260),
    serpApiEventsCount: serpEventCandidates.length,
    aiRawCount: aiRawLog.length,
    aiRawLog: aiRawLog.slice(0, 260),
    aiEventsCount: aiEventCandidates.length
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

function normalizeDeleteTitle(value) {
  const months = "januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december|jan|feb|mrt|apr|jun|jul|aug|sep|sept|okt|nov|dec|january|february|march|april|may|june|july|august|september|october|november|december";
  const weekdays = "monday|tuesday|wednesday|thursday|friday|saturday|sunday|maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag";
  let title = clean(value).toLowerCase();
  title = title.replace(new RegExp(`^(?:${weekdays})\\s+\\d{1,2}\\s+(?:${months})\\s+`, "i"), "");
  title = title.split(/\b(mainstage|downstage|zienema|dansen)\b|\bticket\b|\bdoors\b|\bstart\b|\bsold out\b|\bkoop ticket\b/i)[0];
  return title.replace(/\b20\d{2}\b/g, "").replace(/^(concert|theater|film|bioscoop|markt|workshop|event|evenement):\s*/i, "").replace(/[^a-z0-9]+/g, " ").trim();
}

function eventDeleteKey(event) {
  const title = normalizeDeleteTitle(event.title || "");
  const website = clean(event.website || "").toLowerCase().replace(/\/$/, "");
  return `${event.date || ""}|${title}|${website}`;
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

  const location = /(^|\.)paradiso\.nl$/i.test(new URL(pageUrl).hostname) ? "Amsterdam" : (typeof node.location === "string"
    ? node.location
    : clean([node.location?.name, node.location?.address?.streetAddress, node.location?.address?.addressLocality].filter(Boolean).join(", ")));
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
  const location = /(^|\.)paradiso\.nl$/i.test(new URL(pageUrl).hostname) ? "Amsterdam" : (meta(html, "event:location") || new URL(pageUrl).hostname);

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

  const merged = dedupe([...(foundEvents || []), ...(options.clearArchive ? [] : existing.events)]);
  const mergedKeys = new Set(merged.map(eventKey));
  const archive = options.clearArchive ? [] : existing.archive.filter((event) => !mergedKeys.has(eventKey(event)));
  const payload = {
    schemaVersion: 2,
    updatedAt: new Date().toISOString(),
    events: merged,
    archive,
    siteResults: [],
    rawLog: []
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

async function saveEventFeedback(env, target = {}, vote = "bad") {
  const response = await githubFetch(env, "/contents/event-feedback.json?ref=main");
  let sha = null;
  let data = { schemaVersion: 1, updatedAt: new Date().toISOString(), feedback: [] };
  if (response.ok) { const file = await response.json(); sha = file.sha; try { data = JSON.parse(decodeBase64(file.content)); } catch (_) {} }
  else if (response.status !== 404) throw new Error("Feedbackbestand niet leesbaar: status " + response.status);
  if (!Array.isArray(data.feedback)) data.feedback = [];
  const title = clean(target.title || ""), date = clean(target.date || ""), website = clean(target.website || "").replace(/\/$/, ""), key = clean(target.key || ""), url = clean(target.url || target.website || "");
  const identity = key || [date, title.toLowerCase(), website.toLowerCase()].join("|");
  // Bewaar dubbelmelding en definitieve verwijdering apart; de tweede actie overschrijft de eerste niet.
  const feedbackKey = ["duplicate", "deleted"].includes(vote) ? `${identity}::${vote}` : identity;
  const now = new Date().toISOString();
  const index = data.feedback.findIndex(item => (item.key || [item.date, String(item.title || "").toLowerCase(), String(item.website || "").toLowerCase().replace(/\/$/, "")].join("|")) === feedbackKey);
  const record = {
    key: feedbackKey, vote, title, date, website, url, updatedAt: now,
    ...(vote === "edit" ? {
      originalTitle: clean(target.originalTitle || title),
      originalDate: clean(target.originalDate || date),
      originalFields: target.originalFields && typeof target.originalFields === "object" ? Object.fromEntries(["title","date","location","type","cost"].map(field => [field, clean(target.originalFields[field] || "")])) : {},
      corrections: {
        title: clean(target.corrections?.title || title),
        date: clean(target.corrections?.date || date),
        location: clean(target.corrections?.location || ""),
        type: clean(target.corrections?.type || ""),
        cost: clean(target.corrections?.cost || ""),
        website: clean(target.corrections?.website || url || website || "")
      }
    } : {})
  };
  if (index >= 0) data.feedback[index] = record; else data.feedback.push(record);
  data.schemaVersion = 1; data.updatedAt = now;
  const payload = { message: "Event link feedback: " + vote + " - " + (title || key || "event"), content: encodeBase64(JSON.stringify(data, null, 2)), branch: "main" };
  if (sha) payload.sha = sha;
  const saved = await githubFetch(env, "/contents/event-feedback.json", { method: "PUT", body: JSON.stringify(payload) });
  if (!saved.ok) throw new Error("Feedback opslaan mislukt: " + await saved.text());
  return { vote, feedbackCount: data.feedback.length };
}
async function learnEventUrlPattern(env, siteUrl, eventUrl, rejected = false) {
  let parsed;
  try { parsed = new URL(eventUrl); } catch (_) { return { learned: 0 }; }
  const hostOf = value => { try { return new URL(value).hostname.toLowerCase().replace(/^www\./, ""); } catch (_) { return ""; } };
  if (hostOf(siteUrl) !== hostOf(eventUrl)) return { learned: 0, reason: "Event-URL hoort niet bij de bronhost." };
  const path = parsed.pathname || "/";
  // A rejected item is excluded by its exact path. An approved/corrected item
  // teaches the general detail-page shape, replacing only its final slug.
  const escapeRegex = value => value.split("").map(ch => ".^$*+?()[]{}|".includes(ch) ? "\\" + ch : ch).join("");
  const segments = path.split("/").filter(Boolean);
  const pattern = rejected
    ? "^" + escapeRegex(path).replace(/\/$/, "") + "/?$"
    : "^/" + (segments.length > 1 ? segments.slice(0,-1).map(escapeRegex).join("/") + "/" : "") + "[^/?#]+/?$";
  const property = rejected ? "excludedUrlPatterns" : "eventUrlPatterns";
  const getFile = async name => {
    const response = await githubFetch(env, "/contents/" + name + "?ref=main");
    if (!response.ok) throw new Error(name + " niet leesbaar: status " + response.status);
    const file = await response.json();
    return { file, data: JSON.parse(decodeBase64(file.content || "")) };
  };
  const sitesResult = await getFile("sites.json");
  const sites = Array.isArray(sitesResult.data) ? sitesResult.data : [];
  const candidates = sites.filter(site => hostOf(site) === hostOf(siteUrl));
  if (!candidates.length) return { learned: 0 };
  const selected = candidates.sort((a,b) => { try{return new URL(b).pathname.length-new URL(a).pathname.length}catch{return 0} })[0];
  for (let attempt=1; attempt<=3; attempt++) {
    const current = await getFile("site-scraper-config.json");
    const registry = current.data && typeof current.data === "object" ? current.data : {schemaVersion:1,sites:{}};
    if (!registry.sites || typeof registry.sites !== "object") registry.sites = {};
    const config = registry.sites[selected] || {methodology:"",startUrls:[],exclusions:[],excludedUrls:[],adjustments:[],eventUrlPatterns:[],excludedUrlPatterns:[]};
    if (!Array.isArray(config[property])) config[property] = [];
    const learned = !config[property].includes(pattern);
    if (!learned) return { learned: 0, pattern, site: selected };
    config[property].push(pattern);
    config[property] = config[property].slice(rejected ? -200 : -100);
    registry.sites[selected] = config;
    registry.updatedAt = new Date().toISOString();
    const saved = await githubFetch(env, "/contents/site-scraper-config.json", {method:"PUT",body:JSON.stringify({
      message:(rejected?"Learn rejected event URL pattern: ":"Learn approved event URL pattern: ")+parsed.pathname.slice(0,80),
      content:encodeBase64(JSON.stringify(registry,null,2)),sha:current.file.sha,branch:"main"
    })});
    if (saved.ok) return { learned: 1, pattern, site: selected, property };
    if (saved.status === 409) continue;
    throw new Error("URL-patroon kon niet worden opgeslagen: "+validateInput(await saved.text(),300));
  }
  throw new Error("URL-patroon niet opgeslagen: configuratie veranderde gelijktijdig.");
}

async function learnSiteFieldAdjustments(env, siteUrl, originalFields, corrections, sourceUrl) {
  const fields = ["title", "date", "location", "type", "cost"];
  const additions = fields.map(field => ({
    field,
    find: clean(originalFields?.[field] || ""),
    replace: clean(corrections?.[field] || "")
  })).filter(rule => rule.find && rule.replace && rule.find !== rule.replace);
  if (!additions.length) return { learned: 0 };
  const getJsonFile = async path => {
    const response = await githubFetch(env, "/contents/" + path + "?ref=main");
    if (!response.ok) throw new Error(path + " niet leesbaar: status " + response.status);
    const file = await response.json();
    return { file, data: JSON.parse(decodeBase64(file.content || "")) };
  };
  const siteHost = value => { try { return new URL(value).hostname.toLowerCase().replace(/^www\./, ""); } catch (_) { return ""; } };
  const host = siteHost(siteUrl);
  const sitesResult = await getJsonFile("sites.json");
  const activeSites = Array.isArray(sitesResult.data) ? sitesResult.data : [];
  const candidates = activeSites.filter(site => siteHost(site) && siteHost(site) === host);
  if (!candidates.length) return { learned: 0, reason: "Geen actieve bronwebsite gevonden voor deze host." };
  const selected = candidates.sort((a,b) => {
    const pa = (()=>{try{return new URL(a).pathname.length}catch{return 0}})();
    const pb = (()=>{try{return new URL(b).pathname.length}catch{return 0}})();
    return pb-pa;
  })[0];
  let lastConflict = "";
  for (let attempt=1; attempt<=3; attempt++) {
    const current = await getJsonFile("site-scraper-config.json");
    const registry = current.data && typeof current.data === "object" ? current.data : { schemaVersion: 1, sites: {} };
    if (!registry.sites || typeof registry.sites !== "object") registry.sites = {};
    const config = registry.sites[selected] || { methodology:"", startUrls:[], exclusions:[], excludedUrls:[], adjustments:[] };
    if (!Array.isArray(config.adjustments)) config.adjustments = [];
    let learned = 0;
    for (const rule of additions) {
      const exists = config.adjustments.some(old => old.field === rule.field && clean(old.find).toLowerCase() === rule.find.toLowerCase() && String(old.replace || "") === rule.replace);
      if (!exists) {
        config.adjustments.push(rule);
        learned++;
      }
    }
    if (!learned) return { learned: 0, site: selected };
    config.adjustments = config.adjustments.slice(-100);
    registry.sites[selected] = config;
    registry.updatedAt = new Date().toISOString();
    const saved = await githubFetch(env, "/contents/site-scraper-config.json", { method:"PUT", body:JSON.stringify({
      message:"Learn field corrections from edited event: " + clean(corrections?.title || "event").slice(0,80),
      content:encodeBase64(JSON.stringify(registry,null,2)),
      sha:current.file.sha,
      branch:"main"
    })});
    if (saved.ok) return { learned, site:selected };
    const details = await saved.text();
    if (saved.status === 409) { lastConflict = details; continue; }
    throw new Error("Automatische veldregel kon niet worden opgeslagen: " + validateInput(details,300));
  }
  throw new Error("site-scraper-config.json wijzigde gelijktijdig; veldregels niet opgeslagen. " + validateInput(lastConflict,200));
}

async function saveEventCorrection(env, target = {}) {
  const corrections = target.corrections || {};
  const title = clean(corrections.title || "");
  const date = clean(corrections.date || "");
  const location = clean(corrections.location || "");
  const type = clean(corrections.type || "");
  const cost = clean(corrections.cost || "");
  const improvedUrl = clean(corrections.website || target.website || "");
  let parsedImprovedUrl;
  try { parsedImprovedUrl = new URL(improvedUrl); } catch (_) { throw new Error("Vul een geldige verbeterde URL in"); }
  if (!["http:", "https:"].includes(parsedImprovedUrl.protocol)) throw new Error("De URL moet beginnen met http:// of https://");
  if (!title || !date || !target.website) throw new Error("Titel, datum en oorspronkelijke website zijn verplicht");
  if (!location || !type || !cost) throw new Error("Vul plaats, type en prijs in; gebruik bijvoorbeeld 'Onbekend' als de prijs niet bekend is");

  const response = await githubFetch(env, "/contents/events.json?ref=main");
  if (!response.ok) throw new Error("events.json niet leesbaar: status " + response.status);
  const file = await response.json();
  const decodedEvents = decodeBase64(file.content || "");
  if (!decodedEvents.trim()) throw new Error("events.json is leeg; herstel eerst de evenementdata voordat je een correctie opslaat.");
  let data;
  try { data = JSON.parse(decodedEvents); } catch (_) { throw new Error("events.json bevat ongeldige JSON; correctie afgebroken om gegevensverlies te voorkomen."); }
  if (!Array.isArray(data) && (!data || (!Array.isArray(data.events) && !Array.isArray(data.archive)))) {
    throw new Error("events.json heeft een onbekende structuur; correctie afgebroken om gegevensverlies te voorkomen.");
  }
  const originalTitle = clean(target.originalTitle || target.title || "");
  const originalDate = clean(target.originalDate || target.date || "");
  const wantedUrl = clean(target.url || target.website || "").replace(/\/$/, "").toLowerCase();
  const wantedWebsite = clean(target.website || "").replace(/\/$/, "").toLowerCase();
  const hostOf = (value) => {
    try { return new URL(value).hostname.toLowerCase(); } catch (_) { return ""; }
  };
  const wantedHost = hostOf(wantedWebsite);
  const sameEvent = (event) => {
    const eventUrl = clean(event.website || "").replace(/\/$/, "").toLowerCase();
    const sameUrl = Boolean(wantedUrl && eventUrl === wantedUrl);
    const sameOriginal = clean(event.title || "").toLowerCase() === originalTitle.toLowerCase()
      && clean(event.date || "") === originalDate
      && (!wantedHost || eventUrl === wantedWebsite || hostOf(eventUrl) === wantedHost);
    return Boolean(sameUrl || sameOriginal);
  };
  let updatedCount = 0;
  let originalFields = null;
  const apply = (event) => {
    if (!sameEvent(event)) return event;
    updatedCount += 1;
    if (!originalFields) originalFields = Object.fromEntries(["title","date","location","type","cost"].map(field => [field, clean(event[field] || "")]));
    const locationChanged = clean(event.location || "").toLowerCase() !== location.toLowerCase();
    const corrected = { ...event, title, date, location, type, cost, website: improvedUrl, editedByUser: true, editedAt: new Date().toISOString() };
    // Remove coordinates tied to the old scraped location so the corrected location is used after reload.
    if (locationChanged) {
      delete corrected.lat;
      delete corrected.lon;
    }
    return corrected;
  };
  if (Array.isArray(data)) {
    data = data.map(apply);
  } else {
    if (Array.isArray(data.events)) data.events = data.events.map(apply);
    if (Array.isArray(data.archive)) data.archive = data.archive.map(apply);
  }
  if (!updatedCount) throw new Error("Het evenement is niet teruggevonden in events.json; vernieuw de pagina en probeer opnieuw");
  const payload = {
    message: "Correct event fields: " + title,
    content: encodeBase64(JSON.stringify(data, null, 2)),
    sha: file.sha,
    branch: "main"
  };
  const saved = await githubFetch(env, "/contents/events.json", { method: "PUT", body: JSON.stringify(payload) });
  if (!saved.ok) throw new Error("Bijgewerkte evenementdata opslaan mislukt: " + await saved.text());

  await saveEventFeedback(env, {
    ...target,
    title,
    date,
    originalTitle,
    originalDate,
    originalFields: originalFields || {},
    corrections: { title, date, location, type, cost, website: improvedUrl },
    url: target.url || target.website
  }, "edit");
  let learnedRules = { learned: 0 };
  try { learnedRules = await learnSiteFieldAdjustments(env, target.website, originalFields || {}, { title, date, location, type, cost }, improvedUrl); learnedRules.urlPattern = await learnEventUrlPattern(env, target.website, improvedUrl, false); }
  catch (error) { learnedRules = { ...learnedRules, warning: validateInput(error.message || String(error), 300) }; }
  return { updatedCount, corrections: { title, date, location, type, cost, website: improvedUrl }, learnedRules };
}
async function deleteEventFromGithub(env, target = {}) {
  // Persist the rejection first so a concurrent scrape already knows this event is unwanted.
  await saveEventFeedback(env, target, "deleted");
  let learnedUrlPattern = { learned: 0 };
  try { learnedUrlPattern = await learnEventUrlPattern(env, target.website || target.url || "", target.url || target.website || "", true); } catch (_) {}
  const wantedKey = clean(target.key || "");
  const wantedTitle = normalizeDeleteTitle(target.title || "");
  const wantedDate = clean(target.date || "");
  const wantedWebsite = clean(target.website || "").toLowerCase().replace(/\/$/, "");
  const shouldRemove = (event) => {
    const key = eventKey(event);
    const fullKey = eventDeleteKey(event);
    if (wantedKey && (key === wantedKey || fullKey === wantedKey)) return true;
    const sameDate = wantedDate && clean(event.date || "") === wantedDate;
    const sameTitle = wantedTitle && normalizeDeleteTitle(event.title || "") === wantedTitle;
    if (sameDate && sameTitle) return true;
    const exactTitle = clean(target.title || "").toLowerCase() === clean(event.title || "").toLowerCase();
    const sameWebsite = wantedWebsite && clean(event.website || "").toLowerCase().replace(/\/$/, "") === wantedWebsite;
    return Boolean(exactTitle && sameDate && (!wantedWebsite || sameWebsite));
  };

  // A scrape may update events.json between GET and PUT. On GitHub HTTP 409,
  // re-read the newest file and re-apply the deletion instead of losing it.
  let lastConflict = "";
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    const getResponse = await githubFetch(env, "/contents/events.json?ref=main");
    if (!getResponse.ok) throw new Error(`events.json niet gevonden: status ${getResponse.status}`);
    const file = await getResponse.json();
    const decodedText = decodeBase64(file.content || "");
    if (!decodedText.trim()) throw new Error("events.json is leeg; herstel eerst de evenementdata voordat je verwijdert.");
    let decoded;
    try { decoded = JSON.parse(decodedText); } catch (_) { throw new Error("events.json bevat ongeldige JSON; verwijderen afgebroken om gegevensverlies te voorkomen."); }
    if (!Array.isArray(decoded) && (!decoded || (!Array.isArray(decoded.events) && !Array.isArray(decoded.archive)))) {
      throw new Error("events.json heeft een onbekende structuur; verwijderen afgebroken om gegevensverlies te voorkomen.");
    }
    const existing = {
      events: Array.isArray(decoded) ? decoded : (Array.isArray(decoded.events) ? decoded.events : []),
      archive: Array.isArray(decoded?.archive) ? decoded.archive : [],
      siteResults: Array.isArray(decoded?.siteResults) ? decoded.siteResults : [],
      rawLog: Array.isArray(decoded?.rawLog) ? decoded.rawLog : []
    };
    const events = existing.events.filter((event) => !shouldRemove(event));
    const archive = existing.archive.filter((event) => !shouldRemove(event));
    const removedEvents = existing.events.length - events.length;
    const removedArchive = existing.archive.length - archive.length;
    if (removedEvents + removedArchive === 0) {
      throw new Error("Geen overeenkomstig evenement gevonden in events.json. De afkeuring is wel opgeslagen zodat de scraper het evenement voortaan kan overslaan.");
    }
    const payload = {
      schemaVersion: 2,
      updatedAt: new Date().toISOString(),
      events, archive,
      siteResults: existing.siteResults,
      rawLog: existing.rawLog
    };
    const putResponse = await githubFetch(env, "/contents/events.json", {
      method: "PUT",
      body: JSON.stringify({
        message: `Delete event ${clean(target.title || target.key || "item")}`,
        content: encodeBase64(JSON.stringify(payload, null, 2)),
        sha: file.sha,
        branch: "main"
      })
    });
    if (putResponse.ok) {
      return { removedEvents, removedArchive, remainingEvents: events.length, remainingArchive: archive.length, retries: attempt - 1, learnedUrlPattern };
    }
    const details = await putResponse.text();
    if (putResponse.status === 409) { lastConflict = details; continue; }
    throw new Error(details);
  }
  throw new Error("events.json veranderde tijdens vier verwijderpogingen door een gelijktijdige scraper-update. De afkeuring is bewaard; probeer definitief verwijderen opnieuw. " + validateInput(lastConflict, 250));
}

function workflowInputs(body, overrides = {}) {
  return {
    region: validateInput(body.region || "Groningen", 100),
    radius: validateInput(body.radius ?? "0", 10),
    category: validateInput(body.category || "all", 100),
    query: validateInput(body.query || "", 200),
    dateFrom: validateInput(body.dateFrom || "", 40),
    dateTo: validateInput(body.dateTo || "", 40),
    minResults: validateInput(body.minResults || "20", 10),
    maxEventsPerSite: validateInput(body.maxEventsPerSite || body.minResults || "20", 10),
    siteTimeLimitSeconds: validateInput(body.siteTimeLimitSeconds || "20", 10),
    sites: JSON.stringify(validSites(body.sites)),
    providers: JSON.stringify(body.providers && typeof body.providers === "object" ? body.providers : {}),
    serpApiLinks: JSON.stringify(validSites(body.serpApiLinks, 200)),
    serpApiRawLog: JSON.stringify(Array.isArray(body.serpApiRawLog) ? body.serpApiRawLog.slice(0, 220) : []),
    serpApiEvents: JSON.stringify(Array.isArray(body.serpApiEvents) ? body.serpApiEvents.slice(0, 260) : []),
    aiEvents: JSON.stringify(Array.isArray(body.aiEvents) ? body.aiEvents.slice(0, 260) : []),
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
    return {
      ok: true,
      method: "workflow_dispatch",
      workflowFile: dispatch.workflowFile,
      serpApiAddedCount: enriched.serpApiAddedCount,
      serpApiRawCount: enriched.serpApiRawCount,
      serpApiRawLog: enriched.serpApiRawLog || [],
      serpApiEventsCount: enriched.serpApiEventsCount || 0,
      aiRawCount: enriched.aiRawCount || 0,
      aiRawLog: enriched.aiRawLog || [],
      aiEventsCount: enriched.aiEventsCount || 0
    };
  }

  const fallback = await upsertRefreshRequest(env, refreshBody, overrides, dispatch);
  if (fallback.ok) {
    return {
      ok: true,
      method: "refresh_request",
      dispatchFailure: dispatch,
      serpApiAddedCount: enriched.serpApiAddedCount,
      serpApiRawCount: enriched.serpApiRawCount,
      serpApiRawLog: enriched.serpApiRawLog || [],
      serpApiEventsCount: enriched.serpApiEventsCount || 0,
      aiRawCount: enriched.aiRawCount || 0,
      aiRawLog: enriched.aiRawLog || [],
      aiEventsCount: enriched.aiEventsCount || 0
    };
  }

  return {
    ok: false,
    status: fallback.status || dispatch.status,
    details: `Workflow dispatch: ${dispatch.details || dispatch.status}. Refresh-request fallback: ${fallback.details || fallback.status}`
  };
}

function weatherCodeLabel(code) {
  const value = Number(code);
  if (value === 0) return "Helder";
  if (value === 1) return "Overwegend helder";
  if (value === 2) return "Gedeeltelijk bewolkt";
  if (value === 3) return "Bewolkt";
  if ([45, 48].includes(value)) return "Mist";
  if ([51, 53, 55].includes(value)) return "Motregen";
  if ([56, 57].includes(value)) return "IJzel/motregen";
  if ([61, 63, 65].includes(value)) return "Regen";
  if ([66, 67].includes(value)) return "IJzel";
  if ([71, 73, 75, 77].includes(value)) return "Sneeuw";
  if ([80, 81, 82].includes(value)) return "Regenbuien";
  if ([85, 86].includes(value)) return "Sneeuwbuien";
  if ([95, 96, 99].includes(value)) return "Onweer";
  return "Weerbeeld onbekend";
}

async function fetchWeatherForecast(region) {
  const place = String(region || "Groningen").trim() || "Groningen";
  const geocodeUrl = new URL("https://geocoding-api.open-meteo.com/v1/search");
  geocodeUrl.searchParams.set("name", place);
  geocodeUrl.searchParams.set("count", "10");
  geocodeUrl.searchParams.set("language", "nl");
  geocodeUrl.searchParams.set("format", "json");
  const geocodeResponse = await fetch(geocodeUrl.toString(), { headers: { "Accept": "application/json" } });
  if (!geocodeResponse.ok) throw new Error("Locatie kon niet worden opgezocht");
  const geocode = await geocodeResponse.json();
  const places = Array.isArray(geocode.results) ? geocode.results : [];
  const match = places.find((item) => String(item.country_code || "").toUpperCase() === "NL") || places[0];
  if (!match || !Number.isFinite(Number(match.latitude)) || !Number.isFinite(Number(match.longitude))) {
    throw new Error("Geen weerlocatie gevonden voor " + place);
  }

  const forecastUrl = new URL("https://api.open-meteo.com/v1/forecast");
  forecastUrl.searchParams.set("latitude", String(match.latitude));
  forecastUrl.searchParams.set("longitude", String(match.longitude));
  forecastUrl.searchParams.set("daily", "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,uv_index_max");
  forecastUrl.searchParams.set("hourly", "precipitation_probability,precipitation,temperature_2m,weather_code");
  forecastUrl.searchParams.set("forecast_days", "7");
  forecastUrl.searchParams.set("timezone", "Europe/Amsterdam");
  const forecastResponse = await fetch(forecastUrl.toString(), { headers: { "Accept": "application/json" } });
  if (!forecastResponse.ok) throw new Error("Weersverwachting kon niet worden opgehaald");
  const forecast = await forecastResponse.json();
  const daily = forecast.daily || {};
  const dates = Array.isArray(daily.time) ? daily.time : [];
  const rows = dates.map((date, index) => {
    const weatherCode = Number(daily.weather_code?.[index]);
    return {
      date,
      tempMin: Number.isFinite(Number(daily.temperature_2m_min?.[index])) ? Math.round(Number(daily.temperature_2m_min[index])) : null,
      tempMax: Number.isFinite(Number(daily.temperature_2m_max?.[index])) ? Math.round(Number(daily.temperature_2m_max[index])) : null,
      rainMM: Number.isFinite(Number(daily.precipitation_sum?.[index])) ? Math.round(Number(daily.precipitation_sum[index]) * 10) / 10 : null,
      rainChance: Number.isFinite(Number(daily.precipitation_probability_max?.[index])) ? Number(daily.precipitation_probability_max[index]) : null,
      windMax: Number.isFinite(Number(daily.wind_speed_10m_max?.[index])) ? Math.round(Number(daily.wind_speed_10m_max[index])) : null,
      uvMax: Number.isFinite(Number(daily.uv_index_max?.[index])) ? Math.round(Number(daily.uv_index_max[index]) * 10) / 10 : null,
      weatherCode,
      summary: weatherCodeLabel(weatherCode)
    };
  });
  if (!rows.length) throw new Error("De weerdienst heeft geen dagverwachting teruggegeven");
  const today = rows[0];
  const hourly = forecast.hourly || {};
  const hourlyTimes = Array.isArray(hourly.time) ? hourly.time : [];
  const now = new Date();
  const nextHours = hourlyTimes.map((time, index) => ({ time, index }))
    .filter((item) => item.time.startsWith(today.date) && new Date(item.time) >= now)
    .slice(0, 3)
    .map(({ time, index }) => ({
      time,
      rainMM: Number.isFinite(Number(hourly.precipitation?.[index])) ? Number(hourly.precipitation[index]) : null,
      rainChance: Number.isFinite(Number(hourly.precipitation_probability?.[index])) ? Number(hourly.precipitation_probability[index]) : null,
      temp: Number.isFinite(Number(hourly.temperature_2m?.[index])) ? Number(hourly.temperature_2m[index]) : null,
      weatherCode: Number(hourly.weather_code?.[index]),
      summary: weatherCodeLabel(hourly.weather_code?.[index])
    }));
  return {
    ok: true,
    location: match.name || place,
    summary: today.summary,
    days: rows.length,
    tempMin: today.tempMin,
    tempMax: today.tempMax,
    rainChance: today.rainChance,
    rainTotalMM: today.rainMM,
    windMax: today.windMax,
    uvMax: today.uvMax,
    weatherCode: today.weatherCode,
    daily: rows,
    nextHours,
    timezone: forecast.timezone || "Europe/Amsterdam",
    updatedAt: new Date().toISOString(),
    source: "Open-Meteo"
  };
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
        chatGptConfigured: Boolean(env.CHATGPT_API_KEY),
        mistralConfigured: Boolean(env.MISTRAL_API_KEY),
        acceptedTokenNames: ["GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"],
        githubOwner: env.GITHUB_OWNER || "mrsjonnie",
        githubRepo: env.GITHUB_REPO || "evenementen",
        configuredWorkflowFile: env.GITHUB_WORKFLOW_FILE || "update-events.yml",
        fallbackWorkflowFiles: ["update-events.yml", "daily_update.yml"]
      }, 200);
    }

    if (path === "/weather" && request.method === "GET") {
      try {
        const forecast = await fetchWeatherForecast(url.searchParams.get("region") || "Groningen");
        return json(forecast, 200);
      } catch (error) {
        return json({
          ok: false,
          error: "Actueel weer tijdelijk niet beschikbaar",
          details: validateInput(error?.message || String(error), 300),
          source: "Open-Meteo"
        }, 502);
      }
    }

    if (path === "/cancel" && request.method === "POST") {
      try {
        const config = githubConfig(env);
        const workflowPath = encodeURIComponent(config.workflowFile);
        const runsResponse = await githubFetch(
          env,
          `/actions/workflows/${workflowPath}/runs?branch=main&per_page=30`
        );
        if (!runsResponse.ok) {
          const details = validateInput(await runsResponse.text(), 700);
          return json({
            ok: false,
            error: "GitHub-runs konden niet worden opgehaald",
            status: runsResponse.status,
            details: /resource not accessible|forbidden|bad credentials/i.test(details)
              ? "De GitHub-token van de Worker mist waarschijnlijk Actions-leesrechten (actions: read)."
              : details
          }, 502);
        }
        const runData = await runsResponse.json().catch(() => ({}));
        const runs = Array.isArray(runData.workflow_runs) ? runData.workflow_runs : [];
        const activeRuns = runs
          .filter((run) => ["in_progress", "queued", "waiting", "requested"].includes(String(run.status || "")))
          .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
        if (!activeRuns.length) {
          return json({ ok: true, cancelled: 0, message: "Geen actieve of ingeplande scraper-run gevonden." }, 200);
        }

        // Request cancellation concurrently and verify whether GitHub has actually
        // ended each run; accepting a cancel request is not the same as stopping.
        const results = await Promise.all(activeRuns.map(async (run) => {
          const cancelResponse = await githubFetch(env, `/actions/runs/${Number(run.id)}/cancel`, { method: "POST" });
          if (cancelResponse.status !== 202 && cancelResponse.status !== 409) {
            return { id: run.id, status: "mislukt", details: validateInput(await cancelResponse.text(), 300) };
          }
          let finalStatus = String(run.status || "unknown");
          let conclusion = "";
          for (let attempt = 0; attempt < 6; attempt += 1) {
            if (attempt > 0) await new Promise((resolve) => setTimeout(resolve, 1000));
            try {
              const stateResponse = await githubFetch(env, `/actions/runs/${Number(run.id)}`);
              if (stateResponse.ok) {
                const state = await stateResponse.json();
                finalStatus = String(state.status || finalStatus);
                conclusion = String(state.conclusion || conclusion || "");
                if (finalStatus === "completed") break;
              }
            } catch {}
          }
          return { id: run.id, status: finalStatus === "completed" ? "gestopt" : "stop aangevraagd", conclusion, finalStatus };
        }));
        const cancelled = results.filter((item) => item.status !== "mislukt").length;
        const stopped = results.filter((item) => item.status === "gestopt").length;
        const stillStopping = results.filter((item) => item.status === "stop aangevraagd").length;
        return json({
          ok: cancelled > 0,
          cancelled,
          stopped,
          stillStopping,
          results,
          message: cancelled
            ? `Stop aangevraagd voor ${cancelled} scraper-run(s): ${stopped} beëindigd bevestigd, ${stillStopping} nog niet als beëindigd bevestigd.`
            : "GitHub heeft geen run kunnen annuleren. Controleer de Actions-rechten van de Worker-token."
        }, cancelled ? 200 : 502);
      } catch (error) {
        return json({ ok: false, error: "Annuleren is mislukt", details: validateInput(error.message, 700) }, 500);
      }
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
          serpApiRawCount: result.serpApiRawCount || 0,
          serpApiRawLog: result.serpApiRawLog || [],
          serpApiEventsCount: result.serpApiEventsCount || 0,
          aiRawCount: result.aiRawCount || 0,
          aiRawLog: result.aiRawLog || [],
          aiEventsCount: result.aiEventsCount || 0,
          scrapedCount: 0,
          totalSaved: 0,
          refreshError: result.ok ? null : (result.error || result.details || "Onbekende fout")
        }, 200);
      } catch (e) {
        return json({ error: "Fout bij verwijderen", details: e.message }, 500);
      }
    }

    if (path === "/site-config" && request.method === "GET") {
      try {
        const sitesResponse = await githubFetch(env, "/contents/sites.json?ref=main");
        if (!sitesResponse.ok) throw new Error("sites.json niet leesbaar: status " + sitesResponse.status);
        const sitesFile = await sitesResponse.json();
        const activeSites = JSON.parse(decodeBase64(sitesFile.content || ""));
        if (!Array.isArray(activeSites)) throw new Error("sites.json bevat geen lijst");
        const configResponse = await githubFetch(env, "/contents/site-scraper-config.json?ref=main");
        let fileSha = null;
        let registry = { schemaVersion: 1, updatedAt: null, sites: {} };
        if (configResponse.ok) {
          const file = await configResponse.json();
          fileSha = file.sha;
          try { registry = JSON.parse(decodeBase64(file.content || "")); } catch (_) { throw new Error("site-scraper-config.json bevat ongeldige JSON"); }
        } else if (configResponse.status !== 404) throw new Error("site-scraper-config.json niet leesbaar: status " + configResponse.status);
        if (!registry || typeof registry !== "object" || !registry.sites || typeof registry.sites !== "object") registry = { schemaVersion: 1, updatedAt: null, sites: {} };
        const active = new Set(activeSites.filter(site => typeof site === "string" && /^https?:\/\//i.test(site)));
        const cleaned = {};
        for (const site of active) {
          const old = registry.sites[site] || { exclusions: ["contact", "privacy", "cookie", "voorwaarden", "login", "account", "nieuwsbrief", "facebook", "instagram", "linkedin"] };
          cleaned[site] = {
            methodology: validateInput(old.methodology || "", 4000),
            startUrls: Array.isArray(old.startUrls) ? old.startUrls.map(value => validateInput(value, 500)).filter(value => /^https?:\/\//i.test(value) && sameHostUrl(value, site)).slice(0, 80) : [],
            exclusions: Array.isArray(old.exclusions) ? old.exclusions.map(value => validateInput(value, 120)).filter(Boolean).slice(0, 100) : [],
            excludedUrls: Array.isArray(old.excludedUrls) ? old.excludedUrls.map(value => validateInput(value, 500)).filter(Boolean).slice(0, 200) : [],
            adjustments: Array.isArray(old.adjustments) ? old.adjustments.filter(item => item && ["title","date","description","location","type","cost","website"].includes(item.field)).map(item => ({ field: item.field, find: validateInput(item.find, 300), replace: validateInput(item.replace, 500) })).filter(item => item.find).slice(0, 100) : [], eventUrlPatterns: Array.isArray(old.eventUrlPatterns) ? old.eventUrlPatterns.map(value => validateInput(value, 500)).filter(Boolean).slice(0, 100) : [], excludedUrlPatterns: Array.isArray(old.excludedUrlPatterns) ? old.excludedUrlPatterns.map(value => validateInput(value, 500)).filter(Boolean).slice(0, 200) : []
          };
        }
        const changed = JSON.stringify(cleaned) !== JSON.stringify(registry.sites || {});
        const updatedAt = changed ? new Date().toISOString() : (registry.updatedAt || null);
        if (changed) {
          const saveResponse = await githubFetch(env, "/contents/site-scraper-config.json", { method: "PUT", body: JSON.stringify({
            message: "Synchronize scraper configuration with active websites",
            content: encodeBase64(JSON.stringify({ schemaVersion: 1, updatedAt, sites: cleaned }, null, 2)),
            ...(fileSha ? { sha: fileSha } : {}),
            branch: "main"
          }) });
          if (!saveResponse.ok) throw new Error("Siteconfiguraties opschonen/opbouwen mislukt: " + validateInput(await saveResponse.text(), 500));
        }
        return json({ ok: true, activeSites: [...active], configs: cleaned, updatedAt }, 200);
      } catch (e) {
        return json({ ok: false, error: "Websiteconfiguraties laden mislukt", details: validateInput(e.message || String(e), 700) }, 500);
      }
    }
    if (path === "/site-config" && request.method === "POST") {
      try {
        const body = await request.json().catch(() => ({}));
        const site = validateInput(body.site, 500);
        const config = body.config && typeof body.config === "object" ? body.config : {};
        const sitesResponse = await githubFetch(env, "/contents/sites.json?ref=main");
        if (!sitesResponse.ok) throw new Error("sites.json niet leesbaar: status " + sitesResponse.status);
        const sitesFile = await sitesResponse.json();
        const activeSites = JSON.parse(decodeBase64(sitesFile.content || ""));
        if (!Array.isArray(activeSites) || !activeSites.includes(site)) return json({ ok: false, error: "Deze website staat niet in de actieve sites.json-lijst; configuratie niet opgeslagen." }, 409);
        const configResponse = await githubFetch(env, "/contents/site-scraper-config.json?ref=main");
        let fileSha = null;
        let registry = { schemaVersion: 1, sites: {} };
        if (configResponse.ok) {
          const file = await configResponse.json();
          fileSha = file.sha;
          registry = JSON.parse(decodeBase64(file.content || ""));
        } else if (configResponse.status !== 404) throw new Error("Configuratiebestand niet leesbaar: status " + configResponse.status);
        if (!registry.sites || typeof registry.sites !== "object") registry.sites = {};
        registry.sites[site] = {
          methodology: validateInput(config.methodology || "", 4000),
          startUrls: Array.isArray(config.startUrls) ? config.startUrls.map(value => validateInput(value, 500)).filter(value => /^https?:\/\//i.test(value) && sameHostUrl(value, site)).slice(0, 80) : [],
          exclusions: Array.isArray(config.exclusions) ? config.exclusions.map(value => validateInput(value, 120)).filter(Boolean).slice(0, 100) : [],
          excludedUrls: Array.isArray(config.excludedUrls) ? config.excludedUrls.map(value => validateInput(value, 500)).filter(Boolean).slice(0, 200) : [],
          adjustments: Array.isArray(config.adjustments) ? config.adjustments.filter(item => item && ["title","date","description","location","type","cost","website"].includes(item.field)).map(item => ({ field: item.field, find: validateInput(item.find, 300), replace: validateInput(item.replace, 500) })).filter(item => item.find).slice(0, 100) : [], eventUrlPatterns: Array.isArray(config.eventUrlPatterns) ? config.eventUrlPatterns.map(value => validateInput(value, 500)).filter(Boolean).slice(0, 100) : [], excludedUrlPatterns: Array.isArray(config.excludedUrlPatterns) ? config.excludedUrlPatterns.map(value => validateInput(value, 500)).filter(Boolean).slice(0, 200) : []
        };
        const active = new Set(activeSites);
        for (const configuredSite of Object.keys(registry.sites)) if (!active.has(configuredSite)) delete registry.sites[configuredSite];
        const updatedAt = new Date().toISOString();
        const saved = await githubFetch(env, "/contents/site-scraper-config.json", { method: "PUT", body: JSON.stringify({
          message: "Update scraper rules for " + site,
          content: encodeBase64(JSON.stringify({ schemaVersion: 1, updatedAt, sites: registry.sites }, null, 2)),
          ...(fileSha ? { sha: fileSha } : {}),
          branch: "main"
        }) });
        if (!saved.ok) throw new Error("Opslaan mislukt: " + validateInput(await saved.text(), 500));
        return json({ ok: true, updatedAt, configs: registry.sites }, 200);
      } catch (e) {
        return json({ ok: false, error: "Websiteconfiguratie opslaan mislukt", details: validateInput(e.message || String(e), 700) }, 500);
      }
    }

    if (path === "/event-feedback" && request.method === "GET") {
      try {
        const response = await githubFetch(env, "/contents/event-feedback.json?ref=main");
        if (response.status === 404) return json({ ok: true, feedback: [] }, 200);
        if (!response.ok) throw new Error("Feedbackbestand niet leesbaar: status " + response.status);
        const file = await response.json();
        let decoded;
        try { decoded = decodeBase64(file.content || ""); } catch (_) { throw new Error("Feedbackbestand kan niet worden gedecodeerd"); }
        let data;
        try { data = JSON.parse(decoded); } catch (_) { throw new Error("event-feedback.json is leeg of bevat ongeldige JSON"); }
        return json({ ok: true, updatedAt: data.updatedAt || null, feedback: Array.isArray(data.feedback) ? data.feedback : [] }, 200);
      } catch (e) { return json({ error: "Logica-regels konden niet worden geladen", details: e.message }, 500); }
    }
    if (path === "/event-feedback-delete" && request.method === "POST") {
      try {
        const body = await request.json().catch(() => ({}));
        const key = validateInput(body.key, 1200);
        if (!key) return json({ error: "Regelsleutel ontbreekt" }, 400);
        const response = await githubFetch(env, "/contents/event-feedback.json?ref=main");
        if (!response.ok) throw new Error("Feedbackbestand niet leesbaar: status " + response.status);
        const file = await response.json();
        const data = JSON.parse(decodeBase64(file.content));
        const entries = Array.isArray(data.feedback) ? data.feedback : [];
        const filtered = entries.filter(item => String(item.key || "") !== key);
        if (filtered.length === entries.length) return json({ error: "Regel niet gevonden; lijst is mogelijk al gewijzigd." }, 404);
        data.feedback = filtered;
        data.updatedAt = new Date().toISOString();
        const saved = await githubFetch(env, "/contents/event-feedback.json", { method: "PUT", body: JSON.stringify({
          message: "Remove unwanted scraper feedback rule: " + key.slice(0, 80),
          content: encodeBase64(JSON.stringify(data, null, 2)),
          sha: file.sha,
          branch: "main"
        }) });
        if (!saved.ok) throw new Error("Regel verwijderen mislukt: " + await saved.text());
        return json({ ok: true, deleted: 1, remaining: filtered.length }, 200);
      } catch (e) { return json({ error: "Regel verwijderen mislukt", details: e.message }, 500); }
    }
    if (path === "/event-feedback" && request.method === "POST") {
      try {
        const body = await request.json().catch(() => ({}));
        if (!body || !body.title || !body.date || !body.website) return json({ error: "Titel, datum en website zijn verplicht" }, 400);
        const result = await saveEventFeedback(env, body, ["good", "duplicate", "deleted"].includes(body.vote) ? body.vote : "bad");
        return json({ ok: true, ...result }, 200);
      } catch (e) { return json({ error: "Feedback opslaan mislukt", details: e.message }, 500); }
    }
    if (path === "/event-edit" && request.method === "POST") {
      try {
        const body = await request.json().catch(() => ({}));
        if (!body || !body.website || !body.corrections) return json({ error: "Website en aangepaste velden zijn verplicht" }, 400);
        const result = await saveEventCorrection(env, body);
        return json({ ok: true, ...result }, 200);
      } catch (e) {
        return json({ error: "Evenement aanpassen mislukt", details: e.message }, 500);
      }
    }
    if (path === "/delete-event" && request.method === "POST") {
      try {
        const body = await request.json().catch(() => ({}));
        const result = await deleteEventFromGithub(env, body || {});
        return json({ ok: true, ...result }, 200);
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
        serpApiRawCount: result.serpApiRawCount || 0,
        serpApiRawLog: result.serpApiRawLog || [],
        serpApiEventsCount: result.serpApiEventsCount || 0,
        aiRawCount: result.aiRawCount || 0,
        aiRawLog: result.aiRawLog || [],
        aiEventsCount: result.aiEventsCount || 0,
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
