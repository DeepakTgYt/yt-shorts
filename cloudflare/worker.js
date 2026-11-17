// yt-cron: exact-time trigger for the yt-shorts GitHub workflow (IST slots) + instant breaking-vacancy check.
// Cron: every 10 min. Token for GitHub lives in KV "gh_token" (set once via /setup?k=SETUP_KEY) or env GH_TOKEN.
const REPO = "DeepakTgYt/yt-shorts";
const WF = "shorts.yml";
const SLOTS = ["07:30", "09:00", "10:30", "12:00", "13:30", "15:30", "17:30", "19:00", "20:30"];
const CHANNELS = ["SSC_Job_Alert", "Railway_Job_Notification_Quiz", "Bihar_Job_Notification_Quiz", "Army_Agniveer_Bharti_Alert"];

function ist(d = new Date()) {
  const t = new Date(d.getTime() + 5.5 * 3600e3);
  const hh = String(t.getUTCHours()).padStart(2, "0"), mm = String(t.getUTCMinutes()).padStart(2, "0");
  return { hh: t.getUTCHours(), hm: `${hh}:${mm}`, day: t.toISOString().slice(0, 10) };
}

async function token(env) {
  return (env.GH_TOKEN || (await env.KV.get("gh_token")) || "").trim();
}

async function log(env, msg) {
  const l = JSON.parse((await env.KV.get("log")) || "[]");
  l.unshift(`${new Date().toISOString()} ${msg}`);
  await env.KV.put("log", JSON.stringify(l.slice(0, 60)));
}

async function dispatch(env, mode, why) {
  const tok = await token(env);
  if (!tok) { await log(env, `skip ${mode} (${why}): no GitHub token yet`); return false; }
  for (let i = 0; i < 3; i++) {
    const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WF}/dispatches`, {
      method: "POST",
      headers: { Authorization: `Bearer ${tok}`, Accept: "application/vnd.github+json", "User-Agent": "yt-cron",
                 "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main", inputs: { mode, force: "0" } }),
    });
    if (r.status === 204) { await log(env, `dispatched ${mode} (${why})`); return true; }
    await log(env, `dispatch ${mode} failed ${r.status} ${(await r.text()).slice(0, 120)}`);
    if (r.status === 401 || r.status === 403 || r.status === 404) return false;
    await new Promise((res) => setTimeout(res, 3000));
  }
  return false;
}

function posts(text) {
  const m = text.match(/(?:Total (?:Vacancy|Posts?)|कुल पद)[^:\n]*:\s*([\d,]+)/i) ||
            text.match(/(?:for|of)\s+([\d,]{4,})\s+/i) || text.match(/([\d,]{4,})\s+(?:Posts?|पद|Vacanc)/i);
  return m ? parseInt(m[1].replace(/\D/g, "").slice(0, 7), 10) || 0 : 0;
}

async function breakingCheck(env) {
  const now = Date.now();
  for (const ch of CHANNELS) {
    try {
      const h = await (await fetch(`https://t.me/s/${ch}`, { headers: { "User-Agent": "Mozilla/5.0" } })).text();
      for (const blk of h.split('<div class="tgme_widget_message_wrap').slice(1)) {
        const tm = blk.match(/<time datetime="([^"]+)"/);
        const tx = blk.match(/js-message_text" dir="auto">([\s\S]*?)<\/div>/);
        if (!tm || !tx) continue;
        if (now - Date.parse(tm[1]) > 25 * 60e3) continue;  // only posts from the last 25 min
        const t = tx[1].replace(/<br\s*\/?>/g, "\n").replace(/<[^>]+>/g, "");
        if (!/NEW VACANCY|नई भर्ती/.test(t)) continue;
        const n = posts(t);
        const key = blk.match(/tgme_widget_message_date" href="([^"]+)"/)?.[1] || t.slice(0, 50);
        if (n >= 1000 && !(await env.KV.get("seen:" + key))) {
          await env.KV.put("seen:" + key, "1", { expirationTtl: 7 * 86400 });
          await dispatch(env, "breaking", `${n} posts ${key}`);
          return;
        }
      }
    } catch (e) { await log(env, `tg fetch ${ch} failed ${String(e).slice(0, 80)}`); }
  }
}

export default {
  async scheduled(ev, env, ctx) {
    const { hh, hm, day } = ist(new Date(ev.scheduledTime));
    if (SLOTS.includes(hm) && !(await env.KV.get(`slot:${day} ${hm}`))) {
      await env.KV.put(`slot:${day} ${hm}`, "1", { expirationTtl: 3 * 86400 });
      ctx.waitUntil(dispatch(env, "auto", `slot ${hm} IST`));
    }
    if (hh >= 7 && hh < 23) ctx.waitUntil(breakingCheck(env));
  },
  async fetch(req, env) {
    const u = new URL(req.url);
    const ok = u.searchParams.get("k") === env.SETUP_KEY;
    if (u.pathname === "/setup" && ok) {
      if (req.method === "POST") {
        const f = await req.formData();
        const tok = String(f.get("token") || "").trim();
        const r = await fetch(`https://api.github.com/repos/${REPO}`, {
          headers: { Authorization: `Bearer ${tok}`, "User-Agent": "yt-cron", Accept: "application/vnd.github+json" } });
        if (r.status !== 200) return html(`<h2>❌ Token काम नहीं कर रहा (${r.status})</h2><p>repo + workflow वाला token डालें।</p><a href="?k=${env.SETUP_KEY}">फिर से</a>`);
        await env.KV.put("gh_token", tok);
        await log(env, "GitHub token saved");
        return html("<h2>✅ हो गया! Token सेव हो गया।</h2><p>अब यह पेज बंद कर दें।</p>");
      }
      return html(`<h2>GitHub token डालें</h2><form method="post"><input name="token" type="password" style="width:100%;font-size:18px;padding:10px" placeholder="ghp_..." required><br><br><button style="font-size:20px;padding:10px 30px">Save</button></form>`);
    }
    if (u.pathname === "/status" && ok) {
      return new Response(JSON.stringify({ ist: ist(), has_token: !!(await token(env)), log: JSON.parse((await env.KV.get("log")) || "[]") }, null, 1),
        { headers: { "content-type": "application/json; charset=utf-8" } });
    }
    if (u.pathname === "/run" && ok) {
      const mode = u.searchParams.get("mode") || "auto";
      return new Response(String(await dispatch(env, mode, "manual")));
    }
    return new Response("ok");
  },
};

function html(b) {
  return new Response(`<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><body style="font-family:sans-serif;max-width:520px;margin:40px auto;padding:0 16px">${b}</body>`,
    { headers: { "content-type": "text/html; charset=utf-8" } });
}
