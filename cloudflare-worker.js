export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') return new Response(null, { headers: corsHeaders() });
    if (request.method !== 'POST') return json({ error: 'Method not allowed' }, 405);
    try {
      const body = await request.json();
      const payload = {
        ref: 'main',
        inputs: {
          region: String(body.region || 'Groningen').slice(0, 100),
          date: String(body.date || '').slice(0, 40),
          radius: String(body.radius || '140').slice(0, 10),
          category: String(body.category || 'all').slice(0, 100),
          query: String(body.query || '').slice(0, 200)
        }
      };
      const gh = await fetch(`https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW_FILE}/dispatches`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${env.GITHUB_TOKEN}`,
          'Accept': 'application/vnd.github+json',
          'Content-Type': 'application/json',
          'User-Agent': 'evenementen-worker'
        },
        body: JSON.stringify(payload)
      });
      if (gh.status !== 204) return json({ error: 'GitHub dispatch mislukt', details: await gh.text() }, 500);
      return json({ ok: true, message: 'Workflow gestart' }, 200);
    } catch (e) {
      return json({ error: e.message || 'Onbekende fout' }, 500);
    }
  }
};
function corsHeaders(){return {'Access-Control-Allow-Origin':'*','Access-Control-Allow-Methods':'POST, OPTIONS','Access-Control-Allow-Headers':'Content-Type'}}
function json(data,status=200){return new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json',...corsHeaders()}})}
