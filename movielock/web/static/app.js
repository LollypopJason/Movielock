"use strict";
const $ = (id) => document.getElementById(id);
let kind = "lockscript";
let library = null;

const STARTER = `MOVIELOCK reel 30fps map=studio captions=on
TITLE My first Movielock
CAST a = haze @ -1.5,0 > b
CAST b = infernus @ 1.5,0 > a
SHOT wide on=a 2s
  a ~ idle
SHOT closeup on=b
  b ? "Write a script with the idea box, or edit this one."
`;

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const data = await res.json().catch(() => ({error: `HTTP ${res.status}`}));
  if (!res.ok && !data.issues) throw new Error(data.error || `HTTP ${res.status}`);
  return {ok: res.ok, data};
}

function save() { try { localStorage.setItem("movielock.script", $("script").value);
  localStorage.setItem("movielock.kind", kind); } catch (e) { /* storage unavailable */ } }

function load() {
  let saved = null;
  try { saved = localStorage.getItem("movielock.script"); kind = localStorage.getItem("movielock.kind") || kind; }
  catch (e) { /* storage unavailable */ }
  $("script").value = saved || STARTER;
  setKindButtons();
}

function setKindButtons() {
  document.querySelectorAll("#kind-seg button").forEach((b) => b.classList.toggle("on", b.dataset.kind === kind));
}

function showIssues(issues, okText) {
  const ul = $("issues");
  ul.innerHTML = "";
  if (!issues.length) { ul.innerHTML = `<li class="ok">${okText || "No problems found."}</li>`; return; }
  for (const i of issues) {
    const li = document.createElement("li");
    li.className = i.severity;
    li.textContent = `${i.severity}: ${i.line ? "line " + i.line + ": " : (i.path ? i.path + ": " : "")}${i.message}`;
    if (i.line) li.onclick = () => gotoLine(i.line);
    ul.appendChild(li);
  }
}

function gotoLine(n) {
  const ta = $("script");
  const lines = ta.value.split("\n");
  const start = lines.slice(0, n - 1).join("\n").length + (n > 1 ? 1 : 0);
  ta.focus();
  ta.setSelectionRange(start, start + (lines[n - 1] || "").length);
  ta.scrollTop = Math.max(0, (n - 4) * 19);
}

async function validate() {
  $("validate-status").textContent = "checking...";
  try {
    const {data} = await api("/api/validate", {script: $("script").value, kind});
    showIssues(data.issues);
    const errs = data.issues.filter((i) => i.severity === "error").length;
    $("validate-status").textContent = errs ? `${errs} error(s)` : "valid";
    $("validate-status").className = "status " + (errs ? "error" : "ok");
    return !errs;
  } catch (e) { $("validate-status").textContent = e.message; return false; }
}

async function poll(jobId, onUpdate) {
  for (;;) {
    const {data} = await api(`/api/jobs/${jobId}`);
    onUpdate(data);
    if (data.status === "done" || data.status === "error") return data;
    await new Promise((r) => setTimeout(r, 700));
  }
}

async function write() {
  const idea = $("idea").value.trim();
  if (!idea) { $("write-status").textContent = "describe your idea first"; return; }
  $("write").disabled = true;
  $("write-status").textContent = "writing...";
  $("writer-notes").textContent = "";
  try {
    const {data} = await api("/api/write", {idea, format: $("format").value});
    const job = await poll(data.job, (j) => { $("write-status").textContent = j.log.at(-1) || j.message; });
    if (job.status === "error") throw new Error(job.error);
    if (kind !== "lockscript") { kind = "lockscript"; setKindButtons(); }
    $("script").value = job.result.script;
    save();
    $("writer-notes").textContent = job.result.notes || "";
    showIssues(job.result.issues, "The writer's script is valid.");
    $("write-status").textContent = job.result.valid ? "done" : "done, but the script still has errors";
  } catch (e) { $("write-status").textContent = "error: " + e.message; }
  finally { $("write").disabled = false; }
}

