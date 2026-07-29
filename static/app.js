const $=id=>document.getElementById(id);
let state=null,socket=null,mainMap=null,dashMap=null,mapMarkers=[],dashMarkers=[],pathLayers=[],dashPaths=[],nightLayer=null,propChart=null,lastSpokenCall="",lastNotifiedCall="",advisorOverride=null,advisorQueue=[],calledUntil=new Map();

function setText(id,v){const n=$(id);if(n)n.textContent=v??""}
function freq(hz){return hz?(hz/1e6).toFixed(6):"0.000000"}
function unit(){return state?.settings?.distance_unit||"mi"}
function dist(v){return v==null?"--":`${Math.round(v).toLocaleString()} ${unit()}`}
function esc(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function starScore(score){const n=Math.max(1,Math.min(5,Math.ceil(score/35)));return"★".repeat(n)+"☆".repeat(5-n)}

function setupMaps(){
  mainMap=L.map("leafletMap",{worldCopyJump:true}).setView([20,0],2);
  dashMap=L.map("dashboardMap",{worldCopyJump:true,zoomControl:false,attributionControl:false}).setView([25,0],1);
  const url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
  [mainMap,dashMap].forEach(m=>L.tileLayer(url,{maxZoom:8,attribution:"© OpenStreetMap"}).addTo(m));
}
function stationLatLon(){return gridToLatLon(state?.settings?.grid||"EM27")}
function gridToLatLon(grid){
  grid=(grid||"").toUpperCase();if(grid.length<4)return null;
  let lon=(grid.charCodeAt(0)-65)*20-180+Number(grid[2])*2+1;
  let lat=(grid.charCodeAt(1)-65)*10-90+Number(grid[3])+.5;
  if(grid.length>=6){lon+=(grid.charCodeAt(4)-65)*5/60-1;lat+=(grid.charCodeAt(5)-65)*2.5/60-.5}
  return[lat,lon];
}
function clearLayers(arr){arr.forEach(x=>x.remove());arr.length=0}
function drawMap(map,rows,markers,paths,compact=false){
  clearLayers(markers);clearLayers(paths);
  const home=stationLatLon();
  if(home){
    const hm=L.circleMarker(home,{radius:compact?6:8,color:"#fff",weight:2,fillColor:"#5bbcff",fillOpacity:1}).addTo(map).bindPopup(`<strong>${esc(state.settings.callsign)}</strong><br>${esc(state.settings.grid)}`);
    markers.push(hm);
  }
  rows.filter(r=>r.latlon).slice(0,compact?40:120).forEach(r=>{
    const color=r.wanted?"#ff5e6c":r.priority>=110?"#ffd166":"#44e0a1";
    const mk=L.circleMarker(r.latlon,{radius:Math.max(4,Math.min(11,3+r.priority/38)),color:"#fff",weight:1,fillColor:color,fillOpacity:.9})
      .addTo(map).bindPopup(`<strong>${esc(r.call)}</strong><br>${r.flag||""} ${esc(r.entity_name)}<br>${r.snr} dB · score ${r.priority}<br>${esc(r.reason)}<br><button onclick="callSpecific('${esc(r.call)}')">Call now</button>`);
    markers.push(mk);
    if(home&&$("showPaths")?.checked!==false){
      const line=L.polyline([home,r.latlon],{color,weight:r.priority>=110?2:1,opacity:compact?.22:.35,dashArray:r.wanted?null:"4 6"}).addTo(map);
      paths.push(line);
    }
  });
  if(map===mainMap&&$("showNight")?.checked)drawNight();
}
function drawNight(){
  if(nightLayer){nightLayer.remove();nightLayer=null}
  const now=new Date(),dayStart=Date.UTC(now.getUTCFullYear(),0,0),day=(now-dayStart)/86500000;
  const decl=-23.44*Math.cos(2*Math.PI*(day+10)/365);
  const subLon=180-((now.getUTCHours()+now.getUTCMinutes()/60)*15);
  const antiLon=((subLon+180+540)%360)-180;
  const pts=[];for(let lat=-90;lat<=90;lat+=3)pts.push([lat,antiLon]);
  // Approximate dark hemisphere as a broad polygon centered opposite the sun.
  const west=((antiLon-90+540)%360)-180,east=((antiLon+90+540)%360)-180;
  const poly=[[-90,west],[90,west],[90,east],[-90,east]];
  nightLayer=L.polygon(poly,{color:"transparent",fillColor:"#000",fillOpacity:.22,interactive:false}).addTo(mainMap);
}

function cleanCalledTargets(){
  const now=Date.now();
  for(const [call,until] of calledUntil.entries())if(until<=now)calledUntil.delete(call);
}

function rankedAdvisors(snapshot){
  cleanCalledTargets();
  const rows=snapshot.recent||[];
  const continents=new Set([...document.querySelectorAll('.continent-filter:checked')].map(x=>x.value));
  let candidates=rows.filter(r=>{
    const code=r.continent||"UNK";
    return continents.has(code)&&!calledUntil.has(r.call);
  });
  if($('optNew')?.checked)candidates=candidates.filter(r=>!r.worked_entity);
  if($('optBand')?.checked)candidates=candidates.filter(r=>r.needed_on_band);
  if($('optWanted')?.checked)candidates=candidates.filter(r=>r.wanted);
  if($('optPota')?.checked)candidates=candidates.filter(r=>(window.potaCache && window.potaCache[r.call.toUpperCase()] || (r.reason && /CQ POTA/i.test(r.reason))));

  // Keep only the newest decode for each call.
  const unique=[];
  const seen=new Set();
  for(const row of candidates){
    if(seen.has(row.call))continue;
    seen.add(row.call);
    unique.push(row);
  }

  return unique.sort((a,b)=>{
    let av=0,bv=0;
    if($('optBest')?.checked){av+=a.priority||0;bv+=b.priority||0}
    if($('optFar')?.checked){av+=(a.distance||0)/100;bv+=(b.distance||0)/100}
    if($('optStrong')?.checked){av+=(a.snr||-30)+30;bv+=(b.snr||-30)+30}
    return bv-av;
  });
}

function selectedAdvisor(snapshot){
  const ranked=rankedAdvisors(snapshot);
  return ranked[0]||snapshot.advisor;
}


function render(snapshot){
  state=snapshot;const s=snapshot.status,a=selectedAdvisor(snapshot);
  const firstRun=$("firstRunPanel");
  if(firstRun)firstRun.hidden=Boolean(snapshot.settings?.callsign&&snapshot.settings?.grid);
advisorOverride=a;
  $("connectionDot").classList.toggle("online",!!s.connected);setText("connectionText",s.connected?`${s.id} connected`:"Waiting for WSJT-X");
  setText("frequency",freq(s.dial_frequency));setText("band",s.band||"--");setText("mode",s.mode||"--");setText("rxdf",s.rx_df??"--");setText("txdf",s.tx_df??"--");
  setText("rigName",s.de_call?`${s.de_call} / FT-710`:"FT-710");setText("commandStatus",s.command_status||"");
  const badge=$("rxTxBadge");badge.textContent=s.transmitting?"TX":(s.decoding?"DECODING":"RX");badge.classList.toggle("tx",!!s.transmitting);

  setText("advisorCall",a?.call||"---");setText("advisorFlag",a?.flag||"");setText("advisorEntity",a?.entity_name||"");setText("advisorReason",a?.reason||"Waiting for a CQ target");
  setText("advisorScore",a?.priority??0);setText("advisorDistance",dist(a?.distance));setText("advisorBearing",a?.bearing==null?"--":`${Math.round(a.bearing)}°`);
  setText("advisorZones",a?`${a.cq_zone||"--"} / ${a.itu_zone||"--"}`:"-- / --");
  const confidence=a?.success_estimate||0;$("confidenceBar").style.width=`${confidence}%`;setText("confidenceText",a?`${confidence}%`:"--");
  $("callButton").disabled=!a||!!s.transmitting;$("callButton").textContent=a?`CALL ${a.call} IN WSJT-X`:"CALL IN WSJT-X";

  if(snapshot.sync_stats){setText("qrzStats", `Successfully synced: ${snapshot.sync_stats.qrz_session} this session / ${snapshot.sync_stats.qrz_all_time} all-time`);setText("lotwStats", `Successfully synced: ${snapshot.sync_stats.lotw_session} this session / ${snapshot.sync_stats.lotw_all_time} all-time`);}
  setText("statDecodes",snapshot.stats.decodes.toLocaleString());setText("statQsos",snapshot.stats.qsos.toLocaleString());setText("statWorked",snapshot.stats.dxcc_worked.toLocaleString());
  setText("statConfirmed",snapshot.stats.dxcc_confirmed.toLocaleString());setText("statWanted",snapshot.stats.wanted.toLocaleString());
  advisorQueue=rankedAdvisors(snapshot).slice(0,5);
  renderAdvisorQueue(advisorQueue);
  if (window.potaSpotsRaw) renderPotaTab(window.potaSpotsRaw);
  renderBandAdvisor(snapshot.band_advisor||{});
  renderStationHealth(snapshot.health||[]);
  renderTimeStatus(snapshot.time||{});
  renderOpening(snapshot.propagation);renderWanted(snapshot.wanted);renderActivity(snapshot.recent);renderRadar(snapshot.radar);renderAwards(snapshot);renderPsk(snapshot.psk_reporter||{});
  drawMap(dashMap,snapshot.recent,dashMarkers,dashPaths,true);drawMap(mainMap,snapshot.recent,mapMarkers,pathLayers,false);
  populateSettings(snapshot.settings);maybeAlert(a);

  if (snapshot.status) {
      const banner = $("updateBanner");
      if (banner) {
          if (snapshot.status.update_available) {
              banner.style.display = "flex";
              banner.style.background = "#3b82f6";
              banner.style.color = "#ffffff";
              if($("updateBranchName")) $("updateBranchName").textContent = snapshot.status.update_branch || "master";
          } else if (snapshot.status.update_error) {
              banner.style.display = "flex";
              banner.style.background = "#ef4444";
              banner.style.color = "#ffffff";
              if($("updateBranchName")) $("updateBranchName").textContent = "Error: " + snapshot.status.update_error;
          } else {
              banner.style.display = "none";
          }
      }
  }
}

function renderAdvisorQueue(rows){
  const box=$("advisorQueue");
  if(!box)return;
  box.innerHTML=rows.length?rows.map((r,index)=>`<article class="queue-card">
    <div class="queue-rank"><span>#${index+1}</span><span>${starScore(r.priority)}</span></div>
    <div class="queue-call" style="display:flex; justify-content:space-between; align-items:center;"><div>${r.flag||""} ${esc(r.call)}</div><div style="font-size:10px;">${getPotaTagHTML(r.call, r.reason)}</div></div>
    <div class="queue-entity">${esc(r.entity_name||"Unknown")}</div>
    <div class="queue-reason">${esc(r.reason||"Interesting CQ")}</div>
    <div class="queue-metrics">
      <div><span>SNR</span><strong>${r.snr}</strong></div>
      <div><span>DIST</span><strong>${r.distance==null?"--":Math.round(r.distance).toLocaleString()}</strong></div>
      <div><span>SCORE</span><strong>${r.priority}</strong></div>
    </div>
    <button class="queue-call-btn" data-call="${esc(r.call)}" onclick="callTargetNow('${esc(r.call)}',this)">CALL ${esc(r.call)}</button>
  </article>`).join(""):`<div class="queue-empty">No stations match the current advisor filters.</div>`;
}

function renderBandAdvisor(a){
  setText("bandRecommendation",a.recommendation||"Insufficient data");
  setText("bandAdvisorSummary",a.summary||"");
  setText("bandConfidence",(a.confidence||"low").toUpperCase());
  const confidence=$("bandConfidence");
  if(confidence)confidence.className=`confidence-${a.confidence||"low"}`;

  const cards=$("bandCards");
  if(cards){
    cards.innerHTML=(a.bands||[]).map((b,index)=>{
      const live=b.live||{},psk=b.psk||{};
      const stars="★".repeat(b.stars||1)+"☆".repeat(5-(b.stars||1));
      const current=b.band===a.current_band;
      return `<article class="band-card ${index===0?"best":""} ${current?"current":""}">
        <div class="band-card-top"><strong>${esc(b.band)}</strong><span>${stars}</span></div>
        <div class="band-score">${Number(b.score||0).toFixed(0)}<small>/100</small></div>
        <div class="band-mini">
          <span>${Number(live.stations||0)} live stations</span>
          <span>${Number(live.entities||0)} entities</span>
          <span>${Number(psk.receivers||0)} PSK receivers</span>
        </div>
        <div class="band-tag">${current?"CURRENT BAND":index===0?"BEST EVIDENCE":(b.confidence||"low").toUpperCase()+" CONFIDENCE"}</div>
      </article>`;
    }).join("")||'<div class="muted">No band evidence yet.</div>';
  }

  const details=$("bandWhyDetails");
  if(details){
    details.innerHTML=(a.bands||[]).slice(0,4).map(b=>`<div class="why-band">
      <strong>${esc(b.band)} — ${Number(b.score||0).toFixed(0)}/100</strong>
      <ul>${(b.evidence||[]).map(x=>`<li>${esc(x)}</li>`).join("")}</ul>
    </div>`).join("");
  }
  setText("bandLimitations",a.limitations||"");
}
function renderStationHealth(items){
  const box=$("healthList");if(!box)return;
  box.innerHTML=items.map(item=>`<div class="health-item">
    <i class="health-light ${item.ok?"ok":""}"></i>
    <div><strong>${esc(item.name)}</strong><small title="${esc(item.detail)}">${esc(item.detail)}</small></div>
  </div>`).join("");
}
function renderTimeStatus(t){
  const drift=t.drift_seconds;
  const warning=Number(state?.settings?.time_warning_seconds||1.0);
  const good=drift!=null&&!t.error&&Math.abs(drift)<=warning;
  const badge=$("timeHealthBadge");
  badge.className=`health-badge ${good?"ok":drift==null?"":"bad"}`;
  badge.textContent=t.error?"ERROR":good?"FT8 READY":drift==null?"CHECKING":"SYNC ADVISED";
  setText("clockDrift",drift==null?"--":`${drift>=0?"+":""}${Number(drift).toFixed(3)} s`);
  setText("timeServerLabel",t.server||"NTP server");
  setText("timeRoundTrip",t.round_trip_ms==null?"-- ms":`${Number(t.round_trip_ms).toFixed(0)} ms RTT`);
  setText("timeLastCheck",t.checked_at?`Checked ${new Date(t.checked_at).toLocaleTimeString()}`:"Not checked");
  if(t.error)setText("timeSyncMessage",t.error);else if(t.sync_message)setText("timeSyncMessage",t.sync_message);
  $("syncTime").disabled=!!t.syncing;
  $("syncTime").textContent=t.syncing?"SYNCHRONIZING…":"SYNC WINDOWS CLOCK";
}
function renderOpening(p){
  setText("openingName",p.opening);const e=Object.entries(p.counts||{}).sort((a,b)=>b[1]-a[1]),m=Math.max(1,...e.map(x=>x[1]));
  $("openingBars").innerHTML=e.length?e.map(([n,c])=>`<div class="opening-row"><span>${esc(n)}</span><div class="bar"><i style="width:${c/m*100}%"></i></div><strong>${c}</strong></div>`).join(""):`<div class="muted">Waiting for CQ activity</div>`;
}
function renderWanted(items){$("wantedList").innerHTML=items.length?items.map(i=>`<div class="wanted-item"><div><strong>${esc(i.pattern)}</strong><div class="muted">${esc(i.kind)}${i.note?` · ${esc(i.note)}`:""}</div></div><button onclick="removeWanted('${esc(i.pattern)}')">Remove</button></div>`).join(""):`<div class="muted">No wanted calls or prefixes yet.</div>`}
function renderActivity(rows){
  const f=($("activityFilter")?.value||"").toUpperCase(),v=rows.filter(r=>!f||`${r.call} ${r.entity_name} ${r.grid}`.toUpperCase().includes(f));
  $("activityCards").innerHTML=v.map(r=>`<article class="activity-card ${r.wanted?"wanted":r.priority>=110?"rare":""}">
    <div class="card-head"><div><div class="card-call">${r.flag||""} ${esc(r.call)}</div><div class="card-entity">${esc(r.entity_name)} · ${esc(r.grid||"No grid")}</div></div><div class="stars">${starScore(r.priority)}</div></div>
    <div class="card-tags">${!r.worked_entity?'<span class="tag new">NEW DXCC</span>':""}${r.needed_on_band?'<span class="tag hot">NEEDED ON BAND</span>':""}${r.wanted?'<span class="tag hot">WANTED</span>':""}${r.snr>=-10?'<span class="tag">STRONG</span>':""}${getPotaTagHTML(r.call, r.reason)}</div>
    <div class="card-metrics"><div><span>SNR</span><strong>${r.snr}</strong></div><div><span>Distance</span><strong>${dist(r.distance)}</strong></div><div><span>Bearing</span><strong>${r.bearing==null?"--":Math.round(r.bearing)+"°"}</strong></div><div><span>Score</span><strong>${r.priority}</strong></div></div>
    <div class="advisor-reason">${esc(r.reason)}</div><div class="card-actions"><button onclick="focusStation('${esc(r.call)}')">MAP</button><button class="primary" onclick="callTargetNow('${esc(r.call)}',this)">CALL ${esc(r.call)}</button></div>
  </article>`).join("")||`<div class="muted">No CQ activity yet.</div>`;
}
function renderRadar(items){
  const html=(items||[]).slice(0,20).map(r=>`<div class="radar-item"><div class="radar-flag">${r.flag||""}</div><div><strong>${esc(r.entity_name)}</strong><div class="radar-meta">${r.stations} stations · avg ${r.avg_snr} dB · ${r.avg_distance?Math.round(r.avg_distance).toLocaleString()+" "+unit():"distance --"}</div></div><div class="radar-score">${r.best_score}</div></div>`).join("")||`<div class="muted">Waiting for classified DX activity.</div>`;
  $("dashboardRadar").innerHTML=html;$("fullRadar").innerHTML=html;
}
function renderAwards(s){
  setText("awardWorked",`${s.stats.dxcc_worked} / 340`);setText("awardConfirmed",`${s.stats.dxcc_confirmed} / 340`);$("awardWorkedBar").style.width=`${Math.min(100,s.stats.dxcc_worked/340*100)}%`;$("awardConfirmedBar").style.width=`${Math.min(100,s.stats.dxcc_confirmed/340*100)}%`;
  setText("awardCqZones",s.awards?.zones?.cq||0);setText("awardItuZones",s.awards?.zones?.itu||0);
  const bands=s.awards?.bands||[],max=Math.max(1,...bands.map(x=>x.worked));$("bandAwards").innerHTML=bands.length?bands.map(b=>`<div class="award-row"><strong>${esc(b.band)}</strong><div class="track"><i style="width:${b.worked/max*100}%"></i></div><span>${b.worked} / ${b.confirmed}</span></div>`).join(""):`<div class="muted">Import an ADIF log.</div>`;
  $("topEntities").innerHTML=(s.top_entities||[]).map((e,i)=>`<div class="entity-rank"><strong>${i+1}</strong><div>${e.flag||""} ${esc(e.entity_name)}<div class="muted">${esc(e.continent||"")}</div></div><strong>${e.heard}</strong></div>`).join("");
}
function populateSettings(s){
  if(!s)return;
  const update = (id, val) => { const el = $(id); if(el && document.activeElement !== el) el.value = val; };
  const updateCb = (id, val) => { const el = $(id); if(el && document.activeElement !== el) el.checked = val; };
  
  update("settingsCallsign", s.callsign||"");
  update("settingsGrid", s.grid||"");
  update("settingsUnit", s.distance_unit||"mi");
  update("settingsAdif", s.adif_path||"");
  update("notificationScore", s.notification_score??110);
  update("voiceScore", s.voice_score??120);
  update("ntpServer", s.ntp_server||"time.google.com");
  update("timeWarning", s.time_warning_seconds??1.0);
  update("settingsQrzKey", s.qrz_api_key||"");
  update("settingsLotwPath", s.lotw_tqsl_path||"C:\\Program Files (x86)\\TrustedQSL\\tqsl.exe");
  update("settingsLotwLoc", s.lotw_station_location||"");
  update("settingsLotwPass", s.lotw_password||"");
  update("pskTimeframe", s.psk_timeframe_minutes||60);
  
  updateCb("settingsQrzAuto", s.qrz_auto_log||false);
  updateCb("settingsLotwAuto", s.lotw_auto_log||false);
  updateCb("settingsAudioAuto", s.audio_auto_start||false);
}

function renderPsk(p){
  window.lastPskRefresh = p.last_refresh ? new Date(p.last_refresh).getTime() : null;
  const rows=p.reports||[];setText('pskCount',rows.length);setText('pskLast',p.last_refresh?p.last_refresh.substring(11,19):'--');
  setText('pskStatus',p.refreshing?'Refreshing…':(p.error?`Error: ${p.error}`:(rows.length?'PSK Reporter data loaded':'No reports in selected window')));
  const withDist=rows.map(r=>({...r,_distance:gridDistance(state.settings.grid,r.receiver_grid)}));
  const far=withDist.filter(r=>r._distance!=null).sort((a,b)=>b._distance-a._distance)[0];
  const best=rows.slice().sort((a,b)=>(b.snr||0)-(a.snr||0))[0];
  setText('pskFarthest',far?`${Math.round(far._distance).toLocaleString()} ${unit()}`:'--');setText('pskBest',best?`${best.receiver_call} ${best.snr} dB`:'--');
  $('pskRows').innerHTML=withDist.map(r=>`<tr><td><strong>${esc(r.receiver_call)}</strong></td><td>${esc(r.receiver_grid||'--')}</td><td>${esc(r.mode||'--')}</td><td>${r.frequency?(r.frequency/1e6).toFixed(6):'--'}</td><td>${r.snr}</td><td>${r.age_seconds==null?'--':Math.floor(r.age_seconds/60)+'m'}</td><td>${r._distance==null?'--':Math.round(r._distance).toLocaleString()+' '+unit()}</td></tr>`).join('')||'<tr><td colspan="7" class="muted">No stations have reported hearing your callsign in this window.</td></tr>';
}
function gridDistance(a,b){const p1=gridToLatLon(a),p2=gridToLatLon(b);if(!p1||!p2)return null;const R=unit()==='mi'?3958.7613:6371.0088,d=Math.PI/180,la1=p1[0]*d,la2=p2[0]*d,dl=(p2[0]-p1[0])*d,dn=(p2[1]-p1[1])*d;const h=Math.sin(dl/2)**2+Math.cos(la1)*Math.cos(la2)*Math.sin(dn/2)**2;return R*2*Math.atan2(Math.sqrt(h),Math.sqrt(1-h))}

function maybeAlert(a){
  if(!a)return;
  if($("voiceToggle").checked&&a.call!==lastSpokenCall&&a.priority>=(state.settings.voice_score||120)){lastSpokenCall=a.call;speechSynthesis.cancel();speechSynthesis.speak(new SpeechSynthesisUtterance(`Rare DX detected. ${a.call}. ${a.entity_name}. ${a.reason}.`))}
  if(Notification.permission==="granted"&&a.call!==lastNotifiedCall&&a.priority>=(state.settings.notification_score||110)){lastNotifiedCall=a.call;new Notification(`DX target: ${a.call}`,{body:`${a.entity_name} · ${a.snr} dB · ${a.reason}`})}
}

async function sendCallCommand(call,button=null){
  if(!call)return;
  const original=button?.textContent||"";
  if(button){button.disabled=true;button.classList.add("sending");button.textContent=`CALLING ${call}…`}
  setText("commandStatus",`Sending ${call} to WSJT-X…`);
  try{
    const message=await postText(`/api/call/${encodeURIComponent(call)}`);
    setText("commandStatus",message);
    calledUntil.set(call,Date.now()+5*60*1000);
    if(button){
      button.classList.remove("sending");
      button.classList.add("sent");
      button.textContent="✓ CALL SENT";
    }
    // Immediately advance the advisor and queue to the next target.
    window.saveCheckboxState();
  if(state)render(state);
    setTimeout(()=>{
      if(button){button.classList.remove("sent");button.disabled=false;button.textContent=original}
    },1400);
  }catch(e){
    setText("commandStatus",e.message);
    if(button){button.classList.remove("sending");button.disabled=false;button.textContent=original}
  }
}
async function callBest(){
  const c=advisorOverride?.call;
  if(!c)return;
  await sendCallCommand(c,$("callButton"));
}
async function callTargetNow(call,button){
  await sendCallCommand(call,button);
}
window.callTargetNow=callTargetNow;
window.callBest=callBest;window.callSpecific=call=>sendCallCommand(call);
function focusStation(call){const r=state.recent.find(x=>x.call===call);if(!r?.latlon)return;document.querySelector('[data-view="map"]').click();setTimeout(()=>mainMap.setView(r.latlon,5),120)}
window.focusStation=focusStation;
async function postText(url){const r=await fetch(url,{method:"POST"}),t=await r.text();if(!r.ok)throw new Error(t);return t}
$("callButton").onclick=callBest;$("haltButton").onclick=async()=>{try{setText("commandStatus",await postText("/api/halt-tx"))}catch(e){setText("commandStatus",e.message)}};
$("wantedForm").onsubmit=async e=>{e.preventDefault();await fetch("/api/wanted",{method:"POST",body:new FormData(e.target)});e.target.reset()};window.removeWanted=async p=>fetch(`/api/wanted/${encodeURIComponent(p)}`,{method:"DELETE"});
$("adifForm").onsubmit=async e=>{e.preventDefault();const f=$("adifFile").files[0];if(!f)return;const d=new FormData();d.append("file",f);setText("importResult","Importing…");const r=await fetch("/api/import-adif",{method:"POST",body:d}),j=await r.json();setText("importResult",`Imported ${j.imported} records; ${j.confirmed} confirmed.`)};
$("activityFilter").oninput=()=>state&&renderActivity(state.recent);
$("clearCalledQueue").onclick=()=>{calledUntil.clear();if(state)render(state)};
$("showPaths").onchange=()=>state&&drawMap(mainMap,state.recent,mapMarkers,pathLayers,false);$("showNight").onchange=()=>state&&drawMap(mainMap,state.recent,mapMarkers,pathLayers,false);
$("fitMap").onclick=()=>{const pts=state.recent.filter(r=>r.latlon).map(r=>r.latlon);if(pts.length)mainMap.fitBounds(pts,{padding:[30,30]})};

document.querySelectorAll(".nav-btn").forEach(b=>b.onclick=()=>{
  document.querySelectorAll(".nav-btn").forEach(x=>x.classList.remove("active"));b.classList.add("active");document.querySelectorAll(".view").forEach(x=>x.classList.remove("active"));$(`view-${b.dataset.view}`).classList.add("active");
  if(b.dataset.view==="map")setTimeout(()=>mainMap.invalidateSize(),100);if(b.dataset.view==="dashboard")setTimeout(()=>dashMap.invalidateSize(),100);if(b.dataset.view==="propagation")loadPropagation();
});
$("propWindow").onchange=loadPropagation;
async function loadPropagation(){
  const mins=$("propWindow").value,data=await fetch(`/api/radar?minutes=${mins}`).then(r=>r.json()),rad=data.radar||[],bands=data.bands||[];
  setText("propEntities",rad.length);setText("propStations",rad.reduce((a,b)=>a+b.stations,0));const total=rad.reduce((a,b)=>a+b.decodes,0)||1;
  setText("propSnr",(rad.reduce((a,b)=>a+(b.avg_snr||0)*b.decodes,0)/total).toFixed(1)+" dB");setText("propDistance",dist(rad.reduce((a,b)=>a+(b.avg_distance||0)*b.decodes,0)/total));setText("propRegion",state?.propagation?.opening||"--");
  renderRadar(rad);renderBandPerformance(bands);renderPropChart(data.history||[]);
}
function renderBandPerformance(bands){const max=Math.max(1,...bands.map(b=>b.stations));$("bandPerformance").innerHTML=bands.map(b=>`<div class="band-perf-row"><strong>${esc(b.band)}</strong><div class="track"><i style="width:${b.stations/max*100}%"></i></div><span>${b.stations} stations</span><span>${b.entities} entities</span><span>${b.avg_snr} dB</span><span>${dist(b.avg_distance)}</span></div>`).join("")}
function renderPropChart(rows){
  const labels=[...new Set(rows.map(r=>r.bucket))],regions=[...new Set(rows.map(r=>r.continent))],datasets=regions.map(region=>({label:region,data:labels.map(l=>rows.find(r=>r.bucket===l&&r.continent===region)?.stations||0),tension:.3}));
  if(propChart)propChart.destroy();propChart=new Chart($("propChart"),{type:"line",data:{labels,datasets},options:{responsive:true,plugins:{legend:{labels:{color:"#aac6cf"}}},scales:{x:{ticks:{color:"#7899a5"}},y:{ticks:{color:"#7899a5"},beginAtZero:true}}}});
}
$("loadHistory").onclick=loadHistory;
async function loadHistory(){const q=encodeURIComponent($("historyQuery").value),b=encodeURIComponent($("historyBand").value);const rows=await fetch(`/api/history?limit=1000&query=${q}&band=${b}`).then(r=>r.json());$("historyRows").innerHTML=rows.map(r=>`<tr><td>${new Date(r.heard_at).toISOString().replace("T"," ").substring(0,19)}</td><td>${esc(r.call)}</td><td>${r.flag||""} ${esc(r.entity_name||"")}</td><td>${r.snr}</td><td>${esc(r.grid||"")}</td><td>${esc(r.band||"")}</td><td>${r.priority}</td><td>${esc(r.message)}</td></tr>`).join("")}
$("settingsForm").onsubmit=async e=>{e.preventDefault();const r=await fetch("/api/settings",{method:"POST",body:new FormData(e.target)});setText("settingsResult",r.ok?"Settings saved.":"Could not save settings.")};
$("checkTime").onclick=async()=>{
  setText("timeSyncMessage","Checking NTP server…");
  try{await fetch("/api/time/check",{method:"POST"})}catch(e){setText("timeSyncMessage","Time check failed")}
};
$("syncTime").onclick=async()=>{
  setText("timeSyncMessage","Opening the separate Administrator clock helper…");
  $("syncTime").disabled=true;
  try{
    const r=await fetch("/api/time/sync",{method:"POST"}),text=await r.text();
    setText("timeSyncMessage",text);
  }catch(e){setText("timeSyncMessage","Time sync request failed")}
  finally{$("syncTime").disabled=false}
};
document.querySelectorAll("[data-preset]").forEach(button=>button.onclick=()=>{
  const preset=button.dataset.preset;
  const set=(id,value)=>{const n=$(id);if(n)n.checked=value};
  if(preset==="new"){
    set("optBest",true);set("optFar",false);set("optStrong",false);set("optNew",true);set("optBand",false);set("optWanted",false);set("optPota",false);
  }else if(preset==="pota"){
    set("optBest",true);set("optFar",false);set("optStrong",false);set("optNew",false);set("optBand",false);set("optWanted",false);set("optPota",true);
  }else if(preset==="long"){
    set("optBest",true);set("optFar",true);set("optStrong",false);set("optNew",false);set("optBand",false);set("optWanted",false);set("optPota",false);
  }else if(preset==="outside-na"){
    document.querySelectorAll(".continent-filter").forEach(x=>x.checked=x.value!=="NA"&&x.value!=="UNK");
    set("optBest",true);set("optFar",false);set("optStrong",false);set("optNew",false);set("optBand",false);set("optWanted",false);set("optPota",false);
  }else if(preset==="strong"){
    set("optBest",false);set("optFar",false);set("optStrong",true);set("optNew",false);set("optBand",false);set("optWanted",false);set("optPota",false);
  }else{
    set("optBest",true);set("optFar",false);set("optStrong",false);set("optNew",false);set("optBand",false);set("optWanted",false);set("optPota",false);
    document.querySelectorAll(".continent-filter").forEach(x=>x.checked=x.value!=="UNK");
  }
  window.saveCheckboxState();
  if(state)render(state);
});
$("enableNotifications").onclick=async()=>{const p=await Notification.requestPermission();setText("settingsResult",`Desktop notifications: ${p}`)};

function updateClock(){const n=new Date();setText("utcClock",n.toISOString().substring(11,19));let p=Number(state?.status?.tr_period)||15;if(p>1000)p/=1000;if(p<=0||p>120)p=15;const sec=n.getTime()/1000%p,rem=p-sec;setText("cycleRemaining",`${rem.toFixed(1)} s`);$("cycleBar").style.width=`${sec/p*100}%`}
setInterval(updateClock,100);
function connect(){const proto=location.protocol==="https:"?"wss":"ws";socket=new WebSocket(`${proto}://${location.host}/ws`);socket.onmessage=e=>render(JSON.parse(e.data));socket.onclose=()=>setTimeout(connect,1500);socket.onerror=()=>socket.close()}

const checkboxIds = ['optBest','optFar','optStrong','optNew','optBand','optWanted','optPota','voiceToggle'];

// Load state
checkboxIds.forEach(id => {
    let saved = localStorage.getItem('wsjtx_' + id);
    if (saved !== null && $(id)) $(id).checked = saved === 'true';
});
document.querySelectorAll('.continent-filter').forEach(x => {
    let saved = localStorage.getItem('wsjtx_cont_' + x.value);
    if (saved !== null) x.checked = saved === 'true';
});

// Save state function
window.saveCheckboxState = () => {
    checkboxIds.forEach(id => {
        if ($(id)) localStorage.setItem('wsjtx_' + id, $(id).checked);
    });
    document.querySelectorAll('.continent-filter').forEach(x => {
        localStorage.setItem('wsjtx_cont_' + x.value, x.checked);
    });
};

['optBest','optFar','optStrong','optNew','optBand','optWanted','optPota','voiceToggle'].forEach(id=>$(id)?.addEventListener('change',()=>{window.saveCheckboxState();state&&render(state)}));document.querySelectorAll('.continent-filter').forEach(x=>x.addEventListener('change',()=>{window.saveCheckboxState();state&&render(state)}));
$("pskRefresh").onclick=async()=>{
  const m=$("pskTimeframe")?.value||60;
  setText("pskStatus","Refreshing...");
  try{await fetch(`/api/psk-refresh?minutes=${m}`,{method:"POST"})}catch(e){setText("pskStatus","Refresh failed")}
};
$("pskTimeframe").onchange=async(e)=>{
  const fd = new FormData();
  fd.append("psk_timeframe_minutes", e.target.value);
  await fetch("/api/settings",{method:"POST",body:fd});
};
setupMaps();connect();fetch("/api/status").then(r=>r.json()).then(render);

setInterval(() => {
  if(!$('pskRefresh') || !window.lastPskRefresh) return;
  const passed = (Date.now() - window.lastPskRefresh) / 1000;
  const left = 300 - passed; // 5 minute cooldown
  const countSpan = $('pskCountdown');
  if(left > 0) {
    $('pskRefresh').disabled = true;
    const m = Math.floor(left / 60);
    const s = Math.floor(left % 60).toString().padStart(2, '0');
    $('pskRefresh').textContent = `Refresh now`;
    if (countSpan) countSpan.textContent = `Safe to refresh in: ${m}:${s}`;
  } else {
    $('pskRefresh').disabled = false;
    $('pskRefresh').textContent = `Refresh now`;
    if (countSpan) countSpan.textContent = `Safe to refresh`;
  }
}, 1000);


// ----- Native browser waterfall (public v1.1 controls/zoom/persistence) -----
let wfSocket=null,wfCtx=null,wfOverlayCtx=null,wfLastRow=null,wfPeakRow=null;
let wfSelectedHz=1500,wfSelectedDecode=null,wfGL=null,wfGLState=null,wfTracks=[];
let wfLastLabelSignature="",wfAutoFloorTimer=0,wfPreviousIntensity=null,wfLatestIntensity=null;
let wfViewCenter=0.5,wfRowAccumulator=0,wfDragging=false,wfDragStartX=0,wfDragStartCenter=0;

const WF_SETTINGS_KEY="wsjtx-operator-console.waterfall.v1";
const WF_DEFAULTS={
  speed:65,zoom:1,center:0.5,followRx:false,palette:"classic",
  floor:-120,autoFloor:true,ceiling:-45,average:0.55,peak:0.82,drive:0
};

function wfLoadPreferences(){
  let saved={};
  try{saved=JSON.parse(localStorage.getItem(WF_SETTINGS_KEY)||"{}")}catch{}
  const p={...WF_DEFAULTS,...saved};
  $("waterfallSpeed").value=p.speed;
  $("waterfallZoom").value=p.zoom;
  $("waterfallFollowRx").checked=Boolean(p.followRx);
  $("waterfallPalette").value=p.palette;
  $("waterfallFloor").value=p.floor;
  $("waterfallAutoFloor").checked=Boolean(p.autoFloor);
  $("waterfallCeiling").value=p.ceiling;
  $("waterfallAverage").value=p.average;
  $("waterfallPeakHold").value=p.peak;
  $("waterfallDrive").value=p.drive;
  wfViewCenter=Math.max(0,Math.min(1,Number(p.center)||.5));
}
function wfSavePreferences(){
  const p={
    speed:Number($("waterfallSpeed").value),
    zoom:Number($("waterfallZoom").value),
    center:wfViewCenter,
    followRx:$("waterfallFollowRx").checked,
    palette:$("waterfallPalette").value,
    floor:Number($("waterfallFloor").value),
    autoFloor:$("waterfallAutoFloor").checked,
    ceiling:Number($("waterfallCeiling").value),
    average:Number($("waterfallAverage").value),
    peak:Number($("waterfallPeakHold").value),
    drive:Number($("waterfallDrive").value)
  };
  try{localStorage.setItem(WF_SETTINGS_KEY,JSON.stringify(p))}catch{}
}
function wfZoom(){return Math.max(1,Math.min(8,Number($("waterfallZoom")?.value||1)))}
function wfViewSpan(){return 1/wfZoom()}
function wfClampCenter(center=wfViewCenter){
  const half=wfViewSpan()/2;
  if(half>=0.5)return 0.5;
  return Math.max(half,Math.min(1-half,center));
}
function wfViewStart(){return wfClampCenter()-wfViewSpan()/2}
function wfVisibleMinHz(){return wfViewStart()*5000}
function wfVisibleMaxHz(){return (wfViewStart()+wfViewSpan())*5000}
function wfHzToX(hz,width){
  return ((hz/5000-wfViewStart())/wfViewSpan())*width;
}
function wfPalette(v,name=$("waterfallPalette")?.value||"classic"){
  v=Math.max(0,Math.min(1,v));
  if(name==="gray"){const n=Math.round(v*255);return[n,n,n]}
  const stops=name==="contrast"?[
    [0,[0,0,0]],[.18,[0,0,80]],[.38,[0,190,255]],[.58,[0,255,80]],
    [.76,[255,255,0]],[.9,[255,40,0]],[1,[255,255,255]]
  ]:[
    [0,[0,2,8]],[.10,[0,8,32]],[.24,[0,38,105]],[.40,[0,123,190]],
    [.55,[0,210,205]],[.68,[42,225,105]],[.79,[218,235,42]],
    [.89,[255,150,15]],[1,[255,255,255]]
  ];
  for(let i=1;i<stops.length;i++)if(v<=stops[i][0]){
    const[a,ca]=stops[i-1],[b,cb]=stops[i],q=(v-a)/(b-a);
    return ca.map((n,j)=>Math.round(n+(cb[j]-n)*q));
  }
  return[255,255,255];
}
function setupWaterfallGL(canvas){
  const gl=canvas.getContext("webgl2",{alpha:false,antialias:false,preserveDrawingBuffer:false});
  if(!gl)return false;
  const vs=`#version 300 es
  in vec2 p;out vec2 uv;
  void main(){uv=(p+1.0)*0.5;gl_Position=vec4(p,0.0,1.0);}`;
  const fs=`#version 300 es
  precision mediump float;
  uniform sampler2D tex;
  uniform float rowOffset;
  uniform float viewStart;
  uniform float viewSpan;
  uniform int paletteMode;
  in vec2 uv;out vec4 outColor;
  vec3 classicPalette(float v){
    vec3 c0=vec3(0.0,0.008,0.03),c1=vec3(0.0,0.15,0.41);
    vec3 c2=vec3(0.0,0.82,0.78),c3=vec3(0.16,0.88,0.41);
    vec3 c4=vec3(0.86,0.92,0.16),c5=vec3(1.0,0.59,0.06),c6=vec3(1.0);
    if(v<.24)return mix(c0,c1,v/.24);
    if(v<.55)return mix(c1,c2,(v-.24)/.31);
    if(v<.68)return mix(c2,c3,(v-.55)/.13);
    if(v<.79)return mix(c3,c4,(v-.68)/.11);
    if(v<.89)return mix(c4,c5,(v-.79)/.10);
    return mix(c5,c6,(v-.89)/.11);
  }
  vec3 contrastPalette(float v){
    vec3 a=vec3(0.0),b=vec3(0.0,0.0,.32),c=vec3(0.0,.75,1.0);
    vec3 d=vec3(0.0,1.0,.30),e=vec3(1.0,1.0,0.0),f=vec3(1.0,.1,0.0);
    if(v<.18)return mix(a,b,v/.18);
    if(v<.38)return mix(b,c,(v-.18)/.20);
    if(v<.58)return mix(c,d,(v-.38)/.20);
    if(v<.76)return mix(d,e,(v-.58)/.18);
    if(v<.90)return mix(e,f,(v-.76)/.14);
    return mix(f,vec3(1.0),(v-.90)/.10);
  }
  void main(){
    float y=fract(rowOffset+uv.y);
    float x=viewStart+uv.x*viewSpan;
    if(x<0.0 || x>1.0){ outColor=vec4(0.0,0.0,0.0,1.0); return; }
    float v=texture(tex,vec2(x,y)).r;
    vec3 color=paletteMode==2?vec3(v):(paletteMode==1?contrastPalette(v):classicPalette(v));
    outColor=vec4(color,1.0);
  }`;
  function shader(type,source){
    const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);
    if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  try{
    const prog=gl.createProgram();
    gl.attachShader(prog,shader(gl.VERTEX_SHADER,vs));gl.attachShader(prog,shader(gl.FRAGMENT_SHADER,fs));gl.linkProgram(prog);
    if(!gl.getProgramParameter(prog,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(prog));
    const buf=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buf);
    gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW);
    gl.useProgram(prog);const loc=gl.getAttribLocation(prog,"p");
    gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,2,gl.FLOAT,false,0,0);
    const tex=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,tex);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.REPEAT);
    gl.texImage2D(gl.TEXTURE_2D,0,gl.R8,canvas.width,canvas.height,0,gl.RED,gl.UNSIGNED_BYTE,new Uint8Array(canvas.width*canvas.height));
    wfGL=gl;wfGLState={
      prog,tex,row:0,
      rowOffset:gl.getUniformLocation(prog,"rowOffset"),
      viewStart:gl.getUniformLocation(prog,"viewStart"),
      viewSpan:gl.getUniformLocation(prog,"viewSpan"),
      paletteMode:gl.getUniformLocation(prog,"paletteMode")
    };
    return true;
  }catch(e){console.warn("WebGL waterfall unavailable",e);return false}
}
function wfRenderGL(){
  if(!wfGL||!wfGLState)return false;
  const gl=wfGL,s=wfGLState,c=$("waterfallCanvas");
  gl.viewport(0,0,c.width,c.height);gl.useProgram(s.prog);
  gl.uniform1f(s.rowOffset,s.row/c.height);
  gl.uniform1f(s.viewStart,wfViewStart());
  gl.uniform1f(s.viewSpan,wfViewSpan());
  const mode=$("waterfallPalette").value==="gray"?2:($("waterfallPalette").value==="contrast"?1:0);
  gl.uniform1i(s.paletteMode,mode);
  gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
  return true;
}
function renderWaterfallGL(row,rowCount){
  if(!wfGL||!wfGLState)return false;
  const gl=wfGL,s=wfGLState,c=$("waterfallCanvas");
  gl.bindTexture(gl.TEXTURE_2D,s.tex);
  const previous=wfPreviousIntensity&&wfPreviousIntensity.length===row.length?wfPreviousIntensity:row;
  const count=Math.max(0,Math.min(4,rowCount));
  for(let step=1;step<=count;step++){
    const q=step/count,blended=new Uint8Array(row.length);
    for(let i=0;i<row.length;i++)blended[i]=Math.round(previous[i]+(row[i]-previous[i])*q);
    gl.texSubImage2D(gl.TEXTURE_2D,0,0,s.row,c.width,1,gl.RED,gl.UNSIGNED_BYTE,blended);
    s.row=(s.row+1)%c.height;
  }
  if(count)wfPreviousIntensity=row.slice();
  wfRenderGL();
  return true;
}
function wfUpdateLabels(){
  setText("waterfallSpeedValue",`${$("waterfallSpeed").value}%`);
  setText("waterfallZoomValue",`${Number($("waterfallZoom").value).toFixed(2).replace(/\.00$/,".0")}×`);
  setText("waterfallFloorValue",`${$("waterfallFloor").value} dB`);
  setText("waterfallCeilingValue",`${$("waterfallCeiling").value} dB`);
  setText("waterfallDriveValue",`${$("waterfallDrive").value > 0 ? "+" : ""}${$("waterfallDrive").value} dB`);
  setText("waterfallAverageValue",Number($("waterfallAverage").value).toFixed(2));
  setText("waterfallPeakValue",Number($("waterfallPeakHold").value).toFixed(2));
  setText("waterfallViewLabel",`${Math.round(wfVisibleMinHz())}–${Math.round(wfVisibleMaxHz())} Hz`);
}
function wfViewChanged(){
  wfViewCenter=wfClampCenter(wfViewCenter);
  wfLastLabelSignature="";
  wfUpdateLabels();wfSavePreferences();drawWaterfallScale();drawWaterfallMinimap();drawDecodeTicks();wfRenderGL();
}
function setupWaterfall(){
  const c=$("waterfallCanvas");if(!c)return;
  wfLoadPreferences();
  const hasGL=setupWaterfallGL(c);
  wfCtx=hasGL?null:c.getContext("2d",{alpha:false});
  const overlay=$("waterfallOverlay");wfOverlayCtx=overlay?overlay.getContext("2d"):null;
  if(wfCtx){wfCtx.fillStyle="#02070a";wfCtx.fillRect(0,0,c.width,c.height)}
  const persisted=["waterfallSpeed","waterfallPalette","waterfallFloor","waterfallAutoFloor","waterfallCeiling","waterfallAverage","waterfallPeakHold","waterfallDrive"];
  for(const id of persisted)$(id).addEventListener("input",()=>{wfUpdateLabels();wfSavePreferences();wfRenderGL();drawWaterfallMinimap()});
  $("waterfallZoom").addEventListener("input",wfViewChanged);
  $("waterfallFollowRx").addEventListener("change",()=>{wfSavePreferences();wfLastLabelSignature="";drawDecodeTicks()});
  $("waterfallResetView").onclick=()=>{$("waterfallZoom").value="1";$("waterfallFollowRx").checked=false;wfViewCenter=.5;wfViewChanged()};
  $("waterfallDetach").onclick=()=>window.open(`${location.origin}${location.pathname}?waterfall=detached`,"wsjtxWaterfall","width=1400,height=760,resizable=yes");
  wfUpdateLabels();drawWaterfallScale();drawWaterfallMinimap();
  window.addEventListener("resize",()=>{drawWaterfallScale();drawWaterfallMinimap()});
  loadAudioDevices();

  c.addEventListener("mousemove",e=>{
    wfMoveCursor(e);
    if(wfDragging){
      const r=c.getBoundingClientRect(),delta=(e.clientX-wfDragStartX)/r.width*wfViewSpan();
      wfViewCenter=wfClampCenter(wfDragStartCenter-delta);wfViewChanged();
    }
  });
  c.addEventListener("mousedown",e=>{
    if(e.button===0){
      wfDragging=true;
      wfDragStartX=e.clientX;
      wfDragStartCenter=wfViewCenter;
      c.parentElement.classList.add("dragging");
      if($("waterfallFollowRx").checked){
        $("waterfallFollowRx").checked=false;
        $("waterfallFollowRx").dispatchEvent(new Event("change"));
      }
    }
  });
  window.addEventListener("mouseup",()=>{wfDragging=false;c.parentElement.classList.remove("dragging")});
  c.addEventListener("mouseleave",()=>{
    $("waterfallCursor").style.display="none";$("waterfallCursorLabel").style.display="none";
  });
  
  c.addEventListener("click",e=>{if(Math.abs(e.clientX-wfDragStartX)<4)wfSelect(e,false)});
  c.addEventListener("dblclick",e=>wfSelect(e,true));
  $("waterfallMinimap").addEventListener("click",e=>{
    const r=e.currentTarget.getBoundingClientRect();
    wfViewCenter=wfClampCenter((e.clientX-r.left)/r.width);wfViewChanged();
  });
  $("refreshAudioDevices").onclick=loadAudioDevices;
  $("startWaterfall").onclick=startWaterfall;
  $("stopWaterfall").onclick=stopWaterfall;

  if(new URLSearchParams(location.search).get("waterfall")==="detached"){
    document.body.classList.add("waterfall-detached");
    document.title="Waterfall — WSJT-X Operator Console";
  }
}
async function loadAudioDevices(){
  const r=await fetch("/api/audio/devices"),j=await r.json(),sel=$("audioDevice");
  sel.innerHTML='<option value="-1">Default audio input</option>'+j.devices.map(d=>`<option value="${d.id}">${esc(d.name)} (${d.channels} ch)</option>`).join("");
  const savedDev = localStorage.getItem("wsjtx_audio_device");
  if(savedDev !== null) sel.value = savedDev;
  else if(state?.settings?.audio_device!=null)sel.value=String(state.settings.audio_device);
  sel.addEventListener("change", () => localStorage.setItem("wsjtx_audio_device", sel.value));
  if(!j.status.available)setText("waterfallStatus",j.status.error||"Audio support unavailable");
  else if(j.status.running){setText("waterfallStatus",`Capturing ${j.status.device_name} at ${j.status.sample_rate} Hz native rate`);connectWaterfallSocket()}
  setText("audioHealth",j.status.recovering?"Reconnecting audio…":`Drops ${j.status.dropped_blocks||0} · rejected ${j.status.rejected_frames||0}`);
}
async function startWaterfall(){
  const fd=new FormData();fd.set("device",$("audioDevice").value);fd.set("sample_rate","48000");fd.set("fft_size","4096");fd.set("fps","15");
  const r=await fetch("/api/audio/start",{method:"POST",body:fd});let j;try{j=await r.json()}catch{j={message:await r.text()}}
  if(!r.ok){setText("waterfallStatus",j.detail||j.message||"Could not start audio");return}
  setText("waterfallStatus",j.message);$("startWaterfall").classList.add("active");connectWaterfallSocket();
}
async function stopWaterfall(){
  await fetch("/api/audio/stop",{method:"POST"});
  if(wfSocket)wfSocket.close();wfSocket=null;
  setText("waterfallStatus","Waterfall stopped");$("startWaterfall").classList.remove("active");
}
function connectWaterfallSocket(){
  if(wfSocket&&wfSocket.readyState<2)return;
  const proto=location.protocol==="https:"?"wss":"ws";
  wfSocket=new WebSocket(`${proto}://${location.host}/ws/waterfall`);wfSocket.binaryType="arraybuffer";
  wfSocket.onmessage=e=>{
    if(e.data instanceof ArrayBuffer){const frame=parseWaterfallBinary(e.data);if(frame)drawWaterfallFrame(frame)}
    else{try{drawWaterfallFrame(JSON.parse(e.data))}catch{}}
  };
  wfSocket.onclose=()=>{wfSocket=null};
}
function parseWaterfallBinary(buffer){
  if(buffer.byteLength<20)return null;
  const view=new DataView(buffer);
  const magic=String.fromCharCode(view.getUint8(0),view.getUint8(1),view.getUint8(2),view.getUint8(3));
  if(magic!=="WF17")return null;
  const sequence=view.getUint32(4,true),level_db=view.getFloat32(8,true),suggested_floor=view.getFloat32(12,true);
  const count=view.getUint16(16,true),flags=view.getUint16(18,true);let offset=20;
  if(buffer.byteLength<offset+count*2)return null;
  const values=new Array(count);
  for(let i=0;i<count;i++)values[i]=view.getInt16(offset+i*2,true)/100;
  offset+=count*2;const peaks=[];
  if(buffer.byteLength>=offset+2){
    const peakCount=view.getUint16(offset,true);offset+=2;
    for(let i=0;i<peakCount&&buffer.byteLength>=offset+6;i++,offset+=6)peaks.push({
      hz:view.getUint16(offset,true),width_hz:view.getUint16(offset+2,true)/10,
      strength_db:view.getInt16(offset+4,true)/100
    });
  }
  return{sequence,level_db,suggested_floor,values_dbfs:values,peaks,flags,min_hz:0,max_hz:5000};
}
function updatePeakTracks(peaks){
  const now=performance.now(),next=[];
  for(const p of peaks||[]){
    let best=null,bestDiff=35;
    for(const track of wfTracks){const d=Math.abs(track.hz-p.hz);if(d<bestDiff){best=track;bestDiff=d}}
    if(best){
      best.hz=best.hz*.65+p.hz*.35;best.width_hz=best.width_hz*.6+p.width_hz*.4;
      best.strength_db=p.strength_db;best.last=now;best.age++;next.push(best);
    }else next.push({...p,last:now,age:1});
  }
  for(const track of wfTracks)if(now-track.last<900&&!next.includes(track))next.push(track);
  wfTracks=next.slice(0,48);
}
function drawWaterfallFrame(frame){
  const c=$("waterfallCanvas");if((!wfCtx&&!wfGL)||!c)return;
  setText("audioLevel",`${frame.level_db.toFixed(1)} dBFS`);
  updatePeakTracks(frame.peaks||[]);
  if($("waterfallFollowRx").checked){
    const rx=Number(state?.status?.rx_df);
    if(Number.isFinite(rx)&&rx>=0&&rx<=5000){
      const next=wfClampCenter(rx/5000);
      if(Math.abs(next-wfViewCenter)>.002){wfViewCenter=next;wfViewChanged()}
    }
  }
  const avg=Number($("waterfallAverage").value),peakDecay=Number($("waterfallPeakHold").value);
  let floor=Number($("waterfallFloor").value);
  const ceiling=Math.max(floor+10,Number($("waterfallCeiling").value));
  const vals=frame.values_dbfs||frame.values||[];if(!vals.length)return;
  if($("waterfallAutoFloor").checked&&Number.isFinite(frame.suggested_floor)){
    const now=performance.now();
    if(now-wfAutoFloorTimer>800){
      const target=Math.max(-140,Math.min(-70,frame.suggested_floor));
      floor=floor*.8+target*.2;$("waterfallFloor").value=String(Math.round(floor));
      wfUpdateLabels();wfSavePreferences();wfAutoFloorTimer=now;
    }
  }
  if(!wfLastRow||wfLastRow.length!==vals.length){wfLastRow=vals.slice();wfPeakRow=vals.slice()}
  const intensity=new Uint8Array(c.width);
  for(let x=0;x<c.width;x++){
    const source=x*(vals.length-1)/(c.width-1),lo=Math.floor(source),hi=Math.min(vals.length-1,lo+1),q=source-lo;
    const value=vals[lo]+(vals[hi]-vals[lo])*q;
    wfLastRow[lo]=wfLastRow[lo]*avg+value*(1-avg);
    wfPeakRow[lo]=Math.max(wfLastRow[lo],wfPeakRow[lo]*peakDecay+wfLastRow[lo]*(1-peakDecay));
    const drive = parseFloat($("waterfallDrive").value) || 0;
    const displayDb=Math.max(wfLastRow[lo],wfPeakRow[lo]-2.5) + drive;
    intensity[x]=Math.max(0,Math.min(255,Math.round((displayDb-floor)/(ceiling-floor)*255)));
  }
  wfLatestIntensity=intensity;
  wfRowAccumulator+=Number($("waterfallSpeed").value)/100;
  const rows=Math.min(4,Math.floor(wfRowAccumulator));
  if(rows>0)wfRowAccumulator-=rows;
  if(!renderWaterfallGL(intensity,rows)&&rows>0){
    const span=wfViewSpan(),start=wfViewStart();
    for(let row=0;row<rows;row++){
      wfCtx.drawImage(c,0,0,c.width,c.height,0,1,c.width,c.height);
      const img=wfCtx.createImageData(c.width,1);
      for(let x=0;x<c.width;x++){
        const source=(start+x/c.width*span)*(intensity.length-1);
        if(source<0 || source>intensity.length-1){
            const p=x*4; img.data[p]=0;img.data[p+1]=0;img.data[p+2]=0;img.data[p+3]=255;
            continue;
        }
        const lo=Math.floor(source),hi=Math.min(intensity.length-1,lo+1),q=source-lo;
        const value=(intensity[lo]*(1-q)+intensity[hi]*q)/255,[r,g,b]=wfPalette(value),p=x*4;
        img.data[p]=r;img.data[p+1]=g;img.data[p+2]=b;img.data[p+3]=255;
      }
      wfCtx.putImageData(img,0,0);
    }
  }
  drawWaterfallMinimap();drawDecodeTicks();
}
function drawDecodeTicks(){
  if(!wfOverlayCtx)return;
  const c=$("waterfallOverlay"),min=wfVisibleMinHz(),max=wfVisibleMaxHz();
  const recent=(state?.recent||[]).slice(0,50);
  const signature=[
    Math.round(wfSelectedHz),Math.round(min),Math.round(max),
    wfTracks.map(track=>`${Math.round(track.hz)}:${Math.round(track.width_hz)}`).join(","),
    recent.map(row=>`${row.call}:${row.delta_frequency??row.df??0}`).join(",")
  ].join("|");
  if(signature===wfLastLabelSignature)return;wfLastLabelSignature=signature;
  wfOverlayCtx.clearRect(0,0,c.width,c.height);wfOverlayCtx.save();
  for(const track of wfTracks){
    if(track.age<2||track.hz<min||track.hz>max)continue;
    const x=wfHzToX(track.hz,c.width),w=Math.max(2,track.width_hz/(max-min)*c.width);
    wfOverlayCtx.fillStyle="rgba(70,215,255,.10)";wfOverlayCtx.fillRect(x-w/2,45,w,Math.max(0,c.height-45));
    wfOverlayCtx.strokeStyle=track.width_hz>90?"rgba(255,120,65,.75)":"rgba(70,215,255,.38)";
    wfOverlayCtx.beginPath();wfOverlayCtx.moveTo(x,38);wfOverlayCtx.lineTo(x,46);wfOverlayCtx.stroke();
    if(track.width_hz>90){wfOverlayCtx.fillStyle="#ff9c70";wfOverlayCtx.font="9px sans-serif";wfOverlayCtx.fillText(`${Math.round(track.width_hz)} Hz`,x+3,29)}
  }
  if(Number.isFinite(wfSelectedHz)&&wfSelectedHz>=min&&wfSelectedHz<=max){
    const sx=wfHzToX(wfSelectedHz,c.width);
    wfOverlayCtx.strokeStyle="rgba(80,210,255,.95)";wfOverlayCtx.lineWidth=1;
    wfOverlayCtx.beginPath();wfOverlayCtx.moveTo(sx,0);wfOverlayCtx.lineTo(sx,c.height);wfOverlayCtx.stroke();
    wfOverlayCtx.fillStyle="rgba(6,20,28,.88)";wfOverlayCtx.fillRect(Math.min(sx+4,c.width-66),c.height-22,62,17);
    wfOverlayCtx.fillStyle="#8fe9ff";wfOverlayCtx.font="11px sans-serif";
    wfOverlayCtx.fillText(`${Math.round(wfSelectedHz)} Hz`,Math.min(sx+8,c.width-62),c.height-19);
  }
  wfOverlayCtx.font="12px sans-serif";wfOverlayCtx.textBaseline="top";
  const candidates=[],seen=new Set();
  for(const row of recent){
    const hz=Number(row.delta_frequency??row.df??0);
    if(hz<min||hz>max||!row.call||seen.has(row.call))continue;
    seen.add(row.call);const flag=row.flag?`${row.flag} `:"";
    const entity=(row.entity_name||"").split(" ")[0].slice(0,8);
    candidates.push({r:row,hz,x:wfHzToX(hz,c.width),label:`${flag}${row.call}`,entity});
  }
  candidates.sort((a,b)=>a.x-b.x);const rowRight=[-999,-999,-999];
  for(const item of candidates){
    const{r,x,label,entity}=item;
    const width=Math.max(wfOverlayCtx.measureText(label).width,wfOverlayCtx.measureText(entity).width)+10;
    let labelRow=-1;for(let i=0;i<rowRight.length;i++)if(x>rowRight[i]+4){labelRow=i;break}
    if(labelRow<0)continue;
    const color=r.wanted?"#ff5e6c":r.needed_on_band?"#ffd166":"#eaf7ff",y=2+labelRow*19;
    wfOverlayCtx.fillStyle="rgba(2,10,15,.78)";wfOverlayCtx.fillRect(x+2,y-1,width,18);
    wfOverlayCtx.strokeStyle=color;wfOverlayCtx.beginPath();wfOverlayCtx.moveTo(x,0);wfOverlayCtx.lineTo(x,y+16);wfOverlayCtx.stroke();
    wfOverlayCtx.fillStyle=color;wfOverlayCtx.font="11px sans-serif";wfOverlayCtx.fillText(label,x+5,y);
    if(entity){wfOverlayCtx.fillStyle="#8ba5b0";wfOverlayCtx.font="8px sans-serif";wfOverlayCtx.fillText(entity,x+5,y+10)}
    rowRight[labelRow]=x+width;
  }
  wfOverlayCtx.restore();
}
function drawWaterfallScale(){
  const c=$("waterfallScale");if(!c)return;const ctx=c.getContext("2d");
  ctx.clearRect(0,0,c.width,c.height);ctx.strokeStyle="#486673";ctx.fillStyle="#8ca8b4";ctx.font="10px sans-serif";ctx.textBaseline="top";
  const min=wfVisibleMinHz(),max=wfVisibleMaxHz(),range=max-min;
  const rawStep=range/8,steps=[10,20,25,50,100,200,250,500,1000];
  const majorStep=steps.find(step=>step>=rawStep)||1000,minorStep=majorStep/5;
  const first=Math.ceil(min/minorStep)*minorStep;
  for(let hz=first;hz<=max+.001;hz+=minorStep){
    const x=(hz-min)/range*c.width,major=Math.abs(hz/majorStep-Math.round(hz/majorStep))<.001;
    ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,major?11:6);ctx.stroke();
    if(major){
      const label=`${Math.round(hz)}${hz===max?" Hz":""}`,w=ctx.measureText(label).width;
      ctx.fillText(label,Math.max(0,Math.min(c.width-w,x-w/2)),14);
    }
  }
}
function drawWaterfallMinimap(){
  const c=$("waterfallMinimap");if(!c)return;const ctx=c.getContext("2d"),w=c.width,h=c.height;
  ctx.clearRect(0,0,w,h);ctx.fillStyle="#02070a";ctx.fillRect(0,0,w,h);
  if(wfLatestIntensity){
    for(let x=0;x<w;x++){
      const i=Math.min(wfLatestIntensity.length-1,Math.floor(x/w*wfLatestIntensity.length));
      const[r,g,b]=wfPalette(wfLatestIntensity[i]/255);ctx.fillStyle=`rgb(${r},${g},${b})`;ctx.fillRect(x,0,1,h);
    }
  }
  const x=wfViewStart()*w,vw=wfViewSpan()*w;
  ctx.fillStyle="rgba(0,0,0,.38)";ctx.fillRect(0,0,x,h);ctx.fillRect(x+vw,0,w-(x+vw),h);
  ctx.strokeStyle="#8fe9ff";ctx.lineWidth=2;ctx.strokeRect(x+1,1,Math.max(1,vw-2),h-2);
}
function wfHzFromEvent(e){
  const r=$("waterfallCanvas").getBoundingClientRect();
  return Math.max(0,Math.min(5000,(wfViewStart()+(e.clientX-r.left)/r.width*wfViewSpan())*5000));
}
function wfMoveCursor(e){
  const wrap=$("waterfallCanvas").getBoundingClientRect(),x=e.clientX-wrap.left,hz=wfHzFromEvent(e);
  const cur=$("waterfallCursor"),lab=$("waterfallCursorLabel");
  cur.style.display=lab.style.display="block";cur.style.left=`${x}px`;
  lab.style.left=`${Math.min(x+7,wrap.width-70)}px`;lab.textContent=`${Math.round(hz)} Hz`;
}
function nearestDecode(hz){
  let best=null,diff=1e9;
  for(const row of state?.recent||[]){
    const f=Number(row.delta_frequency??row.df??-9999),d=Math.abs(f-hz);
    if(f>=0&&d<diff){best=row;diff=d}
  }
  return diff<=100?best:null;
}
function wfSelect(e,callNow){
  wfSelectedHz=wfHzFromEvent(e);wfSelectedDecode=nearestDecode(wfSelectedHz);
  if(wfSelectedDecode){
    setText("waterfallSelection",`${wfSelectedDecode.call} · ${Math.round(wfSelectedHz)} Hz · ${wfSelectedDecode.snr} dB · ${wfSelectedDecode.entity_name||""}`);
    if(callNow)callSpecific(wfSelectedDecode.call);
  }else setText("waterfallSelection",`${Math.round(wfSelectedHz)} Hz selected — no recent decode within 100 Hz`);
  wfLastLabelSignature="";drawDecodeTicks();
}
window.addEventListener("load",()=>{
  setupWaterfall();
  const b=$("openSettingsFirstRun");if(b)b.onclick=()=>document.querySelector('[data-view="settings"]')?.click();
  $("verifyLotwPath").onclick=async()=>{
  const path=$("settingsLotwPath").value;
  setText("lotwVerifyResult","Verifying...");
  const fd=new FormData();fd.append("path",path);
  const r=await fetch("/api/settings/verify_tqsl",{method:"POST",body:fd}),data=await r.json();
  setText("lotwVerifyResult",data.message);
  $("lotwVerifyResult").style.color=data.ok?"var(--accent)":"red";
};
$("openLotwHelp").onclick=()=>$("lotwHelpDialog").showModal();
document.querySelectorAll(".close-btn").forEach(b=>b.onclick=e=>{const d=e.target.closest("dialog");if(d)d.close();});
$("aboutButton").onclick=()=>$("aboutDialog").showModal();
  const about=$("aboutDialog"),open=$("aboutButton"),close=$("aboutClose");
  if(open)open.onclick=()=>about.showModal();
  if(close)close.onclick=()=>about.close();
  if(about)about.addEventListener("click",e=>{if(e.target===about)about.close()});
});



