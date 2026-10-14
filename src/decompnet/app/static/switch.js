"use strict";
const $ = id => document.getElementById(id);
let current = null, selected = null, detail = null, mode = "c", projectId = location.hash.slice(1), lastMap = "", loadingDetail = 0;
const active = p => ["queued", "analyzing", "drafting"].includes(p.state);
async function api(path, data) {
  const response = await fetch(`/api/switch/${path}`, data === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-Decomp": "1"}, body: JSON.stringify(data)
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}
function message(text, error = false) { $("message").textContent = text; $("message").classList.toggle("error", error); }
function button(label, className, click) { const b = document.createElement("button"); b.type = "button"; b.textContent = label; b.className = className; b.onclick = click; return b; }
function clearDetail() {
  selected = null; detail = null; loadingDetail++;
  $("function-name").textContent = "Select a function"; $("function-address").textContent = "";
  $("code").textContent = "Analyze a sample to see actual function results."; $("calls").replaceChildren();
}
async function listProjects() {
  const result = await api("projects");
  $("projects").replaceChildren();
  for (const p of result.projects) {
    const b = button(p.title, "project" + (p.slug === projectId ? " selected" : ""), () => chooseProject(p.slug));
    b.dataset.project = p.slug;
    const small = document.createElement("small"); small.textContent = `${p.format} · ${p.state} · ${p.drafted} drafts`; b.append(small); $("projects").append(b);
  }
  const t = result.tools;
  $("tools").textContent = `Ghidra ${t.ghidra_installed ? "installed" : "needed"} · Java ${t.java_version || "needed"} · Native decompiler ${t.native_decompiler_available ? "ready" : "needed"} · Switch loader ${t.switch_loader_installed ? "installed" : "needed for NSO"}`;
  if (!projectId && result.projects.length) await chooseProject(result.projects[0].slug);
}
async function chooseProject(id) {
  projectId = id; location.hash = id; clearDetail(); lastMap = ""; $("import-panel").open = false;
  await refresh(); await listProjects();
}
function renderFunctions() {
  const filter = $("search").value.toLowerCase();
  $("functions").replaceChildren();
  for (const f of current.functions.filter(f => `${f.name} ${f.address}`.toLowerCase().includes(filter))) {
    const b = button(f.name, "fn" + (f.address === selected ? " selected" : ""), () => select(f.address));
    const small = document.createElement("small"); small.textContent = `${f.address} · ${f.status}`; b.append(small); $("functions").append(b);
  }
}
async function select(address) {
  selected = address; detail = null; const serial = ++loadingDetail, id = projectId;
  renderFunctions();
  for (const b of $("map").children) b.classList.toggle("selected", b.dataset.address === address);
  $("code").textContent = "Loading function…";
  try {
    const result = await api(`function?id=${encodeURIComponent(id)}&address=${encodeURIComponent(address)}`);
    if (serial !== loadingDetail || id !== projectId) return;
    detail = result; renderDetail();
  } catch (e) { if (serial === loadingDetail) $("code").textContent = e.message; }
}
function renderDetail() {
  if (!detail) return;
  $("function-name").textContent = detail.name;
  $("function-address").textContent = `${detail.address} · relative ${detail.relative_address} · ${detail.size} bytes · ${detail.status}`;
  $("code").textContent = mode === "c" ? (detail.c || detail.error || "No draft available") : detail.assembly + (detail.assembly_truncated ? "\n… assembly display truncated" : "");
  $("show-c").setAttribute("aria-pressed", String(mode === "c")); $("show-asm").setAttribute("aria-pressed", String(mode === "assembly"));
  $("calls").replaceChildren();
  for (const call of detail.calls || []) {
    const inSample = current.functions.some(f => f.address === call.address);
    const b = button(`${call.name} · ${call.address}${inSample ? "" : " (outside sample)"}`, "secondary", () => select(call.address));
    b.disabled = !inSample; $("calls").append(b);
  }
  if (!(detail.calls || []).length) $("calls").textContent = "No direct function calls identified.";
}
async function refresh() {
  if (!projectId) return;
  const id = projectId, p = await api(`project?id=${encodeURIComponent(id)}`);
  if (id !== projectId) return;
  if (!current) $("import-panel").open = false;
  current = p; $("workspace").hidden = false;
  for (const b of $("projects").children) {
    if (b.dataset.project === p.slug) b.querySelector("small").textContent = `${p.format} · ${p.state} · ${p.drafted} drafts`;
  }
  $("project-title").textContent = p.title; $("identity").textContent = `${p.format} · ${p.summary.language || p.architecture} · SHA-256 ${p.sha256.slice(0, 16)}…`;
  $("state").textContent = p.state; $("discovered").textContent = p.summary.total_functions ?? "—";
  $("exported").textContent = p.exported; $("drafted").textContent = p.drafted;
  $("start").disabled = active(p); $("cancel").disabled = !active(p); $("limit").disabled = active(p);
  $("job-note").textContent = p.error || (active(p) ? "Discovering functions and exporting a bounded sample…" : `${p.discovery_timed_out ? "Discovery time limit reached · " : ""}${p.failed} sample failures · no compiler verification`);
  $("job-note").classList.toggle("error", Boolean(p.error));
  $("metadata").textContent = JSON.stringify({sha256: p.sha256, build_id: p.build_id, segments: p.segments, analysis_scope: p.analysis_scope, language: p.summary.language}, null, 2);
  const stamp = JSON.stringify(p.functions);
  if (stamp !== lastMap) {
    if (selected && !p.functions.some(f => f.address === selected)) clearDetail();
    lastMap = stamp; $("map").replaceChildren();
    for (const f of p.functions) {
      const b = button("", f.status + (f.address === selected ? " selected" : ""), () => select(f.address));
      b.dataset.address = f.address; b.title = `${f.name} · ${f.address} · ${f.status}`; b.setAttribute("aria-label", b.title); $("map").append(b);
    }
    if (!p.functions.length) $("map").textContent = active(p) ? "Waiting for Ghidra's first results…" : "No function sample yet.";
    renderFunctions();
    if (!selected && p.functions.length) await select(p.functions[0].address);
  }
}
$("import").onclick = async () => {
  $("import").disabled = true;
  try { const p = await api("import", {path: $("path").value, title: $("title").value, confirmed: $("owned").checked}); message("Executable imported. Start a sample to inspect its functions."); await chooseProject(p.slug); }
  catch (e) { message(e.message, true); } finally { $("import").disabled = false; }
};
$("start").onclick = async () => {
  $("start").disabled = true;
  try { await api("start", {id: projectId, limit: Number($("limit").value)}); message("Analysis started locally."); await refresh(); }
  catch (e) { message(e.message, true); $("start").disabled = false; }
};
$("cancel").onclick = async () => { try { await api("cancel", {id: projectId}); message("Cancellation requested; waiting for the worker to stop."); } catch (e) { message(e.message, true); } };
$("search").oninput = () => { if (current) renderFunctions(); };
$("show-c").onclick = () => { mode = "c"; renderDetail(); }; $("show-asm").onclick = () => { mode = "assembly"; renderDetail(); };
window.addEventListener("hashchange", () => { const id = location.hash.slice(1); if (id && id !== projectId) chooseProject(id).catch(e => message(e.message, true)); });
async function poll() { try { await refresh(); } catch (e) { message(e.message, true); } setTimeout(poll, 2000); }
listProjects().then(poll).catch(e => message(e.message, true));