async function render() {
  $("render").disabled = true;
  $("render-status").textContent = "checking script...";
  $("log").textContent = "";
  $("bar").style.width = "0";
  try {
    const res = await api("/api/render", {script: $("script").value, kind,
      quality: $("quality").value, renderer: $("renderer").value});
    if (!res.ok) { showIssues(res.data.issues || []); throw new Error(res.data.error); }
    const job = await poll(res.data.job, (j) => {
      $("bar").style.width = `${Math.round(j.progress * 100)}%`;
      $("render-status").textContent = j.message;
      $("log").textContent = j.log.join("\n");
    });
    if (job.status === "error") throw new Error(job.error);
    const url = job.result.video;
    $("result").hidden = false;
    $("player").src = url + "?t=" + Date.now();
    $("download").href = url + "?download=1";
    $("render-status").textContent = `done: ${job.result.duration.toFixed(1)} s, ${job.result.renderer}`;
    const w = $("render-warnings");
    w.innerHTML = "";
    for (const msg of job.result.warnings) { const li = document.createElement("li"); li.textContent = msg; w.appendChild(li); }
  } catch (e) { $("render-status").textContent = "error: " + e.message; }
  finally { $("render").disabled = false; }
}

async function switchKind(to) {
  if (to === kind) return;
  try {
    const {data} = await api("/api/convert", {script: $("script").value, kind, to});
    $("script").value = data.script;
    kind = to;
    setKindButtons();
    save();
  } catch (e) { $("validate-status").textContent = "can't convert: " + e.message; }
}

function insertAtCursor(text) {
  const ta = $("script");
  const s = ta.selectionStart, e = ta.selectionEnd;
  ta.value = ta.value.slice(0, s) + text + ta.value.slice(e);
  ta.focus();
  ta.setSelectionRange(s + text.length, s + text.length);
  save();
}

function castNameFor(heroId) {
  const m = $("script").value.match(new RegExp(`^\\s*CAST\\s+(\\w[\\w-]*)\\s*=\\s*${heroId}\\b`, "m"));
  return m ? m[1] : heroId;
}

function renderLibrary() {
  if (!library) return;
  const q = $("search").value.toLowerCase();
  const box = $("library");
  box.innerHTML = "";
  for (const h of library.heroes) {
    const lines = library.voices.filter((v) => v.hero === h.id &&
      (!q || v.id.toLowerCase().includes(q) || v.transcript.toLowerCase().includes(q) || h.name.toLowerCase().includes(q)));
    if (q && !lines.length && !h.name.toLowerCase().includes(q)) continue;
    const head = document.createElement("div");
    head.className = "hero";
    head.innerHTML = `<span class="dot"></span>`;
    head.querySelector(".dot").style.background = h.color;
    head.append(`${h.name} (${h.id}) · ${lines.length} lines` + (h.model ? "" : " · no model"));
    box.appendChild(head);
    for (const v of lines.slice(0, 300)) {
      const row = document.createElement("div");
      row.className = "line" + (v.file ? "" : " missing");
      const code = document.createElement("code");
      code.textContent = v.id;
      row.append(code, v.transcript || "(no transcript)");
      row.onclick = () => insertAtCursor(`  ${castNameFor(h.id)} ! ${v.id}\n`);
      box.appendChild(row);
    }
  }
  const extra = document.createElement("p");
  extra.className = "small";
  extra.textContent = `maps: ${library.maps.map((m) => m.id).join(", ")} · sounds: ${library.sounds.join(", ")}` +
    ` · music: ${library.music.join(", ") || "none"} · TTS ${library.tts ? "on" : "off"}`;
  box.appendChild(extra);
}

document.addEventListener("DOMContentLoaded", async () => {
  load();
  $("write").onclick = write;
  $("validate").onclick = validate;
  $("render").onclick = render;
  $("script").addEventListener("input", save);
  $("search").addEventListener("input", renderLibrary);
  document.querySelectorAll("#kind-seg button").forEach((b) => b.onclick = () => switchKind(b.dataset.kind));
  try { library = (await api("/api/library")).data; renderLibrary(); }
  catch (e) { $("library").textContent = "could not load the library: " + e.message; }
});