// --- POTA Integration ---
window.potaCache = {};

async function fetchPotaSpots() {
    try {
        const response = await fetch('https://api.pota.app/spot/activator');
        if (response.ok) {
            const data = await response.json();
            const newCache = {};
            const rawSpots = [];
            const arr = Array.isArray(data) ? data : (data && Array.isArray(data.data) ? data.data : []);
            arr.forEach(spot => {
                if (spot.activator && spot.reference) {
                    newCache[spot.activator.toUpperCase()] = spot.reference;
                    const mode = (spot.mode || "").toUpperCase();
                    if (mode.includes("FT8") || mode.includes("FT4")) {
                        rawSpots.push(spot);
                    }
                }
            });
            window.potaCache = newCache;
            window.potaSpotsRaw = rawSpots;
            if (state) renderPotaTab(rawSpots);
        }
    } catch (e) {
        console.error("Error fetching POTA spots:", e);
    }
}

// Initial fetch and then every 2 minutes
fetchPotaSpots();
setInterval(fetchPotaSpots, 120000);

window.getPotaTagHTML = function(call, reason) {
    if (!call) return "";
    call = call.toUpperCase();
    
    // Method B: Check API cache
    if (window.potaCache[call]) {
        return `<span class="tag pota">POTA ${window.potaCache[call]}</span>`;
    }
    
    // Method A: Check reason/message text for CQ POTA
    if (reason && /CQ POTA/i.test(reason)) {
        return `<span class="tag pota">POTA</span>`;
    }
    
    return "";
};

