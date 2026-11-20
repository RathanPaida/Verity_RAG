"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const KINDS = ["architecture", "postmortem", "security", "repo", "runbook", "slo", "records"];
const EXAMPLES = [
  "What are the top reliability risks of the payments platform?",
  "Is the payments platform really able to survive a regional outage?",
  "What caused the ERR-4471 errors on PAY-API?",
  "Which service had the most SEV1 and SEV2 incidents in 2024?",
  "What was the result of the SOC 2 Type II audit?",
];
const state = { result: null, mode: "hybrid" };
const eng = () => $("#engagement").value.trim();

function store(k, v) { try { v === undefined ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} }
function load(k) { try { return localStorage.getItem(k); } catch { return null; } }

async function api(path, opts = {}) {
  const headers = { ...(opts.body instanceof FormData ? {} : { "Content-Type": "application/json" }), "X-API-Key": $("#apiKey").value };
  const res = await fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || res.statusText));
  return body;
}
const fmt = {
  ms: (v) => v == null ? "–" : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`,
  usd: (v) => v == null ? "–" : `$${v < 0.01 ? v.toFixed(4) : v.toFixed(3)}`,
  n: (v, d = 0) => v == null ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d }),
  date: (v) => v ? String(v).slice(0, 10) : "–",
  pct: (v) => v == null ? "–" : v.toFixed(3),
};
const tile = (l, v) => `<div class="tile"><div class="v">${v}</div><div class="l">${esc(l)}</div></div>`;
const spinner = (t) => `<div class="empty"><span class="spinner"></span> ${esc(t)}</div>`;
const errBox = (e) => `<div class="callout bad">${esc(e.message || e)}</div>`;

// ---------------------------------------------------------------- tiny markdown renderer (escaped first)
function inline(s) {
  return s.replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>")
    .replace(/\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]/g, (m, g) => g.match(/S\d+/g).map((lab) => `<span class="cite" data-label="${lab}">${lab}</span>`).join(" "));
}
function markdown(md) {
  const lines = esc(md).split("\n");
  let html = "", i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*\|/.test(line)) {
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r));
      html += "<table><thead><tr>" + cells(body[0]).map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>" +
        body.slice(1).map((r) => "<tr>" + cells(r).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table>";
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) { html += `<h${h[1].length + 2}>${inline(h[2])}</h${h[1].length + 2}>`; i++; continue; }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
      html += `<${ordered ? "ol" : "ul"}>` + items.map((t) => `<li>${inline(t)}</li>`).join("") + `</${ordered ? "ol" : "ul"}>`;
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^\s*(\||#{1,4}\s|[-*]\s|\d+\.\s)/.test(lines[i])) para.push(lines[i++]);
    html += `<p>${inline(para.join(" "))}</p>`;
  }
  return html;
}

// ---------------------------------------------------------------- navigation
function show(view) {
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  store("verity.view", view);
  ({ sources: loadSources, memory: loadMemories, runs: loadRuns, eval: loadEvalHistory }[view] || (() => {}))();
}

// ---------------------------------------------------------------- ask
async function ask(e) {
  e.preventDefault();
  const question = $("#question").value.trim();
  if (question.length < 5) return;
  const kinds = $$("#kindFilters input:checked").map((c) => c.value);
  const filters = {};
  if (kinds.length) filters.kinds = kinds;
  if ($("#dateFrom").value) filters.date_from = $("#dateFrom").value + "T00:00:00Z";
  if ($("#dateTo").value) filters.date_to = $("#dateTo").value + "T23:59:59Z";
  $("#askBtn").disabled = true;
  $("#result").innerHTML = spinner("Supervisor planning → researchers searching → analyst → verifier → writer…");
  try {
    const r = await api("/ask", { method: "POST", body: JSON.stringify({ engagement_id: eng(), question, filters: Object.keys(filters).length ? filters : null, use_cache: $("#useCache").checked }) });
    renderResult(r);
  } catch (err) { $("#result").innerHTML = errBox(err); }
  finally { $("#askBtn").disabled = false; }
}

function renderResult(r) {
  state.result = r;
  const m = r.metrics || {};
  const cache = r.cache?.hit ? `<span class="badge info" title="${esc(r.cache.cached_question)}">cache hit · sim ${r.cache.similarity}</span>` : "";
  const conflicts = (r.conflicts || []).length ? `<div class="callout bad"><b>Sources disagree</b><ul>${r.conflicts.map((c) =>
    `<li>${inline(esc(c.description))} ${[...c.side_a, ...c.side_b].map((l) => `<span class="cite" data-label="${l}">${l}</span>`).join(" ")}</li>`).join("")}</ul></div>` : "";
  const discovery = (r.discovery || []).length ? `<div class="callout"><b>Discovery Pack</b> - questions for the next client session<ol>${r.discovery.map((q) =>
    `<li><b>${esc(q.question)}</b><br><span class="small">${esc(q.why_it_matters)} · ask: ${esc(q.ask_whom)}</span></li>`).join("")}</ol></div>` : "";
  const memories = (r.memories_used || []).length ? `<div class="callout info"><b>Engagement memory applied</b><ul>${r.memories_used.map((mm) =>
    `<li><span class="badge info">${esc(mm.type)}</span> ${esc(mm.content)}</li>`).join("")}</ul></div>` : "";

  $("#result").innerHTML = `
    <div class="row wrap"><span class="badge ${esc(r.status)}">${esc((r.status || "").replace(/_/g, " "))}</span>${cache}
      <span class="muted small">run <code>${esc(r.run_id)}</code> · retrieval ${esc(JSON.stringify(r.retrieval_backends))}</span></div>
    <div class="tiles">${tile("latency", fmt.ms(m.latency_ms))}${tile("LLM calls", fmt.n(m.llm_calls))}${tile("tokens in / out", `${fmt.n(m.tokens_in)} / ${fmt.n(m.tokens_out)}`)}
      ${tile("output tok/s", fmt.n(m.output_tokens_per_sec, 1))}${tile("cost", fmt.usd(m.cost_usd))}${tile("tool calls", fmt.n(m.tool_calls))}${tile("evidence items", fmt.n(r.evidence_count))}</div>
    <div class="cols">
      <div>
        <div class="card brief">${markdown(r.answer || "")}</div>
        ${conflicts}${discovery}${memories}
        <h3>Citations</h3>
        ${(r.citations || []).length ? r.citations.map((c) => `<div class="hit"><div class="meta"><span class="cite" data-label="${c.label}">${c.label}</span>
          <b>${esc(c.title)}</b><span>${esc(c.source_id)}</span><span>${esc((c.section_path || []).join(" › "))}</span><span>${fmt.date(c.doc_date)}</span></div>
          <pre>${esc((c.passage || "").slice(0, 320))}${(c.passage || "").length > 320 ? "…" : ""}</pre></div>`).join("") : `<div class="muted small">No citations - nothing was asserted.</div>`}
      </div>
      <div>${renderTrace(r)}</div>
    </div>`;
}

function renderTrace(r) {
  const spans = r.spans || [];
  const total = Math.max(1, ...spans.map((s) => s.ms));
  const top = spans.filter((s) => !s.parent || !s.name.startsWith("tool:"));
  const wf = spans.map((s) => `<span title="${esc(s.parent ? "child of " + s.parent : "")}">${s.name.startsWith("tool:") ? "↳ " : ""}${esc(s.name)}</span>
      <div class="wf-bar"><span class="${s.name.startsWith("tool:") ? "tool" : ""}" style="width:${(100 * s.ms / total).toFixed(1)}%"></span></div>
      <span class="num">${fmt.ms(s.ms)} · ${fmt.n(s.tokens_in + s.tokens_out)} tok · ${fmt.usd(s.cost_usd)}</span>`).join("");
  const plan = (r.plan || []).map((p) => `<li><b>${esc(p.id)}</b> <span class="badge info">${esc(p.specialist)}</span> ${esc(p.question)}${p.use_records ? ' <span class="badge ok">records</span>' : ""}</li>`).join("");
  const steps = (r.tool_log || []).map((t) => `<li><b>${esc(t.subquestion)}·${t.step}</b> <code>${esc(t.action)}</code> - ${esc(t.thought)}<br><span class="muted">${esc(t.observation.slice(0, 220))}</span></li>`).join("");
  const v = r.verification || {};
  const verif = v.route ? `<div class="callout ${v.unsupported?.length ? "" : "info"}"><b>Verifier (self-reflection)</b> → route <code>${esc(v.route)}</code>, attempt ${v.attempt}
      ${(v.results || []).map((x) => `<div class="small">${x.supported ? "✅" : "❌"} ${esc(x.claim_id)} - ${esc(x.reason)}</div>`).join("")}
      ${(v.unsupported || []).length ? `<div class="small"><b>Dropped:</b> ${v.unsupported.map(esc).join(" · ")}</div>` : ""}</div>` : "";
  return `<div class="card"><b>Supervisor plan</b> (hierarchical delegation)<ol class="steps">${plan || "<li>-</li>"}</ol></div>
    <div class="card"><b>Researcher ReAct steps</b><ol class="steps">${steps || "<li>-</li>"}</ol></div>
    ${verif}
    <div class="card"><b>Trace</b> <span class="muted small">(${top.length} nodes, ${spans.length - top.length} tool spans)</span><div class="waterfall">${wf}</div></div>`;
}

// ---------------------------------------------------------------- citation drawer
async function openCitation(label) {
  const c = (state.result?.citations || []).find((x) => x.label === label);
  $("#drawer").classList.add("open");
  $("#drawerTitle").textContent = `Citation ${label}`;
  if (!c) { $("#drawerBody").innerHTML = `<div class="muted">This label is not in the citation list.</div>`; return; }
  let html = `<p><b>${esc(c.title)}</b><br><span class="small muted">${esc(c.source_id)} · ${esc((c.section_path || []).join(" › "))} · ${fmt.date(c.doc_date)}</span></p>
    <h3>Exact passage used</h3><pre class="source-text">${esc(c.passage)}</pre>`;
  if (c.supporting_chunk_ids?.length) {
    html += `<h3>Underlying records</h3><div class="chips">${c.supporting_chunk_ids.slice(0, 20).map((id) => `<span class="chip" data-chunk="${esc(id)}">${esc(id.split("#")[1] ? "row " + (+id.split("#")[1] + 1) : id)}</span>`).join("")}</div><div id="chunkView"></div>`;
  }
  $("#drawerBody").innerHTML = html + (c.chunk_id ? `<h3>In context</h3><div id="ctx">${spinner("loading source")}</div>` : "");
  if (c.chunk_id) {
    try {
      const [chunk, src] = await Promise.all([api(`/chunks/${encodeURIComponent(c.chunk_id)}`), api(`/sources/${encodeURIComponent(c.source_id)}`)]);
      if (src.raw_text && chunk.fields == null) {
        const t = src.raw_text, a = chunk.char_start, b = chunk.char_end;
        $("#ctx").innerHTML = `<div class="small muted">chars ${a}-${b} of ${t.length} · source v${src.version}</div><pre class="source-text">${esc(t.slice(Math.max(0, a - 600), a))}<mark id="hl">${esc(t.slice(a, b))}</mark>${esc(t.slice(b, b + 600))}</pre>`;
        $("#hl")?.scrollIntoView({ block: "center" });
      } else if (chunk.fields) {
        $("#ctx").innerHTML = fieldsTable(chunk.fields);
      }
    } catch (e) { $("#ctx").innerHTML = errBox(e); }
  }
}
const fieldsTable = (f) => `<table class="grid">${Object.entries(f).map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v) ? v.slice(0, 10) : v)}</td></tr>`).join("")}</table>`;

