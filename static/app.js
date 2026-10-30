'use strict';
const $ = s=>document.querySelector(s);
const canvas=$('#canvas'), ctx=canvas.getContext('2d');
const W=900,H=500;let csrf='',current=null,strokes=[],times=[],active=false,activePointer=null,startTime=null,pointerType='unknown',submitting=false;
function show(id,on=true){$('#'+id).classList.toggle('hide',!on)}
function error(message=''){$('#error').textContent=message}
async function api(path,data){const opts={credentials:'same-origin',headers:{'Content-Type':'application/json','X-D2S-CSRF':csrf}};if(data!==undefined){opts.method='POST';opts.body=JSON.stringify(data)}const r=await fetch(path,opts);const result=await r.json();if(!r.ok)throw new Error(result.error||`Request failed (${r.status})`);return result}
function setupCanvas(el,w=W,h=H){const dpr=Math.min(window.devicePixelRatio||1,2);el.width=w*dpr;el.height=h*dpr;el.getContext('2d').setTransform(dpr,0,0,dpr,0,0)}
function resetDrawing(){strokes=[];times=[];active=false;activePointer=null;startTime=null;ctx.clearRect(0,0,W,H);renderCanvas();error('')}
function renderCanvas(){ctx.clearRect(0,0,W,H);ctx.strokeStyle='#245da9';ctx.lineWidth=4;ctx.lineJoin='round';ctx.lineCap='round';for(const s of strokes){ctx.beginPath();s.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.stroke()}}
function coords(e){const r=canvas.getBoundingClientRect();return [Math.min(W,Math.max(0,(e.clientX-r.left)/r.width*W)),Math.min(H,Math.max(0,(e.clientY-r.top)/r.height*H))]}
function point(e){if(!active||e.pointerId!==activePointer)return;const p=coords(e);const pts=strokes[strokes.length-1];const t=times[times.length-1];if(pts.length&&Math.hypot(p[0]-pts[pts.length-1][0],p[1]-pts[pts.length-1][1])<.8)return;pts.push(p);t.push(Math.max(0,e.timeStamp-startTime));renderCanvas()}
canvas.addEventListener('pointerdown',e=>{if(submitting||active||!current||current.phase==='complete')return;e.preventDefault();active=true;activePointer=e.pointerId;pointerType=e.pointerType||'unknown';try{canvas.setPointerCapture(e.pointerId)}catch{};if(startTime===null)startTime=e.timeStamp;strokes.push([]);times.push([]);point(e)});
canvas.addEventListener('pointermove',e=>{if(!active||e.pointerId!==activePointer)return;e.preventDefault();const events=e.getCoalescedEvents?.()||[e];for(const v of events)point(v)});
function finish(e){if(!active||e.pointerId!==activePointer)return;point(e);active=false;activePointer=null;const s=strokes[strokes.length-1];if(s.length===1){s.push([...s[0]]);times[times.length-1].push(times[times.length-1][0]+1)}renderCanvas()}
canvas.addEventListener('pointerup',finish);canvas.addEventListener('pointercancel',finish);
function referenceView(ss){const tc=$('#targetCanvas');setupCanvas(tc);const c=tc.getContext('2d');c.strokeStyle='#265ca1';c.lineWidth=3;c.lineCap='round';c.lineJoin='round';for(const s of (ss||[])){c.beginPath();s.forEach((p,i)=>i?c.lineTo(p[0],p[1]):c.moveTo(p[0],p[1]));c.stroke()}}
function paint(s){current=s;error();show('welcome',s.phase==='welcome');show('capture',!['welcome','complete'].includes(s.phase));show('completion',s.phase==='complete');show('progress',!['welcome','complete'].includes(s.phase));$('#title').textContent=s.title;$('#instruction').textContent=s.instruction||'';$('#step').textContent=s.total?`${s.phase.replaceAll('_',' ')}  ·  ${s.step} of ${s.total}`:'';
 if(s.total)$('#progressFill').style.width=(100*s.step/s.total)+'%';show('targetBox',Boolean(s.show_target));if(s.show_target)referenceView(s.target_strokes);show('followup',Boolean(s.can_return));resetDrawing();}
async function bootstrap(){try{setupCanvas(canvas);const state=await api('/api/state');csrf=state.csrf;paint(state.study)}catch(e){error(e.message)}}
$('#clear').onclick=resetDrawing;
$('#submit').onclick=async()=>{if(submitting)return;if(!strokes.length||strokes.some(s=>s.length<2)){error('Draw a gesture first.');return}submitting=true;$('#submit').disabled=true;try{const r=await api('/api/drawing',{strokes,phase:current.phase,step:current.step,dynamics:{times,pointer_type:pointerType}});paint(r.study)}catch(e){error(e.message)}finally{submitting=false;$('#submit').disabled=false}};
$('#start').onclick=async()=>{try{const r=await api('/api/start',{consent:$('#consent').checked,share_reference:$('#share').checked});$('#recoveryValue').textContent=r.recovery_code;paint(r.study);show('recovery',true)}catch(e){error(e.message)}};
$('#dismiss').onclick=()=>show('recovery',false);
$('#copy').onclick=async()=>{try{await navigator.clipboard.writeText($('#recoveryValue').textContent);$('#copy').textContent='Copied'}catch(e){error('Copy the code manually.')}};
$('#resume').onclick=async()=>{try{const r=await api('/api/resume',{code:$('#resumeCode').value.trim()});paint(r.study)}catch(e){error(e.message)}};
$('#followup').onclick=async()=>{try{paint((await api('/api/return',{})).study)}catch(e){error(e.message)}};
$('#withdraw').onclick=async()=>{if(!confirm('Permanently delete your Draw2Seed v2.4 study data?'))return;try{await api('/api/delete-my-data',{});show('recovery',false);paint({phase:'welcome',title:'Welcome to Draw2Seed',instruction:'Choose a gesture and repeat it across a short study.'})}catch(e){error(e.message)}};
$('#switch').onclick=async()=>{if(!confirm('Start a different participant on this browser? Previous data remains stored.'))return;try{await api('/api/new-browser-participant',{});show('recovery',false);paint({phase:'welcome',title:'Welcome to Draw2Seed',instruction:'Choose a gesture and repeat it across a short study.'})}catch(e){error(e.message)}};
bootstrap();
