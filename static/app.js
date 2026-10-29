'use strict';
let csrf = '', selected = null, enrollmentDrawings = [], negativeEnrollment = null, activeChallenge = null;
const $=id=>document.getElementById(id);
const show=(id,yes)=>$(id).classList.toggle('hidden',!yes);
function notice(message,error=false){const e=$('notice');e.textContent=message;e.classList.toggle('err',error);show('notice',!!message);}
async function api(url,method='GET',body){
  const res=await fetch(url,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-D2S-CSRF':csrf},body:body?JSON.stringify(body):undefined});
  const out=await res.json().catch(()=>({error:'Unexpected server response'}));
  if(!res.ok)throw Error(out.error||`HTTP ${res.status}`);
  return out;
}
const onError=fn=>async()=>{try{await fn()}catch(e){notice(e.message,true)}};
function canvasController(id,countId){
 const canvas=$(id),ctx=canvas.getContext('2d');let strokes=[],current=null,pid=null;
 function redraw(){ctx.fillStyle='#f6f6f3';ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.strokeStyle='#e2e4e6';ctx.lineWidth=.8;
  for(let i=60;i<canvas.width;i+=60){ctx.beginPath();ctx.moveTo(i,0);ctx.lineTo(i,canvas.height);ctx.stroke()}
  for(let i=60;i<canvas.height;i+=60){ctx.beginPath();ctx.moveTo(0,i);ctx.lineTo(canvas.width,i);ctx.stroke()}
  for(const st of [...strokes,...(current?[current]:[])]){if(st.length<2)continue;ctx.beginPath();ctx.moveTo(st[0][0],st[0][1]);for(const p of st.slice(1))ctx.lineTo(p[0],p[1]);ctx.strokeStyle='#283746';ctx.lineWidth=4.5;ctx.lineJoin='round';ctx.lineCap='round';ctx.stroke()}
  $(countId).textContent=`${strokes.length} strokes`;
 }
 function pos(e){const r=canvas.getBoundingClientRect();return [Math.round((e.clientX-r.left)*canvas.width/r.width*100)/100,Math.round((e.clientY-r.top)*canvas.height/r.height*100)/100]}
 canvas.addEventListener('pointerdown',e=>{if(current)return;e.preventDefault();pid=e.pointerId;canvas.setPointerCapture(pid);current=[pos(e)];redraw()});
 canvas.addEventListener('pointermove',e=>{if(!current||e.pointerId!==pid)return;e.preventDefault();const p=pos(e),prev=current[current.length-1];if(Math.hypot(p[0]-prev[0],p[1]-prev[1])>1.5&&current.length<800){current.push(p);redraw()}});
 function finish(e){if(!current||pid!==e.pointerId)return;e.preventDefault();if(current.length>=2)strokes.push(current);current=null;pid=null;redraw()}
 canvas.addEventListener('pointerup',finish);canvas.addEventListener('pointercancel',finish);
 redraw();return {get:()=>structuredClone(strokes),clear:()=>{strokes=[];current=null;redraw()},undo:()=>{strokes.pop();redraw()}};
}
const enrollCanvas=canvasController('enrollCanvas','enrollStrokes');
const verifyCanvas=canvasController('verifyCanvas','verifyStrokes');
const negativeCanvas=canvasController('negativeCanvas','negativeStrokes');
const challengeCanvas=canvasController('challengeCanvas','challengeStrokes');
async function dashboard(){
 const info=await api('/api/session');csrf=info.csrf;
 $('greeting').textContent=info.has_profile?`Your gestures · ${info.participant_id}`:'Enroll your first gesture';
 show('withdrawPanel',info.has_profile);
 const list=(await api('/api/enrollments')).enrollments;
 $('challengeStudy').disabled=!list.length;
 const host=$('enrollmentCards');host.replaceChildren();
 if(!list.length){const p=document.createElement('div');p.className='empty';p.textContent='Start below: draw your chosen gesture three times. No registration is needed.';host.append(p);
   show('enrollPanel',true);show('verifyPanel',false);show('forbiddenPanel',false);show('challengePanel',false);
 }
 for(const e of list){const card=document.createElement('div');card.className='card';const kicker=document.createElement('div');kicker.className='kicker';kicker.textContent='ENROLLED GESTURE';const h=document.createElement('h3');h.textContent=e.label;
 const stats=document.createElement('div');stats.className='stats';stats.textContent=`Enrollment consistency: ${Math.round(e.stability*100)}% · ${e.attempt_count} attempts · ${e.gallery_count} gallery references`;
 const btn=document.createElement('button');btn.className='primary';btn.textContent='Test this gesture';btn.addEventListener('click',()=>openVerify(e));
 const manage=document.createElement('button');manage.className='textbutton';manage.textContent='Forbidden gestures / safety';manage.addEventListener('click',()=>openManage(e));
 const flag=document.createElement('p');flag.className='helper';flag.textContent=`${e.forbidden_count} forbidden variants · ${e.received_challenges} received challenges · Sharing: ${e.challenge_opt_in?'on':'off'}`;
 card.append(kicker,h,stats,flag,btn,manage);host.append(card)}
}
$('newEnrollment').addEventListener('click',()=>{enrollmentDrawings=[];enrollCanvas.clear();$('gestureLabel').value='';$('enrollProgress').textContent='Attempt 1 of 3';show('enrollPanel',true);show('verifyPanel',false);show('forbiddenPanel',false);show('challengePanel',false);$('enrollPanel').scrollIntoView({behavior:'smooth'})});
$('cancelEnroll').addEventListener('click',()=>show('enrollPanel',false));
$('enrollUndo').addEventListener('click',()=>enrollCanvas.undo());$('enrollClear').addEventListener('click',()=>enrollCanvas.clear());
$('enrollSave').addEventListener('click',onError(async()=>{
 const strokes=enrollCanvas.get();if(!strokes.length)throw Error('Draw something first.');
 if(!$('studyConsent').checked)throw Error('Please acknowledge the research consent first.');
 if(enrollmentDrawings.length===0&&!$('gestureLabel').value.trim())throw Error('Give the gesture a label.');
 // First two captures are staged only in this browser. The server validates all three.
 enrollmentDrawings.push(strokes);enrollCanvas.clear();
 if(enrollmentDrawings.length<3){$('enrollProgress').textContent=`Attempt ${enrollmentDrawings.length+1} of 3`;notice('Drawing saved. Repeat the same shape.');return}
 $('enrollSave').disabled=true;
 try{const response=await api('/api/enrollments','POST',{label:$('gestureLabel').value.trim(),consent:$('studyConsent').checked,attempts:enrollmentDrawings,device:('ontouchstart'in window?'touch':'pointer')});
   show('enrollPanel',false);enrollmentDrawings=[];await dashboard();const r=response.enrollment;notice(`Enrolled “${r.label}”. Consistency: ${Math.round(r.stability*100)}%. ${r.warnings.join(' ')}`);
 }catch(err){enrollmentDrawings.pop();$('enrollProgress').textContent='Attempt 3 of 3 — retry';throw err}finally{$('enrollSave').disabled=false}
}));
function openVerify(e){selected=e;verifyCanvas.clear();show('enrollPanel',false);show('forbiddenPanel',false);show('challengePanel',false);show('verifyPanel',true);show('verifyResponse',false);$('verifyTitle').textContent=`Redraw: ${e.label}`;history();$('verifyPanel').scrollIntoView({behavior:'smooth'})}
$('cancelVerify').addEventListener('click',()=>{selected=null;show('verifyPanel',false)});
$('verifyUndo').addEventListener('click',()=>verifyCanvas.undo());$('verifyClear').addEventListener('click',()=>verifyCanvas.clear());
async function history(){if(!selected)return;const rows=(await api(`/api/enrollments/${selected.id}/attempts`)).attempts;const tbody=$('attemptHistory');tbody.replaceChildren();for(const a of rows){const tr=document.createElement('tr');
 for(const s of [new Date(a.created_at).toLocaleString(),a.category,a.accepted?'Accepted':'Rejected',a.token_score.toFixed(3),a.geometry_score.toFixed(3)]){const td=document.createElement('td');td.textContent=s;tr.append(td)}tbody.append(tr)}
 if(!rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=5;td.textContent='No verification attempts yet.';tr.append(td);tbody.append(tr)}}
$('submitAttempt').addEventListener('click',onError(async()=>{if(!selected)throw Error('Select an enrollment.');const strokes=verifyCanvas.get();if(!strokes.length)throw Error('Draw first.');$('submitAttempt').disabled=true;
 try{const data=await api(`/api/enrollments/${selected.id}/attempts`,'POST',{strokes,category:$('attemptType').value,device:('ontouchstart'in window?'touch':'pointer')});
  const r=data.result;const el=$('verifyResponse');el.className=`results ${r.accepted?'ok':'fail'}`;
  el.textContent=`${r.accepted?'ACCEPTED':'REJECTED'}\nToken similarity: ${r.token_score.toFixed(3)} · Geometry: ${r.geometry_score.toFixed(3)}\nGallery rescue: ${r.gallery_rescue?'yes':'no'} · Gallery updated: ${r.gallery_updated?'yes':'no'} (${r.gallery_update_reason})\nUser-defined forbidden veto: ${r.user_negative_veto?'yes':'no'}\nCounterfactual margin: ${r.counterfactual_margin===null?'N/A':r.counterfactual_margin.toFixed(3)}${r.counterfactual_enforced?' (enforced)':' (diagnostic)'}\n${r.failure_reasons.length?'Reasons: '+r.failure_reasons.join(', '):''}`;
  show('verifyResponse',true);verifyCanvas.clear();notice('Attempt recorded.');await history();
 }finally{$('submitAttempt').disabled=false}
}));
(async()=>{try{await dashboard()}catch(e){notice(e.message,true)}})();

// User-defined negative references and enrollment boundary diagnostics.
async function openManage(e){
 negativeEnrollment=e;negativeCanvas.clear();$('negativeLabel').value='';show('verifyPanel',false);show('enrollPanel',false);show('challengePanel',false);show('forbiddenPanel',true);
 $('negativeTitle').textContent=`Forbidden variants: ${e.label}`;
 const reference=await api(`/api/enrollments/${e.id}/reference`);drawTarget(reference.strokes,'canonicalCanvas');
 $('shareChallenge').checked=Boolean(e.challenge_opt_in);
 $('negativeCounts').textContent=`${e.forbidden_count} manually marked negatives. The canonical drawing remains immutable.`;
 show('boundaryReport',false);await loadNegatives();$('forbiddenPanel').scrollIntoView({behavior:'smooth'});
}
async function loadNegatives(){
 if(!negativeEnrollment)return;
 const data=await api(`/api/enrollments/${negativeEnrollment.id}/forbidden`);
 const host=$('negativeList');host.replaceChildren();
 if(!data.forbidden.length){const p=document.createElement('p');p.className='helper';p.textContent='No forbidden variants yet. You can add up to five.';host.append(p)}
 for(const f of data.forbidden){
  const row=document.createElement('div');row.className='negative-item';const text=document.createElement('span');text.textContent=f.label;
  const button=document.createElement('button');button.className='linkbtn';button.textContent='Remove';button.addEventListener('click',onError(async()=>{
   if(!confirm(`Remove forbidden variant “${f.label}”?`))return;
   await api(`/api/enrollments/${negativeEnrollment.id}/forbidden/${f.id}`,'DELETE',{});await loadNegatives();await dashboard();
  }));row.append(text,button);host.append(row);
 }
}
$('closeForbidden').addEventListener('click',()=>show('forbiddenPanel',false));
$('negativeUndo').addEventListener('click',()=>negativeCanvas.undo());
$('negativeClear').addEventListener('click',()=>negativeCanvas.clear());
$('saveNegative').addEventListener('click',onError(async()=>{
 if(!negativeEnrollment)throw Error('Select an enrollment first.');
 const strokes=negativeCanvas.get(),label=$('negativeLabel').value.trim();
 if(!strokes.length||!label)throw Error('Draw and label a structurally incorrect variant.');
 $('saveNegative').disabled=true;
 try{
  const out=await api(`/api/enrollments/${negativeEnrollment.id}/forbidden`,'POST',{label,strokes});
  negativeCanvas.clear();$('negativeLabel').value='';notice(`Forbidden example saved. ${out.warning}`);
  await loadNegatives();await dashboard();
 }finally{$('saveNegative').disabled=false}
}));
$('shareChallenge').addEventListener('change',onError(async()=>{
 if(!negativeEnrollment)return;
 const enabled=$('shareChallenge').checked;
 try{
  if(enabled && !confirm('Other volunteers will be able to view your anonymous gesture and try copying it. You cannot retract images they have already seen. Opt in?')){
   $('shareChallenge').checked=false;return;
  }
  await api(`/api/enrollments/${negativeEnrollment.id}/challenge-sharing`,'POST',{enabled});
  negativeEnrollment.challenge_opt_in=enabled;notice(enabled?'This gesture is now an anonymous opt-in challenge target.':'Challenge sharing disabled for this gesture.');await dashboard();
 }catch(e){$('shareChallenge').checked=!enabled;throw e}
}));
$('runBoundary').addEventListener('click',onError(async()=>{
 if(!negativeEnrollment)return;
 $('runBoundary').disabled=true;
 try{
  const r=(await api(`/api/enrollments/${negativeEnrollment.id}/boundary`,'POST',{})).report;
  const box=$('boundaryReport');box.className='results';
  box.textContent=`Bounded boundary probe (cached snapshot)\n${r.probes_evaluated} generated valid probes · ${r.topology_changed_probes} detected structural changes\n${r.accepted_topology_changed} topology-changing probes accepted\nMinimum normalized coordinate displacement among accepted changes: ${r.smallest_accepted_normalized_displacement??'Not observed'}\n${r.note}`;
  show('boundaryReport',true);
 }finally{$('runBoundary').disabled=false}
}));

// Adversarial research challenges: owner identity and match scores are withheld.
function drawTarget(strokes,canvasId='targetCanvas'){
 const canvas=$(canvasId),ctx=canvas.getContext('2d');ctx.fillStyle='#f6f6f3';ctx.fillRect(0,0,canvas.width,canvas.height);
 ctx.lineWidth=4.5;ctx.lineCap='round';ctx.lineJoin='round';ctx.strokeStyle='#293b4f';
 for(const st of strokes||[]){if(st.length<2)continue;ctx.beginPath();ctx.moveTo(st[0][0],st[0][1]);for(const p of st.slice(1))ctx.lineTo(p[0],p[1]);ctx.stroke();}
}
async function refreshChallenge(){
 const d=await api('/api/challenges');activeChallenge=d.active;
 $('challengeStats').textContent=`Your completed adversarial submissions: ${d.submitted_count}. Maximum 12 new assignments per 24 hours.`;
 show('challengeActive',!!activeChallenge);show('targetPreview',Boolean(activeChallenge?.target_strokes));
 $('assignChallenge').disabled=!!activeChallenge;
 $('challengeMode').disabled=!!activeChallenge;
 if(activeChallenge){$('challengeInstructions').textContent=`${activeChallenge.instructions} Expires: ${new Date(activeChallenge.expires_at).toLocaleTimeString()}`;if(activeChallenge.target_strokes)drawTarget(activeChallenge.target_strokes)}
}
$('challengeStudy').addEventListener('click',onError(async()=>{
 show('forbiddenPanel',false);show('enrollPanel',false);show('verifyPanel',false);show('challengePanel',true);show('challengeResponse',false);challengeCanvas.clear();
 await refreshChallenge();$('challengePanel').scrollIntoView({behavior:'smooth'});
}));
$('closeChallenge').addEventListener('click',()=>show('challengePanel',false));
$('assignChallenge').addEventListener('click',onError(async()=>{
 $('assignChallenge').disabled=true;
 try{const r=await api('/api/challenges/assign','POST',{mode:$('challengeMode').value});
  activeChallenge=r.challenge;challengeCanvas.clear();show('challengeResponse',false);await refreshChallenge();
 }finally{if(!activeChallenge)$('assignChallenge').disabled=false}
}));
$('challengeUndo').addEventListener('click',()=>challengeCanvas.undo());
$('challengeClear').addEventListener('click',()=>challengeCanvas.clear());
$('challengeSubmit').addEventListener('click',onError(async()=>{
 if(!activeChallenge)throw Error('Assign a challenge first.');
 const strokes=challengeCanvas.get();if(!strokes.length)throw Error('Draw an attempt first.');
 $('challengeSubmit').disabled=true;
 try{
  const r=await api(`/api/challenges/${activeChallenge.id}/submit`,'POST',{strokes});
  activeChallenge=null;challengeCanvas.clear();const box=$('challengeResponse');box.className='results';box.textContent=r.message;show('challengeResponse',true);
  await refreshChallenge();
 }finally{$('challengeSubmit').disabled=false}
}));
$('deleteForm').addEventListener('submit',async e=>{
 e.preventDefault();try{
  const confirmation=$('deleteConfirm').value;
  if(confirmation!=='DELETE')throw Error('Type DELETE exactly to confirm withdrawal.');
  if(!confirm('Permanently delete all research data associated with this browser profile?'))return;
  await api('/api/participant','DELETE',{confirm:confirmation});
  selected=null;activeChallenge=null;negativeEnrollment=null;enrollmentDrawings=[];
  $('deleteForm').reset();$('studyConsent').checked=false;
  for(const id of ['enrollPanel','verifyPanel','forbiddenPanel','challengePanel'])show(id,false);
  await dashboard();notice('Your live study records were deleted. You can enroll again with a new anonymous profile.');
 }catch(err){notice(err.message,true)}
});

$('freshParticipant').addEventListener('click',onError(async()=>{
 if(!confirm('Start a NEW volunteer session on this device? Previous session data will remain in Supabase, but you will lose access to it from this browser.'))return;
 const r=await api('/api/fresh-session','POST',{});csrf=r.csrf;selected=null;negativeEnrollment=null;activeChallenge=null;enrollmentDrawings=[];
 $('studyConsent').checked=false;for(const id of ['verifyPanel','forbiddenPanel','challengePanel'])show(id,false);
 await dashboard();notice(r.warning);
}));