async function openChunk(id) {
  const c = await api(`/chunks/${encodeURIComponent(id)}`);
  $("#chunkView").innerHTML = c.fields ? fieldsTable(c.fields) : `<pre class="source-text">${esc(c.text)}</pre>`;
}

// ---------------------------------------------------------------- sources
async function loadSources() {
  $("#sourcesTable").innerHTML = spinner("loading sources");
  try {
    const list = await api(`/sources?engagement_id=${encodeURIComponent(eng())}&status=all`);
    if (!list.length) { $("#sourcesTable").innerHTML = `<div class="empty">No sources for this engagement yet - import some files.</div>`; return; }
    $("#sourcesTable").innerHTML = `<table class="grid"><thead><tr><th>Title</th><th>Kind</th><th>Owner</th><th>Doc date</th><th>Version</th><th class="num">Chunks</th><th>Status</th><th>Privacy</th></tr></thead><tbody>${list.map((s) =>
      `<tr class="click" data-source="${esc(s._id)}"><td><b>${esc(s.title)}</b><br><span class="small muted">${esc(s._id)}</span></td><td>${esc(s.kind)}</td><td>${esc(s.owner)}</td><td>${fmt.date(s.doc_date)}</td><td>v${s.version}</td>
       <td class="num">${s.chunk_count}</td><td><span class="badge ${s.status === "active" ? "ok" : "warn"}">${esc(s.status)}</span></td><td class="small">${(s.privacy_findings || []).join(", ") || "-"}</td></tr>`).join("")}</tbody></table>`;
  } catch (e) { $("#sourcesTable").innerHTML = errBox(e); }
}

