// Sangh Wire — news function
// Free & unlimited: Google News RSS on every Refresh (no API key needed)
// + daily SerpAPI results saved by GitHub Actions in data/serp.json (no quota used by clicks)
// Last 7 days only. Works on Vercel (/api/news) and Netlify (netlify/functions/news.mjs).

import { webcrypto } from "node:crypto";
if (!globalThis.crypto) globalThis.crypto = webcrypto;
import { PEOPLE, DIRECT_FEEDS, GLOBAL_QUERIES, GLOBAL_DAYS, isSanghStory, parseDate } from "../lib/shared.js";
import { isAuthed } from "../lib/auth.js";

const CACHE_TTL = 5 * 60 * 1000;   // 5 min saved copy, avoids hammering Google News
globalThis.__SW_CACHE = globalThis.__SW_CACHE || {};

async function handler(req) {
  const NOW = Date.now();
  const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
  const json = (obj) => new Response(JSON.stringify(obj), {
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" }
  });

  // password check (also done by middleware / edge function; this guards direct function URLs)
  if (!(await isAuthed(req))) {
    return new Response(JSON.stringify({ error: "Login required", login: true }), {
      status: 401, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" }
    });
  }

  const url = new URL(req.url);
  const pk = url.searchParams.get("person");
  const fresh = url.searchParams.get("fresh") === "1";
  const cacheKey = pk || "all";
  const hit = globalThis.__SW_CACHE[cacheKey];
  if (hit && !fresh && url.searchParams.get("debug") !== "1" && NOW - hit.ts < CACHE_TTL) {
    return json({ ...hit.payload, cached: true });
  }

  const ageDays = (d) => { const t = parseDate(d, NOW); return t === null ? null : (NOW - t) / 86400000; };
  const isRecent = (d, maxDays) => { const t = parseDate(d, NOW); return t !== null && NOW - t <= (maxDays ? maxDays * 86400000 : MAX_AGE_MS) && t - NOW < 86400000; };

  // ---------- Google News RSS (free) ----------
  const decode = (s) => String(s || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'").replace(/&nbsp;/g, " ")
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n))
    .replace(/&amp;/g, "&");
  const tag = (block, name) => {
    const m = block.match(new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)</${name}>`, "i"));
    return m ? decode(m[1]).trim() : null;
  };

  const DEBUG = url.searchParams.get("debug") === "1";
  const debugLog = [];
  const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

  // Parse RSS <item> and Atom <entry> blocks into news items
  function parseFeed(xml, defaultSource, maxDays) {
    const out = [];
    const blocks = [...xml.matchAll(/<item[\s>]([\s\S]*?)<\/item>/g), ...xml.matchAll(/<entry[\s>]([\s\S]*?)<\/entry>/g)];
    for (const m of blocks) {
      const b = m[1];
      const source = tag(b, "source") || defaultSource || null;
      let title = (tag(b, "title") || "").replace(/<[^>]+>/g, "").trim();
      if (source && title.endsWith(` - ${source}`)) title = title.slice(0, -(source.length + 3));
      const pubDate = tag(b, "pubDate") || tag(b, "published") || tag(b, "updated") || tag(b, "dc:date");
      if (!title || !isRecent(pubDate, maxDays)) continue;
      let link = tag(b, "link");
      if (!link) { const lm = b.match(/<link[^>]*href="([^"]+)"/i); link = lm ? lm[1] : "#"; }
      const snippet = (tag(b, "description") || tag(b, "summary") || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 400);
      out.push({ title, link: link || "#", pubDate, source, ageDays: ageDays(pubDate), snippet: snippet || null });
    }
    return out;
  }

  async function getFeed(feedUrl, label, errors, timeout = 7000, maxDays) {
    const t0 = Date.now();
    try {
      const r = await fetch(feedUrl, {
        signal: AbortSignal.timeout(timeout),
        headers: { "User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*" },
        redirect: "follow",
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const xml = await r.text();
      if (!/<(rss|feed|rdf:RDF)[\s>]/i.test(xml)) throw new Error("not a feed");
      const items = parseFeed(xml, label.startsWith("site:") ? null : label, maxDays);
      if (DEBUG) debugLog.push({ src: label, items: items.length, ms: Date.now() - t0 });
      return items;
    } catch (e) {
      const msg = String((e && e.message) || e).slice(0, 80);
      errors.push(`${label.slice(0, 40)}: ${msg}`);
      if (DEBUG) debugLog.push({ src: label, items: 0, error: msg, ms: Date.now() - t0 });
      return [];
    }
  }

  function googleNewsUrl(q, hl) {
    const lang = hl === "hi" ? { hl: "hi", gl: "IN", ceid: "IN:hi" } : { hl: "en-IN", gl: "IN", ceid: "IN:en" };
    return `https://news.google.com/rss/search?${new URLSearchParams({ q, ...lang })}`;
  }

  // Run jobs a few at a time (Google slows down floods of requests); stop starting new ones after the time budget
  async function pool(jobs, limit, budgetMs) {
    const deadline = Date.now() + budgetMs;
    const collected = [];
    let i = 0;
    async function worker() {
      while (i < jobs.length && Date.now() < deadline) {
        const job = jobs[i++];
        collected.push(...(await job()));
      }
    }
    await Promise.race([
      Promise.all(Array.from({ length: limit }, worker)),
      new Promise((res) => setTimeout(res, budgetMs + 1000)),
    ]);
    return { items: collected.slice(), skipped: Math.max(0, jobs.length - i) };
  }

  // ---------- daily SerpAPI results (saved file, no quota used here) ----------
  let serp = null;
  try {
    const r = await fetch(new URL(`/data/serp.json?t=${Math.floor(NOW / 600000)}`, url), {
      signal: AbortSignal.timeout(4000),
      headers: { cookie: req.headers.get("cookie") || "" }, // pass the login through the password gate
    });
    if (r.ok) serp = await r.json();
  } catch { /* file missing or unreachable — RSS alone still works */ }

  // ---------- classification ----------
  const SPEECH_KW = ["speech","address","says","said","statement","warns","urges","calls","remarks","lecture","hails","flags","tells","stresses","emphasi","asserts","appeals","advocates","highlights","underscores","message","advice","pitches","reiterates","slams","bats for","भाषण","संबोधन","बोले","कहा","बयान","संदेश","आह्वान","अपील","बताया","मंत्र","साक्षात्कार"];
  const PROGRAM_KW = ["visit","tour","program","programme","schedule","scheduled","upcoming","inaugurat","attend","arrives","reaches","to address","to visit","कार्यक्रम","दौरा","पहुंचे","पहुंचेंगे","आगामी","आगमन","प्रवास","शिविर","समारोह"];
  const INTERVIEW_KW = ["interview","podcast","exclusive","in conversation","speaks to","talks to","साक्षात्कार","बातचीत","पॉडकास्ट"];
  const ORG_KW = ["rss","sangh","meeting","nagpur","pratinidhi","anniversary","vijayadashami","shakha","swayamsevak","karyakari","baithak","seva","शताब्दी","बैठक","संघ","स्वयंसेवक","विजयादशमी","शाखा","आरएसएस"];
  const has = (lo, list) => list.some((k) => lo.includes(k));

  function matchesPerson(it, p) {
    const lo = (it.title + " " + (it.snippet || "")).toLowerCase();
    if (has(lo, p.strong)) return true;
    return p.weak.length > 0 && has(lo, p.weak) && has(lo, p.weakNeeds);
  }
  function isSpeech(it, p) {
    const lo = it.title.toLowerCase();
    if (has(lo, SPEECH_KW)) return true;
    return has(lo, p.strong) && /[:“‘"]/.test(it.title);
  }

  // ---------- de-duplication ----------
  const STOP = new Set(["a","an","the","in","on","at","to","of","for","and","or","is","are","was","were","by","with","be","been","has","have","had","will","says","said","rss","news","india"]);
  const slug = (t) => t.toLowerCase().replace(/[^\w\sऀ-ॿ]/g, " ").split(/\s+/).filter((w) => w.length > 2 && !STOP.has(w));
  function sim(a, b) {
    const sa = new Set(slug(a)), sb = new Set(slug(b));
    if (!sa.size || !sb.size) return 0;
    let c = 0; for (const w of sa) if (sb.has(w)) c++;
    return c / Math.min(sa.size, sb.size);
  }
  function dedupe(items) {
    const groups = [];
    for (const item of items) {
      let hit = false;
      for (const g of groups) {
        if (sim(item.title, g.rep.title) >= 0.6) {
          g.sources.push({ name: item.source, link: item.link });
          if ((item.ageDays ?? 999) < (g.rep.ageDays ?? 999)) g.rep = item;
          hit = true; break;
        }
      }
      if (!hit) groups.push({ rep: item, sources: [{ name: item.source, link: item.link }] });
    }
    return groups.map((g) => ({
      ...g.rep,
      allSources: [...new Map(g.sources.filter((s) => s.name).map((s) => [s.name, s])).values()].slice(0, 5),
      sourceCount: g.sources.length,
    }));
  }
  const urlDedupe = (arr) => { const seen = new Set(); return arr.filter((i) => { const k = (i.link || "").split("?")[0]; return seen.has(k) ? false : (seen.add(k), true); }); };

  // ---------- run ----------
  const targets = pk && PEOPLE[pk] ? { [pk]: PEOPLE[pk] } : PEOPLE;
  const result = {};

  // The websites' own feeds are read once and shared by both people
  const feedErrors = [];
  const feedJobs = DIRECT_FEEDS.map(({ site, url: fu }) => () => getFeed(fu, site, feedErrors, 6000));
  const queryJobs = {};
  const queryErrors = {};
  for (const [key, p] of Object.entries(targets)) {
    queryErrors[key] = [];
    queryJobs[key] = p.rssQueries.map(({ q, hl, maxDays }) => () => getFeed(googleNewsUrl(q, hl), "site:" + q.slice(0, 60), queryErrors[key], 7000, maxDays));
  }
  // foreign press on the Sangh (only on a full refresh, not for a single person)
  const globalErrors = [];
  if (!pk) {
    queryErrors.global = globalErrors;
    queryJobs.global = GLOBAL_QUERIES.map(({ q, hl, maxDays }) => () => getFeed(googleNewsUrl(q, hl), "site:" + q.slice(0, 60), globalErrors, 7000, maxDays));
  }
  // one shared pool: feeds first, then both people's searches interleaved
  const tagged = [];
  feedJobs.forEach((j) => tagged.push({ who: "feeds", j }));
  const keys = Object.keys(queryJobs);
  const maxLen = Math.max(0, ...keys.map((k) => queryJobs[k].length));
  for (let n = 0; n < maxLen; n++) for (const k of keys) if (queryJobs[k][n]) tagged.push({ who: k, j: queryJobs[k][n] });
  const buckets = { feeds: [] };
  keys.forEach((k) => (buckets[k] = []));
  const run = await pool(tagged.map(({ who, j }) => async () => { const r = await j(); buckets[who].push(...r); return []; }), 12, 7500);

  // ---------- "विदेशी मीडिया में संघ" ----------
  let globalMedia = null;
  if (!pk) {
    const g = urlDedupe((buckets.global || []).filter((i) => isSanghStory(i.title + " " + (i.snippet || ""))))
      .sort((a, b) => (a.ageDays ?? 999) - (b.ageDays ?? 999));
    globalMedia = {
      days: GLOBAL_DAYS,
      items: dedupe(g).slice(0, 30),
      notice: globalErrors.length >= GLOBAL_QUERIES.length ? "Google News did not respond for foreign papers. Try again in a few minutes." : null,
      stats: { searches: GLOBAL_QUERIES.length, items: (buckets.global || []).length, failed: globalErrors.length, aboutSangh: g.length },
    };
  }

  for (const [key, p] of Object.entries(targets)) {
    const errors = queryErrors[key];
    const rss = [...buckets[key], ...buckets.feeds];
    const daily = ((serp && serp.people && serp.people[key]) || [])
      .filter((i) => i && i.title && isRecent(i.pubDate))
      .map((i) => ({ ...i, ageDays: ageDays(i.pubDate) }));
    const raw = [...rss, ...daily];
    const matched = raw.filter((i) => matchesPerson(i, p));
    const unique = urlDedupe(matched).sort((a, b) => (a.ageDays ?? 999) - (b.ageDays ?? 999));
    const pick = (fn, n) => dedupe(unique.filter(fn)).slice(0, n);
    const failed = errors.length;

    result[key] = {
      label: p.label, role: p.role,
      allNews:    dedupe(unique).slice(0, 40),
      speeches:   pick((i) => isSpeech(i, p), 15),
      programs:   pick((i) => has(i.title.toLowerCase(), PROGRAM_KW), 12),
      interviews: pick((i) => has(i.title.toLowerCase(), INTERVIEW_KW), 10),
      hindiNews:  pick((i) => /[ऀ-ॿ]/.test(i.title), 15),
      orgEvents:  pick((i) => has(i.title.toLowerCase(), ORG_KW), 12),
      notice: failed >= p.rssQueries.length
        ? `Google News did not respond (${[...new Set(errors.map((e) => e.split(": ").pop()))].slice(0, 2).join(", ")}). Try again in a few minutes.`
        : failed > 0 ? `${failed} of ${p.rssQueries.length} searches failed` : null,
      stats: {
        searches: p.rssQueries.length, searchItems: buckets[key].length, failedSearches: failed,
        siteFeedItems: buckets.feeds.length, dailyItems: daily.length,
        afterNameFilter: matched.length, uniqueItems: unique.length, skippedForTime: run.skipped,
      },
    };
  }

  const payload = { generatedAt: new Date().toISOString(), dailyUpdatedAt: (serp && serp.updatedAt) || null, people: result, globalMedia };
  if (DEBUG) payload.debug = { siteFeeds: debugLog.filter((d) => !d.src.startsWith("site:")), searches: debugLog.filter((d) => d.src.startsWith("site:")), feedErrors };
  const total = Object.values(result).reduce((n, r) => n + r.allNews.length, 0);
  if (total > 0 && !DEBUG) globalThis.__SW_CACHE[cacheKey] = { ts: NOW, payload };
  return json(payload);
}

// Vercel Node.js function (Web Request/Response signature)
export function GET(request) {
  return handler(request);
}