window.addEventListener("beforeunload", () => { if (socket) { socket.onclose = null; socket.close(); } if (typeof wfSocket !== "undefined" && wfSocket) { wfSocket.onclose = null; wfSocket.close(); } });

function renderPotaTab(spots) {
    const tbody = document.getElementById("potaSpotsTable");
    if (!tbody) return;
    if (!spots || spots.length === 0) {
        tbody.innerHTML = "<tr><td colspan='7' class='muted'>No FT8/FT4 POTA spots currently active.</td></tr>";
        return;
    }
    
    // Score and sort spots
    const scored = spots.map(spot => {
        let score = 0;
        let band = "";
        let freq = parseFloat(spot.frequency) * 1000 || 0;
        if (freq >= 7000000 && freq <= 7300000) band = "40m";
        else if (freq >= 14000000 && freq <= 14350000) band = "20m";
        else if (freq >= 10100000 && freq <= 10150000) band = "30m";
        else if (freq >= 18068000 && freq <= 18168000) band = "17m";
        else if (freq >= 21000000 && freq <= 21450000) band = "15m";
        else if (freq >= 24890000 && freq <= 24990000) band = "12m";
        else if (freq >= 28000000 && freq <= 29700000) band = "10m";
        else if (freq >= 3500000 && freq <= 4000000) band = "80m";
        
        if (state && state.band_summary) {
            const summary = state.band_summary.find(b => b.band === band);
            if (summary && summary.decodes > 0) {
                score += 50; // Active band
                score += summary.stations * 2;
                score += summary.best_snr;
            }
        }
        return { ...spot, score };
    });
    
    scored.sort((a, b) => b.score - a.score);
    
    tbody.innerHTML = scored.map((spot, idx) => {
        const prob = spot.score > 60 ? "<span class='tag hot'>HIGH</span>" : (spot.score > 20 ? "<span class='tag'>MEDIUM</span>" : "<span class='tag new'>LOW</span>");
        return `<tr>
            <td><strong>${esc(spot.activator)}</strong></td>
            <td>${esc(spot.reference)}</td>
            <td>${esc(spot.frequency || "")}</td>
            <td>${esc(spot.mode || "")}</td>
            <td>${esc(spot.locationDesc || "")}</td>
            <td>${esc(spot.comments || "")}</td>
            <td>${prob}</td>
        </tr>`;
    }).join("");
}

