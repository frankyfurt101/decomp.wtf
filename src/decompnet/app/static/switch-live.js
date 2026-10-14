"use strict";
/* Switch analysis adapts the original application views and canvas. No simulated work. */
const AS = {polling:false, generation:0, catalog:[], catalogStamp:0, catalogTotal:0, seen:0, job:0, original:[], detailSerial:0, aiSeen:{}, aiJob:{}};
const analysisActive = p => ["queued","analyzing","drafting"].includes(p.state);
async function mountAgentsAntigravity(panel) {
  const box=document.createElement('div');
  panel.replaceChildren(box);
  box.innerHTML='<header class="panel-head"><h2>Start Antigravity</h2></header><p class="pad muted small">Loading your Switch projects…</p>';
  try {
    const [cfg,{projects}]=await Promise.all([api('/api/switch/agent/config'),api('/api/switch/projects')]);
    if(!box.isConnected)return;
    if(!cfg.installed || !projects.length) {
      box.innerHTML=`<header class="panel-head"><h2>Start Antigravity</h2></header><p class="pad muted small">${!cfg.installed?'Install Antigravity CLI and sign in to start a worker.':'Import a Switch executable and decompile some functions first. <a class="link" href="#/analysis">Open Switch analysis</a>'}</p>`;
      return;
    }
    box.innerHTML=`<header class="panel-head"><h2>Start Antigravity</h2><span class="ok-mark">installed</span></header>
      <form class="analysis-agent-controls">
        <label>Switch project<select id="agStartProject">${projects.map(p=>`<option value="${esc(p.slug)}">${esc(p.title)}</option>`).join('')}</select></label>
        <label>Worker<select id="agStartWorker">${[1,2,3,4,5].map(n=>`<option value="${n}">Worker ${n}</option>`).join('')}</select></label><label>Model<select id="agStartModel"><option value="">Antigravity default</option>${cfg.models.map(m=>`<option value="${esc(m.id)}">${esc(m.name)}</option>`).join('')}</select></label>
        <label>Functions this run<input id="agStartLimit" type="number" min="1" max="100" step="1" value="100" required></label>
        <button class="primary" id="agStartButton" type="submit" disabled>Checking project…</button>
      </form>
      <p class="analysis-agent-note muted small">Reconstruct decompiled functions using your signed-in Antigravity account. Opens the live function map; you can stop AI there. Candidates remain unverified.</p>
      <p class="analysis-agent-note small" id="agStartStatus" role="status" aria-live="polite"></p>`;
    const form=box.querySelector('form'),project=box.querySelector('#agStartProject'),worker=box.querySelector('#agStartWorker'),model=box.querySelector('#agStartModel'),limit=box.querySelector('#agStartLimit'),button=box.querySelector('#agStartButton'),status=box.querySelector('#agStartStatus');
    const preferred=cfg.models.find(m=>m.id===cfg.default_model)||cfg.models.find(m=>m.id.startsWith('gemini-'));
    model.value=preferred?.id||'';
    let serial=0,running=false,busy=false;
    const checkProject=async()=>{
      const request=++serial; button.disabled=true; button.textContent='Checking project…'; status.textContent='';
      try {
        const p=await api(`/api/switch/project?id=${encodeURIComponent(project.value)}`);
        if(!box.isConnected || request!==serial)return;
        running=aiWorkers(p.ai||{}).some(w=>w.worker===Number(worker.value) && aiWorkerActive(w));
        const drafted=projects.find(p=>p.slug===project.value)?.drafted||0;
        button.textContent=running?'View Antigravity work':'Start Antigravity';
        button.disabled=(!running && !drafted) || !cfg.max_workers;
        model.disabled=limit.disabled=running;
        status.textContent=!cfg.max_workers?'The app needs to restart to enable multiple workers.':running?'This worker is already working. Other slots can run alongside it.':drafted?'Ready. Start this worker, then add more in the live project view.':'Decompile functions in the project before starting Antigravity.';
      } catch(e) {if(box.isConnected && request===serial){button.textContent='Start Antigravity';status.textContent=e.message;}}
    };
    project.onchange=worker.onchange=checkProject;
    form.onsubmit=async event=>{
      event.preventDefault();
      if(busy || button.disabled || !form.reportValidity())return;
      const id=project.value;
      if(running){location.hash=`#/analysis/${id}`;return;}
      busy=true; button.disabled=true; project.disabled=worker.disabled=model.disabled=limit.disabled=true;
      button.textContent='Starting Antigravity…'; status.textContent='Launching the Antigravity worker…';
      try {
        await api('/api/switch/agent/start',{method:'POST',body:{id,worker:Number(worker.value),limit:Number(limit.value),model:model.value}});
        location.hash=`#/analysis/${id}`;
      } catch(e) {
        status.textContent=e.message;button.textContent='Start Antigravity';button.disabled=false;
        project.disabled=worker.disabled=model.disabled=limit.disabled=false;
      } finally {busy=false;}
    };
    await checkProject();
  } catch(e) {if(box.isConnected)box.innerHTML=`<header class="panel-head"><h2>Start Antigravity</h2></header><p class="pad small">${esc(e.message)} — use Refresh to retry.</p>`;}
}
const analysisStatus = status => ({drafted:'decompiled',working:'decompiling',reconstructing:'AI reconstructing',candidate:'AI candidate',open:'to do'}[status]||status);
function analysisNav(p) {
  return `<a class="nav-proj" href="#/analysis/${esc(p.slug)}" data-slug="${esc(p.slug)}"><span class="np-top"><span class="np-name">${esc(p.title)}</span>${analysisActive(p)?'<span class="dot work" title="Ghidra is running"></span>':''}</span><span class="np-meta">Switch <span class="grow"></span>${p.drafted.toLocaleString()} decompiled</span></a>`;
}
function analysisCard(p) {
  return `<a class="card" href="#/analysis/${esc(p.slug)}"><div class="cc-top"><span class="plat">Nintendo Switch</span><span class="meta">${esc(p.state)}</span></div><div><h4>${esc(p.title)}</h4><p class="meta">${p.drafted.toLocaleString()} decompiled · 0 verified matches</p></div></a>`;
}
function restoreAnalysisUI() {
  if (!document.body.classList.contains("analysis-project")) return;
  AS.generation++; AS.detailSerial++;
  document.body.classList.remove("analysis-project");
  for (const [el,html] of AS.original) el.innerHTML=html;
  AS.original=[];
  document.querySelectorAll('#ptabs [data-ptab]').forEach(el=>el.hidden=false);
  $("analysisActions")?.remove(); $("analysisLive")?.remove(); $("analysisAgent")?.remove();
  $("analysisSourceTabs")?.remove(); $("analysisCandidateNotes")?.remove();
  $("fnEdit").readOnly=false;
}
function rememberAnalysisText(selector,html) {
  const el=document.querySelector(selector); if (!el) return;
  AS.original.push([el,el.innerHTML]); el.innerHTML=html;
}
async function routeAnalysis(hash) {
  const same=hash.match(/^#\/analysis\/([a-f0-9]{64})(?:\/(\w+))?$/);
  if(same && S.slug===same[1] && S.ov?.kind==='analysis' && !$('project').hidden) {
    closeAll();closeNav();setAnalysisTab(same[2]||'overview');markNav();return;
  }
  closeAll(); closeNav(); hideLaunch(); leaveProject(); restoreAnalysisUI();
  try {
    const match=hash.match(/^#\/analysis\/([a-f0-9]{64})(?:\/(\w+))?$/);
    if (!match) {
      const {projects}=await api('/api/switch/projects');
      if (projects.length) { location.hash=`#/analysis/${projects[0].slug}`; return; }
      await showHome(); importAnalysis(); return;
    }
    await openAnalysisProject(match[1],match[2]||'overview');
  } catch(e) { toast(esc(e.message),'err'); }
}
async function openAnalysisProject(slug,tab) {
  show('project'); S.slug=slug; S.ov={kind:'analysis'}; S.map=[]; S.claims=[]; S.scores=new Map(); S.flash=new Map(); S.byName=new Map(); geo=null;
  AS.generation++; AS.catalog=[]; AS.catalogStamp=0; AS.catalogTotal=0; AS.seen=0; AS.job=0; AS.aiSeen={}; AS.aiJob={};
  L.q='';L.filter='all';L.limit=200;L.sort='i';$('fnSearch').value='';
  document.body.classList.add('analysis-project');
  $('forkSetup').hidden=true; $('ptabs').hidden=false; $('compilerNotice').hidden=false;
  $('compilerNotice').textContent='Ghidra decompiles machine code into C-like source. Antigravity reconstructs readable candidates. Neither stage is compiler-verified yet; discovery may be partial.';
  for(const el of document.querySelectorAll('#ptabs [data-ptab]')) {
    el.hidden=!['overview','functions','activity'].includes(el.dataset.ptab);
    el.href=`#/analysis/${slug}/${el.dataset.ptab}`;
  }
  rememberAnalysisText('.ph-pct','<b id="phPct">0</b>% decompiled');
  const labels=['Decompiled functions','Internal functions','Current run','Workers'];
  document.querySelectorAll('.kpis .label').forEach((el,i)=>{AS.original.push([el,el.innerHTML]);el.textContent=labels[i];});
  rememberAnalysisText('.map-panel .legend','<span><i class="sw open"></i>to do</span><span><i class="sw work"></i>decompiling</span><span><i class="sw draft"></i>decompiled</span><span><i class="sw candidate"></i>AI candidate</span><span><i class="sw failed"></i>failed</span>');
  rememberAnalysisText('#project [data-pane="overview"] .cols section:first-child h2','Recent decompilations');
  rememberAnalysisText('#project [data-pane="overview"] .cols section:last-child h2','Local worker');
  document.querySelectorAll('#project [data-pane="overview"] .cols a').forEach(el=>{AS.original.push([el,el.innerHTML]);el.textContent=el.dataset.goto==='functions'?'All functions':'Activity';});
  const action=document.createElement('div'); action.id='analysisActions';action.className='row';
  action.innerHTML='<label id="analysisCheckpointField" class="small" hidden>Functions<input id="analysisBatch" type="number" min="1" max="500" value="500" aria-label="Functions in one checkpoint"></label><label class="analysis-keep small"><input type="checkbox" id="analysisContinuous" checked> Keep going</label><button class="primary" id="analysisRun">Decompile remaining</button><button class="ghost" id="analysisCancel">Stop</button><button class="ghost" id="analysisImport">Import module</button>';
  document.querySelector('.ph-actions').append(action);
  $('analysisRun').onclick=()=>startAnalysisBatch();
  $('analysisContinuous').onchange=()=>{$('analysisRun').textContent=$('analysisContinuous').checked?'Decompile remaining':'Run one checkpoint';$('analysisCheckpointField').hidden=$('analysisContinuous').checked;};
  $('analysisCancel').onclick=async()=>{try {await api('/api/switch/cancel',{method:'POST',body:{id:S.slug}}); await pollAnalysis(false);}catch(e){toast(esc(e.message),'err');}};
  $('analysisImport').onclick=importAnalysis;
  mountAnalysisAgent();
  const live=document.createElement('div');live.id='analysisLive';live.className='autopilot';
  live.innerHTML='<span class="ap-icon" aria-hidden="true"></span><div class="ap-text"><b id="analysisPhase"></b><span class="muted small" id="analysisCurrent" aria-live="polite"></span></div><span class="mono small" id="analysisElapsed"></span>';
  document.querySelector('.map-panel').before(live);
  $('activity').replaceChildren();$('thoughts').replaceChildren();$('now').replaceChildren();
  $('mapTitle').textContent='Function map';$('mapHint').textContent='Every square is a discovered function. Amber pulses while that function is being decompiled; blue is decompiled source, purple is an AI candidate. Both remain unverified.';
  $('thAddr').textContent='Address';
  setAnalysisTab(tab);await pollAnalysis(true);markNav();
  S.timer=setInterval(()=>pollAnalysis(false),500);
}
function setAnalysisTab(tab) {
  if(!['overview','functions','activity'].includes(tab))tab='activity';
  S.tab=tab;
  document.querySelectorAll('#ptabs [data-ptab]').forEach(el=>{el.classList.toggle('on',el.dataset.ptab===tab);el.setAttribute('aria-selected',String(el.dataset.ptab===tab));});
  document.querySelectorAll('[data-pane]').forEach(el=>el.hidden=el.dataset.pane!==tab);
  if(tab==='overview'&&S.map.length)layoutMap();
  if(tab==='functions')renderAnalysisList();
}
async function startAnalysisBatch() {
  const id=S.slug; $('analysisRun').disabled=true;
  try {await api('/api/switch/start',{method:'POST',body:{id,limit:Number($('analysisBatch').value),continuous:$('analysisContinuous').checked}}); await pollAnalysis(false);}
  catch(e){toast(esc(e.message),'err');$('analysisRun').disabled=false;}
}
async function pollAnalysis(first) {
  if(AS.polling || S.ov?.kind!=='analysis')return;
  AS.polling=true;const gen=AS.generation,id=S.slug;
  try {
    const p=await api(`/api/switch/project?id=${id}`);
    if(gen!==AS.generation || id!==S.slug)return;
    if(!AS.catalog.length || AS.catalogStamp!==(p.catalog_version||0) || AS.catalogTotal!==(p.summary.total_functions||0)) {
      const catalog=await api(`/api/switch/map?id=${id}`);
      if(gen!==AS.generation)return;
      AS.catalog=catalog; AS.catalogStamp=p.catalog_version||0; AS.catalogTotal=p.summary.total_functions||0;
    }
    const old=new Map(S.map.map(f=>[f.address,f.status]));
    S.ov=p;
    const done=new Map(p.functions.map(f=>[f.address,f]));
    const active=analysisActive(p);
    const ai=p.ai||{state:'idle',candidates:[],events:[]};
    const aiActiveWorkers=aiWorkers(ai).filter(aiWorkerActive), aiCount=aiActiveWorkers.length;
    const aiAddresses=new Set(aiActiveWorkers.filter(w=>w.current).map(w=>w.current.address));
    const candidates=new Set(ai.candidates.map(c=>c.address));
    S.map=AS.catalog.map((f,i)=>({...f,i,name:f.address,label:f.name,insns:Math.ceil(f.size/4),status:aiAddresses.has(f.address)?'reconstructing':p.current_function?.address===f.address?'working':candidates.has(f.address)?'candidate':done.get(f.address)?.status||'open'}));
    S.byName=new Map(S.map.map((f,i)=>[f.name,i]));
    for(const f of S.map)if(!first && ['drafted','candidate'].includes(f.status) && old.get(f.address)!==f.status)S.flash.set(f.name,performance.now());
    if(AS.job!==p.job_started){AS.job=p.job_started;AS.seen=0;$('activity').replaceChildren();}
    for(const ev of p.events.slice(AS.seen)) {
      const li=document.createElement('li');if(!first)li.className='fresh';
      li.innerHTML=`<time>${ev.time?clock(ev.time):''}</time><span><span class="who">Ghidra</span> ${ev.type==='started'?'started':ev.status==='drafted'?'decompiled':'could not decompile'} <button class="link-btn mono" data-address="${esc(ev.address)}">${esc(ev.name)}</button></span>`;
      li.querySelector('button').onclick=()=>openAnalysisFunction(ev.address);$('activity').prepend(li);
    }
    AS.seen=p.events.length;
    while($('activity').children.length>300)$('activity').lastChild.remove();
    $('phTitle').textContent=p.title;$('phChips').innerHTML='<span class="plat">Nintendo Switch</span><span class="chip-s">Local Ghidra worker</span><span class="chip-s">0 verified matches</span>';
    const total=p.summary.internal_functions || (p.catalog_version?AS.catalog.length:p.summary.total_functions) || 0;
    const percent=total?100*p.drafted/total:0;
    $('phPct').textContent=total?percent.toFixed(2):'—';$('phBar').style.width=percent+'%';
    $('phSub').textContent=`${p.drafted.toLocaleString()} decompiled / ${total.toLocaleString()} internal functions${p.discovery_timed_out?' · discovery incomplete':''}`;
    $('pct').textContent=p.drafted.toLocaleString();$('pctSub').textContent=`${ai.candidate_count||0} AI candidates · 0 verified matches`;
    $('fnCount').textContent=total?total.toLocaleString():'Discovering…';$('fnSub').textContent=`${Math.max(0,total-p.exported).toLocaleString()} remaining · ${p.failed} failed decompilations`;
    $('fnStack').innerHTML=`<i class="analysis-draft-stack" data-w="${percent}"></i>`;setWidths($('fnStack'));
    const runStart=p.run_start_offset??p.batch_offset??0;
    $('paceNum').textContent=`${Math.max(0,p.exported-runStart).toLocaleString()} / ${(p.run_mode==='continuous'?Math.max(0,total-runStart):p.limit).toLocaleString()}`;$('paceSub').textContent=p.run_mode==='continuous'?'Continues through all discovered functions':'Functions attempted in this checkpoint';$('paceBars').replaceChildren();$('progress').replaceChildren();
    $('agNum').textContent=String(Number(active)+aiCount);$('agSub').textContent=[active?'Ghidra':'',aiCount?`${aiCount} Antigravity`:''].filter(Boolean).join(' + ')||'Workers idle';$('spendSub').textContent=ai.started?`Antigravity · ${(ai.tokens||0).toLocaleString()} reported tokens`:'No AI run started';
    $('analysisPhase').textContent=p.error || (active?p.phase||'Starting worker':p.state==='imported'?'Ready to decompile':p.state==='completed'?'Checkpoint finished':p.state);
    $('analysisCurrent').textContent=p.current_function ? `${p.current_function.name} · ${p.current_function.address}` : active?'Reading and analyzing your executable; waiting for the next function event.':`${p.drafted.toLocaleString()} decompilations saved. Start Antigravity to reconstruct source candidates.`;
    $('analysisLive').classList.toggle('running',active);
    $('analysisElapsed').textContent=active&&p.job_started?Math.floor(Date.now()/1000-p.job_started)+'s elapsed':'';
    $('analysisRun').disabled=active;$('analysisCancel').disabled=!active;$('analysisBatch').disabled=active;$('analysisContinuous').disabled=active;
    if(active)$('analysisContinuous').checked=!!p.continuous;
    $('analysisCheckpointField').hidden=$('analysisContinuous').checked;
    renderAnalysisAgent(ai,first);
    $('fnTabCount').textContent=total.toLocaleString();
    $('now').innerHTML=p.current_function?`<div class="job"><div class="job-top"><span class="job-name">${esc(p.current_function.name)}</span><span class="job-who">Ghidra</span></div><div class="job-foot">${esc(p.current_function.address)} · decompiling now</div></div>`:'';
    $('now').innerHTML+=aiActiveWorkers.filter(w=>w.current).map(w=>`<div class="job"><div class="job-top"><span class="job-name">${esc(w.current.name)}</span><span class="job-who">Antigravity ${w.worker}</span></div><div class="job-foot">${esc(w.current.address)} · AI reconstructing</div></div>`).join('');
    $('recentMatches').innerHTML=p.functions.slice(-8).reverse().map(f=>`<li><button class="link-btn mono" data-address="${esc(f.address)}">${esc(f.name)}</button><span class="muted small">${esc(analysisStatus(f.status))}</span></li>`).join('');
    $('recentMatches').querySelectorAll('button').forEach(b=>b.onclick=()=>openAnalysisFunction(b.dataset.address));
    $('board').innerHTML=`<li><b>Ghidra</b><span>${active?'Running':'Idle'} · ${p.drafted.toLocaleString()} decompiled</span></li><li><b>Antigravity</b><span>${aiCount} active · ${ai.candidate_count||0} AI candidates · 0 verified matches</span></li>`;
    if(S.tab==='overview'){if(!geo||S.map.length!==old.size)layoutMap();else drawMap();}
    if(S.tab==='functions')renderAnalysisList();
  } catch(e){if(gen===AS.generation)toast(esc(e.message),'err');}
  finally{AS.polling=false;}
}
function renderAnalysisList() {
  if(S.tab!=='functions')return;
  const filters=[['all','All'],['open','To do'],['working','Decompiling'],['drafted','Decompiled'],['reconstructing','AI working'],['candidate','AI candidates'],['failed','Failed']];
  $('fnFilter').innerHTML=filters.map(([k,t])=>`<button type="button" role="radio" aria-checked="${L.filter===k}" class="${L.filter===k?'on':''}" data-filter="${k}">${t}<span>${S.map.filter(f=>k==='all'||f.status===k).length.toLocaleString()}</span></button>`).join('');
  const q=L.q.toLowerCase();const rows=S.map.filter(f=>(L.filter==='all'||f.status===L.filter)&&(f.label.toLowerCase().includes(q)||f.address.toLowerCase().includes(q)));
  const key=f=>L.sort==='name'?f.label:L.sort==='insns'?f.insns:L.sort==='status'?f.status:f.i;
  rows.sort((a,b)=>(key(a)<key(b)?-1:key(a)>key(b)?1:0)*L.dir);L.rows=rows;
  $('fnRows').innerHTML=rows.slice(0,L.limit).map(f=>`<tr data-fn="${esc(f.address)}" tabindex="0"><td class="mono dim">${esc(f.address)}</td><td class="mono name">${esc(f.label)}</td><td class="r mono">${f.insns}</td><td class="r">—</td><td class="r">${f.status==='drafted'||f.status==='failed'?1:0}</td><td><span class="st ${f.status==='working'?'working':''}">${esc(analysisStatus(f.status))}</span></td></tr>`).join('')||'<tr><td colspan="6">No matching functions.</td></tr>';
  $('fnShown').textContent=`${Math.min(rows.length,L.limit).toLocaleString()} / ${rows.length.toLocaleString()} shown`;$('fnMore').hidden=rows.length<=L.limit;
}
function analysisMapTip(f,ev) {
  const tip=$('tip');tip.innerHTML=`<b>${esc(f.label)}</b><br>${esc(f.address)} · ${f.insns} instructions<br>${esc(analysisStatus(f.status))}`;tip.hidden=false;
  const r=$('mapWrap').getBoundingClientRect();tip.style.left=Math.min(ev.clientX-r.left+14,r.width-250)+'px';tip.style.top=ev.clientY-r.top+14+'px';
}
async function openAnalysisFunction(address) {
  const serial=++AS.detailSerial,id=S.slug;
  const f=S.map[S.byName.get(address)];S.fn=address;
  $('drawer').hidden=false;document.body.classList.add('locked');drawMap();
  $('fnName').textContent=f?.label||address;$('fnAddr').textContent=address;$('fnStatus').textContent=analysisStatus(f?.status||'');
  $('analysisSourceTabs')?.remove();$('analysisCandidateNotes')?.remove();
  $('fnCodeView').hidden=false;$('fnDiffView').hidden=true;$('tryMsg').hidden=true;
  $('copySrc').onclick=()=>navigator.clipboard.writeText($('fnEdit').value);
  $('fnSrcKind').textContent='C-like draft · unverified';$('fnSrc').textContent='Loading…';$('fnEdit').value='';$('fnEdit').readOnly=true;$('fnAsm').textContent='';$('fnXref').replaceChildren();$('fnNotes').replaceChildren();
  try {
    const d=await api(`/api/switch/function?id=${id}&address=${encodeURIComponent(address)}`);
    if(serial!==AS.detailSerial||S.slug!==id||S.fn!==address)return;
    $('fnAsm').textContent=d.assembly;$('fnSrc').innerHTML=highlightC(d.c||d.error||'');$('fnEdit').value=d.c||d.error||'';
    $('fnXref').innerHTML=(d.calls||[]).map(c=>`<button class="link-btn mono" data-address="${esc(c.address)}">${esc(c.name)}</button>`).join(' ');
    $('fnXref').querySelectorAll('button').forEach(b=>b.onclick=()=>openAnalysisFunction(b.dataset.address));
    if(d.candidate)showAnalysisCandidate(d);
  }catch(e){if(serial===AS.detailSerial){$('fnSrc').textContent='This function has not been drafted yet.';$('fnEdit').value='This function has not been drafted yet.';}}
}
function importAnalysis() {
  dialog('Open a Switch executable','<p class="muted">Select an extracted NSO main, ExeFS folder, or AArch64 ELF.</p><label class="field"><span>Local path</span><input id="switchPath"></label><label class="field"><span>Project name</span><input id="switchTitle"></label><label class="check"><input id="switchOwn" type="checkbox"><span>This is my game dump or original test program.</span></label><button class="primary" id="switchImport">Import</button>');
  $('switchImport').onclick=async()=>{try{const p=await api('/api/switch/import',{method:'POST',body:{path:$('switchPath').value,title:$('switchTitle').value,confirmed:$('switchOwn').checked}});closeAll();await loadNav();location.hash=`#/analysis/${p.slug}`;}catch(e){toast(esc(e.message),'err');}};
}

const aiWorkerActive = w => ['starting','reconstructing'].includes(w.state);
function aiWorkers(ai) { return ai.workers || [{...ai,worker:1}]; }
function aiControl(name,worker) { return $(`analysisAI${name}${worker===1?'':worker}`); }
function mountAnalysisAgent() {
  const panel=document.createElement('section');panel.id='analysisAgent';panel.className='panel';
  panel.innerHTML=`<div class="panel-head"><h2>AI source reconstruction</h2><span id="analysisAIState" class="small muted">Loading workers…</span></div>
    <div class="analysis-agent-controls analysis-worker-toolbar"><button class="primary" id="analysisAIStartAll" disabled>Start all idle workers</button><button class="ghost" id="analysisAIStopAll" disabled>Stop all AI</button><span class="small muted">5 parallel workers · up to 100 functions each</span></div>
    <p id="analysisAIHelp" class="small muted analysis-agent-note">Each worker claims different functions. Choose any available model in each row. Candidates remain unverified.</p>
    <div class="analysis-worker-list">${[1,2,3,4,5].map(n=>{
      const suffix=n===1?'':n;
      return `<section class="analysis-worker" data-worker="${n}" aria-label="Antigravity worker ${n}">
        <form class="analysis-agent-controls"><b>Worker ${n}</b>
        <label>Model<select id="analysisAIModel${suffix}" disabled><option value="">Antigravity default</option></select></label>
        <label>Functions this run<input id="analysisAILimit${suffix}" type="number" min="1" max="100" step="1" value="100" required></label>
        <button class="primary" id="analysisAIStart${suffix}" type="submit" disabled>Start worker ${n}</button>
        <button class="ghost" id="analysisAIStop${suffix}" type="button" disabled>Stop worker ${n}</button></form>
        <p id="analysisAIProgress${suffix}" class="small muted analysis-agent-note" aria-live="polite">Ready</p>
        <div id="analysisAIFeed${suffix}" class="analysis-agent-feed small"></div></section>`;
    }).join('')}</div>`;
  document.querySelector('.map-panel').before(panel);
  const gen=AS.generation,id=S.slug;
  let batch=0;
  const current=()=>gen===AS.generation && panel.isConnected;
  const start=async worker=>{
    const row=panel.querySelector(`[data-worker="${worker}"]`),form=row.querySelector('form');
    if(!current() || aiControl('Start',worker).disabled || !form.reportValidity())return;
    row.dataset.pending='start';delete row.dataset.error;
    renderAnalysisAgent(S.ov.ai||{},false);
    try {
      const ai=await api('/api/switch/agent/start',{method:'POST',body:{id,worker,limit:Number(aiControl('Limit',worker).value),model:aiControl('Model',worker).value}});
      if(current())S.ov.ai=ai;
    } catch(e) {if(current()){row.dataset.error=e.message;toast(esc(e.message),'err');}}
    finally {if(current()){delete row.dataset.pending;renderAnalysisAgent(S.ov.ai||{},false);}}
  };
  for(const worker of [1,2,3,4,5]) {
    const row=panel.querySelector(`[data-worker="${worker}"]`);
    row.querySelector('form').onsubmit=event=>{event.preventDefault();start(worker);};
    aiControl('Stop',worker).onclick=async()=>{
      row.dataset.pending='stop';delete row.dataset.error;renderAnalysisAgent(S.ov.ai||{},false);
      try {const ai=await api('/api/switch/agent/stop',{method:'POST',body:{id,worker}});if(current())S.ov.ai=ai;}
      catch(e){if(current())row.dataset.error=e.message;}
      finally{if(current()){delete row.dataset.pending;renderAnalysisAgent(S.ov.ai||{},false);}}
    };
  }
  $('analysisAIStartAll').onclick=async()=>{
    const workers=[1,2,3,4,5].filter(n=>!aiControl('Start',n).disabled);
    if(workers.some(n=>!panel.querySelector(`[data-worker="${n}"] form`).reportValidity()))return;
    const token=++batch;panel.dataset.startingAll='true';renderAnalysisAgent(S.ov.ai||{},false);
    for(const n of workers){if(!current() || token!==batch)break;await start(n);}
    delete panel.dataset.startingAll;if(current())renderAnalysisAgent(S.ov.ai||{},false);
  };
  $('analysisAIStopAll').onclick=async()=>{
    batch++;
    $('analysisAIStopAll').disabled=true;
    try{const ai=await api('/api/switch/agent/stop',{method:'POST',body:{id}});if(current()){S.ov.ai=ai;renderAnalysisAgent(ai,false);}}
    catch(e){if(current())toast(esc(e.message),'err');}
  };
  api('/api/switch/agent/config').then(cfg=>{
    if(!current())return;
    const families=['gemini-','claude-opus-','claude-sonnet-','gpt-oss-','gemini-'];
    for(const worker of [1,2,3,4,5]) {
      const select=aiControl('Model',worker);
      select.innerHTML='<option value="">Antigravity default</option>'+cfg.models.map(m=>`<option value="${esc(m.id)}">${esc(m.name)}</option>`).join('');
      const preferred=cfg.models.find(m=>m.id.startsWith(families[worker-1]) && (worker!==5 || m.id.endsWith('medium')))||cfg.models.find(m=>m.id===cfg.default_model)||cfg.models[0];
      select.value=preferred?.id||'';
    }
    panel.dataset.ready=String(cfg.installed && cfg.max_workers===5);
    if(!cfg.installed)$('analysisAIHelp').textContent=cfg.signin;
    else if(!cfg.max_workers)$('analysisAIHelp').textContent='The app needs to restart to enable multiple workers.';
    renderAnalysisAgent(S.ov.ai||{},true);
  }).catch(e=>{if(current())$('analysisAIHelp').textContent=e.message;});
}
function renderAnalysisAgent(ai,first) {
  const panel=$('analysisAgent');if(!panel)return;
  const workers=aiWorkers(ai),count=workers.filter(aiWorkerActive).length,ready=panel.dataset.ready==='true';
  $('analysisAIState').textContent=`${count} / 5 workers active`;
  for(const worker of [1,2,3,4,5]) {
    const w=workers.find(w=>w.worker===worker)||{state:'idle',events:[]};
    const row=panel.querySelector(`[data-worker="${worker}"]`),active=aiWorkerActive(w),pending=!!row.dataset.pending;
    aiControl('Start',worker).disabled=!ready || active || pending;
    aiControl('Stop',worker).disabled=!active || pending;
    aiControl('Model',worker).disabled=aiControl('Limit',worker).disabled=!ready || active || pending;
    aiControl('Start',worker).textContent=row.dataset.pending==='start'?'Starting…':`Start worker ${worker}`;
    aiControl('Stop',worker).textContent=row.dataset.pending==='stop'?'Stopping…':`Stop worker ${worker}`;
    if(active && ready){aiControl('Model',worker).value=w.model||'';aiControl('Limit',worker).value=w.limit||100;}
    const progress=w.started?`${w.state} · ${w.completed||0} / ${w.limit} candidates saved · ${w.current?.name||'No active function'} · ${w.model||'Antigravity default'}`:'Ready';
    aiControl('Progress',worker).textContent=row.dataset.error||w.error||progress;
    if(AS.aiJob[worker]!==w.started){AS.aiJob[worker]=w.started;AS.aiSeen[worker]=0;aiControl('Feed',worker).replaceChildren();}
    for(const ev of (w.events||[]).filter(e=>e.seq>(AS.aiSeen[worker]||0))){
      const item=document.createElement('div');item.textContent=ev.text;aiControl('Feed',worker).append(item);
      const li=document.createElement('li');if(!first)li.className='fresh';
      li.innerHTML=`<time>${clock(ev.time)}</time><span><span class="who">Antigravity ${worker}</span> ${esc(ev.text)}</span>`;$('activity').prepend(li);
      AS.aiSeen[worker]=ev.seq;
    }
    while(aiControl('Feed',worker).children.length>2)aiControl('Feed',worker).firstChild.remove();
  }
  $('analysisAIStartAll').disabled=!ready || !!panel.dataset.startingAll || [1,2,3,4,5].every(n=>aiControl('Start',n).disabled);
  $('analysisAIStopAll').disabled=!count;
}
function showAnalysisCandidate(d) {
  const tabs=document.createElement('div');tabs.id='analysisSourceTabs';tabs.className='row';
  tabs.innerHTML='<button class="ghost" id="analysisShowGhidra">Ghidra decompilation</button><button class="primary" id="analysisShowCandidate">Antigravity candidate</button>';
  $('fnCodeView').before(tabs);
  const notes=document.createElement('section');notes.id='analysisCandidateNotes';
  notes.innerHTML=`<h3>Candidate evidence · unverified</h3><p>${esc(d.candidate.summary)}</p><p class="small muted">${esc(d.candidate.model)}</p><ul>${d.candidate.evidence.map(e=>`<li>${esc(e)}</li>`).join('')}</ul><h4>Unresolved</h4><ul>${d.candidate.uncertainties.map(e=>`<li>${esc(e)}</li>`).join('')||'<li>No uncertainties supplied by the model. Independent verification is still required.</li>'}</ul>`;
  $('fnCodeView').after(notes);
  const choose=ai=>{const source=ai?d.candidate.source:d.c;$('fnEdit').value=source;$('fnSrc').innerHTML=highlightC(source);$('fnSrcKind').textContent=ai?'Antigravity candidate · unverified':'Ghidra decompilation · unverified';notes.hidden=!ai;};
  $('analysisShowGhidra').onclick=()=>choose(false);$('analysisShowCandidate').onclick=()=>choose(true);choose(true);
}
