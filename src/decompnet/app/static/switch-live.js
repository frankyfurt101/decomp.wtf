"use strict";
/* Switch analysis adapts the original application views and canvas. No simulated work. */
const AS = {polling:false, generation:0, catalog:[], catalogStamp:0, catalogTotal:0, seen:0, job:0, original:[], detailSerial:0};
const analysisActive = p => ["queued","analyzing","drafting"].includes(p.state);
function analysisNav(p) {
  return `<a class="nav-proj" href="#/analysis/${esc(p.slug)}" data-slug="${esc(p.slug)}"><span class="np-top"><span class="np-name">${esc(p.title)}</span>${analysisActive(p)?'<span class="dot work" title="Ghidra is running"></span>':''}</span><span class="np-meta">Switch <span class="grow"></span>${p.drafted.toLocaleString()} drafts</span></a>`;
}
function analysisCard(p) {
  return `<a class="card" href="#/analysis/${esc(p.slug)}"><div class="cc-top"><span class="plat">Nintendo Switch</span><span class="meta">${esc(p.state)}</span></div><div><h4>${esc(p.title)}</h4><p class="meta">${p.drafted.toLocaleString()} drafts · 0 verified matches</p></div></a>`;
}
function restoreAnalysisUI() {
  if (!document.body.classList.contains("analysis-project")) return;
  AS.generation++; AS.detailSerial++;
  document.body.classList.remove("analysis-project");
  for (const [el,html] of AS.original) el.innerHTML=html;
  AS.original=[];
  document.querySelectorAll('#ptabs [data-ptab]').forEach(el=>el.hidden=false);
  $("analysisActions")?.remove(); $("analysisLive")?.remove();
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
  AS.generation++; AS.catalog=[]; AS.catalogStamp=0; AS.catalogTotal=0; AS.seen=0; AS.job=0;
  L.q='';L.filter='all';L.limit=200;L.sort='i';$('fnSearch').value='';
  document.body.classList.add('analysis-project');
  $('forkSetup').hidden=true; $('ptabs').hidden=false; $('compilerNotice').hidden=false;
  $('compilerNotice').textContent='Ghidra generates C-like drafts locally. Compiler verification and AI reconstruction are not implemented for Switch yet. Discovery may be partial.';
  for(const el of document.querySelectorAll('#ptabs [data-ptab]')) {
    el.hidden=!['overview','functions','activity'].includes(el.dataset.ptab);
    el.href=`#/analysis/${slug}/${el.dataset.ptab}`;
  }
  rememberAnalysisText('.ph-pct','<b id="phPct">0</b>% drafted');
  const labels=['Drafted functions','Internal functions','This batch','Workers'];
  document.querySelectorAll('.kpis .label').forEach((el,i)=>{AS.original.push([el,el.innerHTML]);el.textContent=labels[i];});
  rememberAnalysisText('.map-panel .legend','<span><i class="sw open"></i>to do</span><span><i class="sw work"></i>decompiling</span><span><i class="sw draft"></i>drafted</span><span><i class="sw failed"></i>failed</span>');
  rememberAnalysisText('#project [data-pane="overview"] .cols section:first-child h2','Recent drafts');
  rememberAnalysisText('#project [data-pane="overview"] .cols section:last-child h2','Local worker');
  document.querySelectorAll('#project [data-pane="overview"] .cols a').forEach(el=>{AS.original.push([el,el.innerHTML]);el.textContent=el.dataset.goto==='functions'?'All functions':'Activity';});
  const action=document.createElement('div'); action.id='analysisActions';action.className='row';
  action.innerHTML='<label class="small" for="analysisBatch">Next batch</label><input id="analysisBatch" type="number" min="1" max="500" value="500" aria-label="Functions in next batch"><button class="primary" id="analysisRun">Continue decompiling</button><button class="ghost" id="analysisCancel">Stop</button><button class="ghost" id="analysisImport">Import module</button>';
  document.querySelector('.ph-actions').append(action);
  $('analysisRun').onclick=()=>startAnalysisBatch();
  $('analysisCancel').onclick=async()=>{try {await api('/api/switch/cancel',{method:'POST',body:{id:S.slug}}); await pollAnalysis(false);}catch(e){toast(esc(e.message),'err');}};
  $('analysisImport').onclick=importAnalysis;
  const live=document.createElement('div');live.id='analysisLive';live.className='autopilot';
  live.innerHTML='<span class="ap-icon" aria-hidden="true"></span><div class="ap-text"><b id="analysisPhase"></b><span class="muted small" id="analysisCurrent" aria-live="polite"></span></div><span class="mono small" id="analysisElapsed"></span>';
  document.querySelector('.map-panel').before(live);
  $('activity').replaceChildren();$('thoughts').replaceChildren();$('now').replaceChildren();
  $('mapTitle').textContent='Function map';$('mapHint').textContent='Every square is a discovered function. Amber pulses while that function is being decompiled; blue marks an unverified draft.';
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
  try {await api('/api/switch/start',{method:'POST',body:{id,limit:Number($('analysisBatch').value)}}); await pollAnalysis(false);}
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
    S.map=AS.catalog.map((f,i)=>({...f,i,name:f.address,label:f.name,insns:Math.ceil(f.size/4),status:p.current_function?.address===f.address?'working':done.get(f.address)?.status||'open'}));
    S.byName=new Map(S.map.map((f,i)=>[f.name,i]));
    for(const f of S.map)if(!first && f.status==='drafted' && old.get(f.address)!=='drafted')S.flash.set(f.name,performance.now());
    if(AS.job!==p.job_started){AS.job=p.job_started;AS.seen=0;$('activity').replaceChildren();}
    for(const ev of p.events.slice(AS.seen)) {
      const li=document.createElement('li');if(!first)li.className='fresh';
      li.innerHTML=`<time>${ev.time?clock(ev.time):''}</time><span><span class="who">Ghidra</span> ${ev.type==='started'?'started':ev.status==='drafted'?'drafted':'could not decompile'} <button class="link-btn mono" data-address="${esc(ev.address)}">${esc(ev.name)}</button></span>`;
      li.querySelector('button').onclick=()=>openAnalysisFunction(ev.address);$('activity').prepend(li);
    }
    AS.seen=p.events.length;
    while($('activity').children.length>300)$('activity').lastChild.remove();
    $('phTitle').textContent=p.title;$('phChips').innerHTML='<span class="plat">Nintendo Switch</span><span class="chip-s">Local Ghidra worker</span><span class="chip-s">0 verified matches</span>';
    const total=p.summary.internal_functions || (p.catalog_version?AS.catalog.length:p.summary.total_functions) || 0;
    const percent=total?100*p.drafted/total:0;
    $('phPct').textContent=total?percent.toFixed(2):'—';$('phBar').style.width=percent+'%';
    $('phSub').textContent=`${p.drafted.toLocaleString()} drafts / ${total.toLocaleString()} internal functions${p.discovery_timed_out?' · discovery incomplete':''}`;
    $('pct').textContent=p.drafted.toLocaleString();$('pctSub').textContent='C-like drafts; 0 compiler-verified matches';
    $('fnCount').textContent=total?total.toLocaleString():'Discovering…';$('fnSub').textContent=`${Math.max(0,total-p.exported).toLocaleString()} remaining · ${p.failed} failed drafts`;
    $('fnStack').innerHTML=`<i class="analysis-draft-stack" data-w="${percent}"></i>`;setWidths($('fnStack'));
    $('paceNum').textContent=`${Math.max(0,p.exported-(p.batch_offset||0))} / ${p.limit}`;$('paceSub').textContent='Functions attempted in this batch';$('paceBars').replaceChildren();$('progress').replaceChildren();
    $('agNum').textContent=active?'1':'0';$('agSub').textContent=active?'Ghidra is running locally':'Local worker idle';$('spendSub').textContent='No paid model sessions';
    $('analysisPhase').textContent=p.error || (active?p.phase||'Starting worker':p.state==='imported'?'Ready to decompile':p.state==='completed'?'Batch finished':p.state);
    $('analysisCurrent').textContent=p.current_function ? `${p.current_function.name} · ${p.current_function.address}` : active?'Reading and analyzing your executable; waiting for the next function event.':`${p.drafted.toLocaleString()} drafts saved. Continue with the next functions.`;
    $('analysisLive').classList.toggle('running',active);
    $('analysisElapsed').textContent=active&&p.job_started?Math.floor(Date.now()/1000-p.job_started)+'s elapsed':'';
    $('analysisRun').disabled=active;$('analysisCancel').disabled=!active;$('analysisBatch').disabled=active;
    $('fnTabCount').textContent=total.toLocaleString();
    $('now').innerHTML=p.current_function?`<div class="job"><div class="job-top"><span class="job-name">${esc(p.current_function.name)}</span><span class="job-who">Ghidra</span></div><div class="job-foot">${esc(p.current_function.address)} · decompiling now</div></div>`:'';
    $('recentMatches').innerHTML=p.functions.slice(-8).reverse().map(f=>`<li><button class="link-btn mono" data-address="${esc(f.address)}">${esc(f.name)}</button><span class="muted small">${esc(f.status)}</span></li>`).join('');
    $('recentMatches').querySelectorAll('button').forEach(b=>b.onclick=()=>openAnalysisFunction(b.dataset.address));
    $('board').innerHTML=`<li><b>Ghidra</b><span>${active?'Running':'Idle'} · ${p.drafted.toLocaleString()} drafts · 0 verified matches</span></li>`;
    if(S.tab==='overview'){if(!geo||S.map.length!==old.size)layoutMap();else drawMap();}
    if(S.tab==='functions')renderAnalysisList();
  } catch(e){if(gen===AS.generation)toast(esc(e.message),'err');}
  finally{AS.polling=false;}
}
function renderAnalysisList() {
  if(S.tab!=='functions')return;
  const filters=[['all','All'],['open','To do'],['working','Working'],['drafted','Drafted'],['failed','Failed']];
  $('fnFilter').innerHTML=filters.map(([k,t])=>`<button type="button" role="radio" aria-checked="${L.filter===k}" class="${L.filter===k?'on':''}" data-filter="${k}">${t}<span>${S.map.filter(f=>k==='all'||f.status===k).length.toLocaleString()}</span></button>`).join('');
  const q=L.q.toLowerCase();const rows=S.map.filter(f=>(L.filter==='all'||f.status===L.filter)&&(f.label.toLowerCase().includes(q)||f.address.toLowerCase().includes(q)));
  const key=f=>L.sort==='name'?f.label:L.sort==='insns'?f.insns:L.sort==='status'?f.status:f.i;
  rows.sort((a,b)=>(key(a)<key(b)?-1:key(a)>key(b)?1:0)*L.dir);L.rows=rows;
  $('fnRows').innerHTML=rows.slice(0,L.limit).map(f=>`<tr data-fn="${esc(f.address)}" tabindex="0"><td class="mono dim">${esc(f.address)}</td><td class="mono name">${esc(f.label)}</td><td class="r mono">${f.insns}</td><td class="r">—</td><td class="r">${f.status==='drafted'||f.status==='failed'?1:0}</td><td><span class="st ${f.status==='working'?'working':''}">${esc(f.status==='open'?'to do':f.status)}</span></td></tr>`).join('')||'<tr><td colspan="6">No matching functions.</td></tr>';
  $('fnShown').textContent=`${Math.min(rows.length,L.limit).toLocaleString()} / ${rows.length.toLocaleString()} shown`;$('fnMore').hidden=rows.length<=L.limit;
}
function analysisMapTip(f,ev) {
  const tip=$('tip');tip.innerHTML=`<b>${esc(f.label)}</b><br>${esc(f.address)} · ${f.insns} instructions<br>${esc(f.status)}`;tip.hidden=false;
  const r=$('mapWrap').getBoundingClientRect();tip.style.left=Math.min(ev.clientX-r.left+14,r.width-250)+'px';tip.style.top=ev.clientY-r.top+14+'px';
}
async function openAnalysisFunction(address) {
  const serial=++AS.detailSerial,id=S.slug;
  const f=S.map[S.byName.get(address)];S.fn=address;
  $('drawer').hidden=false;document.body.classList.add('locked');drawMap();
  $('fnName').textContent=f?.label||address;$('fnAddr').textContent=address;$('fnStatus').textContent=f?.status||'';
  $('fnCodeView').hidden=false;$('fnDiffView').hidden=true;$('tryMsg').hidden=true;
  $('copySrc').onclick=()=>navigator.clipboard.writeText($('fnEdit').value);
  $('fnSrcKind').textContent='C-like draft · unverified';$('fnSrc').textContent='Loading…';$('fnEdit').value='';$('fnEdit').readOnly=true;$('fnAsm').textContent='';$('fnXref').replaceChildren();$('fnNotes').replaceChildren();
  try {
    const d=await api(`/api/switch/function?id=${id}&address=${encodeURIComponent(address)}`);
    if(serial!==AS.detailSerial||S.slug!==id||S.fn!==address)return;
    $('fnAsm').textContent=d.assembly;$('fnSrc').innerHTML=highlightC(d.c||d.error||'');$('fnEdit').value=d.c||d.error||'';
    $('fnXref').innerHTML=(d.calls||[]).map(c=>`<button class="link-btn mono" data-address="${esc(c.address)}">${esc(c.name)}</button>`).join(' ');
    $('fnXref').querySelectorAll('button').forEach(b=>b.onclick=()=>openAnalysisFunction(b.dataset.address));
  }catch(e){if(serial===AS.detailSerial){$('fnSrc').textContent='This function has not been drafted yet.';$('fnEdit').value='This function has not been drafted yet.';}}
}
function importAnalysis() {
  dialog('Open a Switch executable','<p class="muted">Select an extracted NSO main, ExeFS folder, or AArch64 ELF.</p><label class="field"><span>Local path</span><input id="switchPath"></label><label class="field"><span>Project name</span><input id="switchTitle"></label><label class="check"><input id="switchOwn" type="checkbox"><span>This is my game dump or original test program.</span></label><button class="primary" id="switchImport">Import</button>');
  $('switchImport').onclick=async()=>{try{const p=await api('/api/switch/import',{method:'POST',body:{path:$('switchPath').value,title:$('switchTitle').value,confirmed:$('switchOwn').checked}});closeAll();await loadNav();location.hash=`#/analysis/${p.slug}`;}catch(e){toast(esc(e.message),'err');}};
}