async function openSource(id) {
  $("#drawer").classList.add("open");
  $("#drawerTitle").textContent = "Source";
  $("#drawerBody").innerHTML = spinner("loading");
  try {
    const s = await api(`/sources/${encodeURIComponent(id)}`);
    $("#drawerBody").innerHTML = `<p><b>${esc(s.title)}</b><br><span class="small muted">${esc(s._id)} · ${esc(s.kind)} · ${esc(s.owner)} · ${fmt.date(s.doc_date)} · ${esc(s.uri)}</span></p>
      <p class="small muted">hash ${esc(s.content_hash)} · ${s.chunk_count} chunks${s.record_count ? ` · ${s.record_count} records` : ""}</p>
      ${s.raw_text ? `<pre class="source-text">${esc(s.raw_text)}</pre>` : `<div class="muted">Structured source - rows are stored as record chunks with typed fields.</div>`}`;
  } catch (e) { $("#drawerBody").innerHTML = errBox(e); }
}

async function upload(e) {
  e.preventDefault();
  const files = $("#files").files;
  if (!files.length) return;
  const fd = new FormData();
  fd.append("engagement_id", eng());
  [...files].forEach((f) => fd.append("files", f));
  $("#uploadResult").innerHTML = spinner("chunking, redacting, embedding, indexing…");
  try {
    const r = await api("/ingest", { method: "POST", body: fd });
    $("#uploadResult").innerHTML = `<div class="callout info">${r.results.map((x) => `<div><code>${esc(x.source_id)}</code> - ${esc(x.status)}, ${x.chunks} chunks${x.privacy_findings?.length ? ` · redacted: ${x.privacy_findings.join(", ")}` : ""}</div>`).join("")}</div>`;
    $("#files").value = "";
    loadSources(); loadEngagements();
  } catch (err) { $("#uploadResult").innerHTML = errBox(err); }
}

// ---------------------------------------------------------------- search
async function search(e) {
  e.preventDefault();
  const q = $("#searchQ").value.trim();
  if (!q) return;
  $("#searchResults").innerHTML = spinner("searching");
  try {
    const hits = await api("/search", { method: "POST", body: JSON.stringify({ engagement_id: eng(), query: q, mode: state.mode, k: 8 }) });
    $("#searchResults").innerHTML = hits.length ? hits.map((h, i) => `<div class="hit"><div class="meta"><b>#${i + 1}</b><b>${esc(h.title)}</b><span>${esc((h.section_path || []).join(" › "))}</span>
      <span>${esc(h.kind)} · ${fmt.date(h.doc_date)}</span><span>RRF ${h.score}</span><span>vector rank ${h.vector_rank ?? "-"}${h.vector_score != null ? ` (${h.vector_score})` : ""}</span>
      <span>keyword rank ${h.text_rank ?? "-"}${h.text_score != null ? ` (${h.text_score})` : ""}</span></div><pre>${esc(h.text.slice(0, 500))}</pre>
      <div class="small muted">${esc(h.chunk_id)}</div></div>`).join("") : `<div class="empty">No results.</div>`;
  } catch (err) { $("#searchResults").innerHTML = errBox(err); }
}

// ---------------------------------------------------------------- memory
async function loadMemories() {
  try {
    const list = await api(`/memories?engagement_id=${encodeURIComponent(eng())}&status=all`);
    $("#memList").innerHTML = list.length ? `<table class="grid"><thead><tr><th>Type</th><th>Content</th><th>Status</th><th>Created</th><th>Expires</th><th></th></tr></thead><tbody>${list.map((m) =>
      `<tr><td><span class="badge info">${esc(m.type)}</span></td><td>${esc(m.content)}</td><td>${esc(m.status)}</td><td>${fmt.date(m.created_at)}</td><td>${fmt.date(m.expires_at)}</td>
       <td>${m.status === "active" ? `<button data-forget="${m._id}">forget</button>` : ""}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No memories yet.</div>`;
  } catch (e) { $("#memList").innerHTML = errBox(e); }
}
async function remember(e) {
  e.preventDefault();
  const content = $("#memContent").value.trim();
  if (content.length < 3) return;
  const ttl = parseFloat($("#memTtl").value);
  try {
    await api("/feedback", { method: "POST", body: JSON.stringify({ engagement_id: eng(), type: $("#memType").value, content, ttl_days: ttl > 0 ? ttl : null }) });
    $("#memContent").value = "";
    loadMemories();
  } catch (err) { alert(err.message); }
}

// ---------------------------------------------------------------- runs & metrics
async function loadRuns() {
  try {
    const [m, runs] = await Promise.all([api(`/metrics?engagement_id=${encodeURIComponent(eng())}`), api(`/runs?engagement_id=${encodeURIComponent(eng())}`)]);
    if (!m.runs) { $("#metricTiles").innerHTML = `<div class="empty">No runs yet - ask a question first.</div>`; $("#nodeTable").innerHTML = $("#runList").innerHTML = ""; return; }
    $("#metricTiles").innerHTML = tile("runs (7d)", m.runs) + tile("p50 latency", fmt.ms(m.latency_ms.p50)) + tile("p95 latency", fmt.ms(m.latency_ms.p95)) +
      tile("cost / request", fmt.usd(m.cost_per_request_usd)) + tile("total cost", fmt.usd(m.total_cost_usd)) + tile("avg output tok/s", fmt.n(m.avg_output_tokens_per_sec, 1)) +
      tile("avg tokens / request", fmt.n(m.avg_tokens_per_request)) + tile("answered / abstained", `${m.status.answered || 0} / ${(m.status.insufficient_evidence || 0) + (m.status.out_of_scope || 0)}`);
    $("#nodeTable").innerHTML = `<table class="grid"><thead><tr><th>Node / tool</th><th class="num">Calls</th><th class="num">Avg latency</th><th class="num">Avg tokens in</th><th class="num">Avg tokens out</th><th class="num">Total cost</th></tr></thead><tbody>${m.nodes.map((n) =>
      `<tr><td>${esc(n._id)}</td><td class="num">${n.calls}</td><td class="num">${fmt.ms(n.avg_ms)}</td><td class="num">${fmt.n(n.avg_tokens_in)}</td><td class="num">${fmt.n(n.avg_tokens_out)}</td><td class="num">${fmt.usd(n.total_cost_usd)}</td></tr>`).join("")}</tbody></table>`;
    $("#runList").innerHTML = `<table class="grid"><thead><tr><th>When</th><th>Question</th><th>Status</th><th class="num">Latency</th><th class="num">Cost</th></tr></thead><tbody>${runs.map((r) =>
      `<tr class="click" data-run="${esc(r._id)}"><td>${new Date(r.created_at).toLocaleString()}</td><td>${esc(r.question)}</td><td><span class="badge ${esc(r.status)}">${esc(r.status)}</span></td><td class="num">${fmt.ms(r.totals.latency_ms)}</td><td class="num">${fmt.usd(r.totals.cost_usd)}</td></tr>`).join("")}</tbody></table>`;
  } catch (e) { $("#metricTiles").innerHTML = errBox(e); }
}
async function openRun(id) {
  const r = await api(`/runs/${encodeURIComponent(id)}`);
  renderResult({ ...r, run_id: r._id, metrics: r.totals, evidence_count: (r.citations || []).length, memories_used: [] });
  $("#question").value = r.question;
  show("ask");
}

// ---------------------------------------------------------------- evaluation
async function runEval(e) {
  e.preventDefault();
  const modes = $$(".evmode:checked").map((c) => c.value);
  if (!modes.length) return;
  $("#evalBtn").disabled = true;
  $("#evalResult").innerHTML = spinner($("#evalAgent").checked ? "running retrieval + full agent over the golden set…" : "running retrieval over the golden set…");
  try {
    const r = await api("/eval", { method: "POST", body: JSON.stringify({ engagement_id: eng(), modes, k: +$("#evalK").value, agent: $("#evalAgent").checked, judge: $("#evalJudge").checked }) });
    const best = Math.max(...r.retrieval.map((x) => x.recall ?? 0));
    let html = `<table class="grid"><thead><tr><th>Mode</th><th class="num">recall@${r.k}</th><th class="num">hit@${r.k}</th><th class="num">MRR</th><th class="num">nDCG@${r.k}</th><th class="num">p50</th><th class="num">p95</th></tr></thead><tbody>${r.retrieval.map((x) =>
      `<tr><td>${esc(x.mode)}${x.recall === best ? ' <span class="badge ok">best</span>' : ""}</td><td class="num">${fmt.pct(x.recall)}</td><td class="num">${fmt.pct(x.hit)}</td><td class="num">${fmt.pct(x.mrr)}</td><td class="num">${fmt.pct(x.ndcg)}</td><td class="num">${fmt.ms(x.p50_ms)}</td><td class="num">${fmt.ms(x.p95_ms)}</td></tr>`).join("")}</tbody></table>
      <details><summary class="small muted">per-question retrieval (${esc(r.retrieval.at(-1).mode)})</summary><table class="grid">${r.retrieval.at(-1).per_question.map((q) =>
      `<tr><td>${q.id}</td><td class="num">${fmt.pct(q.recall)}</td><td class="num">${fmt.pct(q.mrr)}</td><td class="small">${q.retrieved.map(esc).join(", ")}</td></tr>`).join("")}</table></details>`;
    if (r.agent) {
      const a = r.agent;
      html += `<h3>Agent</h3><div class="tiles">${tile("abstention accuracy", fmt.pct(a.abstention_accuracy))}${tile("citation precision", fmt.pct(a.citation_precision))}${tile("faithfulness (judge)", fmt.pct(a.faithfulness))}
        ${tile("p50 latency", fmt.ms(a.p50_latency_ms))}${tile("p95 latency", fmt.ms(a.p95_latency_ms))}${tile("mean cost", fmt.usd(a.mean_cost_usd))}</div>
        <table class="grid"><thead><tr><th>Q</th><th>Status</th><th>Abstention OK</th><th class="num">Citation precision</th><th class="num">Faithfulness</th><th class="num">Latency</th></tr></thead><tbody>${a.per_question.map((q) =>
        `<tr class="click" data-run="${esc(q.run_id)}"><td>${q.id}</td><td><span class="badge ${esc(q.status)}">${esc(q.status)}</span></td><td>${q.abstention_correct ? "✅" : "❌"}</td><td class="num">${fmt.pct(q.citation_precision)}</td><td class="num">${fmt.pct(q.faithfulness)}</td><td class="num">${fmt.ms(q.latency_ms)}</td></tr>`).join("")}</tbody></table>`;
    }
    $("#evalResult").innerHTML = html;
    loadEvalHistory();
  } catch (err) { $("#evalResult").innerHTML = errBox(err); }
  finally { $("#evalBtn").disabled = false; }
}
async function loadEvalHistory() {
  try {
    const h = await api(`/eval/history?engagement_id=${encodeURIComponent(eng())}`);
    $("#evalHistory").innerHTML = h.length ? `<table class="grid"><thead><tr><th>When</th><th class="num">hybrid recall</th><th class="num">vector recall</th><th class="num">keyword recall</th><th class="num">abstention</th><th class="num">faithfulness</th><th>Config</th></tr></thead><tbody>${h.map((x) =>
      `<tr><td>${new Date(x.created_at).toLocaleString()}</td><td class="num">${fmt.pct(x.summary["hybrid.recall"])}</td><td class="num">${fmt.pct(x.summary["vector.recall"])}</td><td class="num">${fmt.pct(x.summary["text.recall"])}</td>
       <td class="num">${fmt.pct(x.summary["agent.abstention_accuracy"])}</td><td class="num">${fmt.pct(x.summary["agent.faithfulness"])}</td><td class="small">${esc(JSON.stringify(x.config))}</td></tr>`).join("")}</tbody></table>` : `<div class="muted small">No evaluation runs yet.</div>`;
  } catch (e) { $("#evalHistory").innerHTML = errBox(e); }
}

// ---------------------------------------------------------------- boot
async function loadEngagements() {
  try { $("#engList").innerHTML = (await api("/engagements")).map((e) => `<option value="${esc(e)}">`).join(""); } catch {}
}

function wire() {
  $$("#nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
  $("#examples").innerHTML = EXAMPLES.map((q) => `<span class="chip">${esc(q)}</span>`).join("");
  $("#examples").addEventListener("click", (e) => { if (e.target.classList.contains("chip")) { $("#question").value = e.target.textContent; $("#question").focus(); } });
  $("#kindFilters").innerHTML = KINDS.map((k) => `<label class="inline"><input type="checkbox" value="${k}"> ${k}</label>`).join("");
  $("#askForm").addEventListener("submit", ask);
  $("#question").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) ask(e); });
  $("#uploadForm").addEventListener("submit", upload);
  $("#searchForm").addEventListener("submit", search);
  $("#modeSeg").addEventListener("click", (e) => {
    const m = e.target.dataset.mode; if (!m) return;
    state.mode = m; $$("#modeSeg button").forEach((b) => b.classList.toggle("active", b.dataset.mode === m));
  });
  $("#memForm").addEventListener("submit", remember);
  $("#evalForm").addEventListener("submit", runEval);
  $("#drawerClose").addEventListener("click", () => $("#drawer").classList.remove("open"));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").classList.remove("open"); });
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-label],[data-source],[data-run],[data-chunk],[data-forget]");
    if (!t) return;
    try {
      if (t.dataset.label) openCitation(t.dataset.label);
      else if (t.dataset.source) openSource(t.dataset.source);
      else if (t.dataset.run) await openRun(t.dataset.run);
      else if (t.dataset.chunk) await openChunk(t.dataset.chunk);
      else if (t.dataset.forget) { await api(`/memories/${t.dataset.forget}`, { method: "DELETE" }); loadMemories(); }
    } catch (err) { alert(err.message); }
  });
  $("#engagement").addEventListener("change", () => { store("verity.engagement", eng()); show(load("verity.view") || "ask"); });
  $("#apiKey").addEventListener("change", () => store("verity.key", $("#apiKey").value || undefined));
}

async function boot() {
  wire();
  $("#engagement").value = load("verity.engagement") || $("#engagement").value;
  $("#apiKey").value = load("verity.key") || "";
  try {
    const h = await api("/health");
    $("#health").textContent = `${h.llm} · ${h.embeddings} embeddings · ${h.mongo}`;
    $("#keyWrap").classList.toggle("hidden", !h.auth);
    if (!load("verity.engagement") && h.demo_engagement) $("#engagement").value = h.demo_engagement;
  } catch { $("#health").textContent = "backend offline"; }
  loadEngagements();
  show(load("verity.view") || "ask");
}
boot();
