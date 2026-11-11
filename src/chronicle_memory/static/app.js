'use strict';
const $ = id => document.getElementById(id);
const COLORS = {experience:'#a3e8cd',understanding:'#b4a9e9',entity:'#e6a88e',source:'#8bbcd5',state:'#dcc68d'};
const NAMES = {experience:'经历',understanding:'认识',entity:'人物与主题',source:'来源证据',state:'事实与计划'};
const SYMBOLS = {experience:'●',understanding:'◆',entity:'✳',source:'▤',state:'◷'};
const RELATIONS = {EVIDENCED_BY:'来源证据',INVOLVES:'涉及实体',SUPPORTED_BY:'认识依据',REVISES:'补充版本',CORRECTS:'更正版本',RELATED_TO:'相关',CONTINUES:'延续',CONTRADICTS:'相互矛盾',CAUSED_BY:'因果依据'};
const RECORD_KINDS = new Set(['experience','understanding','state']);
const canvas = $('graph'), ctx = canvas.getContext('2d');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
let graph = {nodes:[],edges:[]}, nodesById = new Map(), visible = [], visibleEdges = [], matches = new Set();
let selected = null, hovered = null, kind = 'all', query = '', layout = 'orbit';
let yaw = -.28, pitch = -.48, zoom = 1, rotating = !reducedMotion, width = 1, height = 1, last = 0;
let minTime = 0, maxTime = 0, cutoff = Infinity, pending = false, projection = new Map(), pointer = null;
const pad = n => String(n).padStart(2,'0');
function formatDate(value, full=false) {
  if (!value) return '未明确';
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-CN',{timeZone:graph.timezone || 'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',...(full ? {hour:'2-digit',minute:'2-digit',hour12:false}:{})}).format(d);
}
function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}
function hash(value) { let h=2166136261; for (const c of value) h=Math.imul(h^c.charCodeAt(0),16777619); return h>>>0; }
function positionNodes() {
  const records = graph.nodes.filter(n=>RECORD_KINDS.has(n.kind));
  const auxiliary = graph.nodes.filter(n=>!RECORD_KINDS.has(n.kind));
  records.forEach((n,i)=>{
    const a=i*2.39996, radius=records.length===1 ? 0 : 70+Math.sqrt(i)*35;
    n.orbit=[Math.cos(a)*radius,Math.sin(a*1.7)*Math.min(105,radius),Math.sin(a)*radius];
    n.timeline=[records.length===1?0:(new Date(n.recorded_at).getTime()-minTime)/Math.max(1,maxTime-minTime)*460-230,(i%3-1)*48,(i%5-2)*32];
  });
  auxiliary.forEach((n,i)=>{
    const a=i/Math.max(1,auxiliary.length)*Math.PI*2-.65;
    const ring=185+(Math.floor(i/12)%4)*38;
    n.orbit=[Math.cos(a)*ring,Math.sin(a)*ring*.68,Math.sin(a*2+.3)*105];
    const parent=graph.edges.map(e=>e.to===n.id ? nodesById.get(e.from):null).find(x=>x?.timeline);
    const origin=parent?.timeline || [0,0,0];
    n.timeline=[origin[0]+Math.cos(a)*85,origin[1]+Math.sin(a)*100,origin[2]+Math.cos(a*1.3)*85];
  });
  graph.nodes.forEach(n=>{n.position=[...(n.position || n.orbit)];});
}
function filterGraph() {
  const knownRecords = new Set(graph.nodes.filter(n=>RECORD_KINDS.has(n.kind) && new Date(n.recorded_at).getTime()<=cutoff).map(n=>n.id));
  const timeNodes = new Set(knownRecords);
  graph.edges.forEach(e=>{ if(knownRecords.has(e.from) && !RECORD_KINDS.has(nodesById.get(e.to)?.kind)) timeNodes.add(e.to); });
  matches = new Set(graph.nodes.filter(n=>timeNodes.has(n.id) && (kind==='all'||n.kind===kind) && (!query || `${n.title} ${n.text} ${(n.payload.aliases||[]).join(' ')}`.toLocaleLowerCase().includes(query))).map(n=>n.id));
  const included = new Set(matches);
  graph.edges.forEach(e=>{if(timeNodes.has(e.from)&&timeNodes.has(e.to)&&(matches.has(e.from)||matches.has(e.to))){included.add(e.from);included.add(e.to);}});
  visible=graph.nodes.filter(n=>included.has(n.id));
  visibleEdges=graph.edges.filter(e=>included.has(e.from)&&included.has(e.to)&&(!e.recorded_at||new Date(e.recorded_at).getTime()<=cutoff));
  if(!selected || !matches.has(selected)) selected=visible.find(n=>matches.has(n.id)&&RECORD_KINDS.has(n.kind))?.id || visible.find(n=>matches.has(n.id))?.id || null;
  $('graph-count').textContent=pad(visible.length);
  $('graph-caption').textContent=query||kind!=='all' ? `${matches.size} 个匹配 · 保留相邻节点` : `${knownRecords.size} 个记忆版本 · ${visibleEdges.length} 条连接`;
  $('scene-state').textContent=visible.length ? `${visible.length} NODES / ${visibleEdges.length} CONNECTIONS` : 'NO VISIBLE MEMORIES';
  $('empty').classList.toggle('hidden',visible.length>0);
  if(!visible.length){
    $('empty').querySelector('h2').textContent=graph.nodes.length ? '这个视角下，暂时没有记忆。' : '你的记忆宇宙，等待第一颗星。';
    $('empty').querySelector('p').textContent=graph.nodes.length ? '试试清空搜索、切换图层，或回到全部时间。' : '让 Agent 使用 Chronicle 记录一段经历，\n然后刷新这里，连接就会开始生长。';
  }
  renderList(); renderDetail();
}
function renderList() {
  const list=$('memory-list');list.replaceChildren();
  const candidates=visible.filter(n=>matches.has(n.id));
  $('result-count').textContent=pad(candidates.length);
  if(!candidates.length){list.append(element('p','list-empty','没有符合条件的节点'));return;}
  candidates.forEach(n=>{
    const b=element('button',`memory-item${n.id===selected?' selected':''}`);b.type='button';b.setAttribute('aria-pressed',String(n.id===selected));
    b.append(element('span',`item-mark ${n.kind}`,SYMBOLS[n.kind]));
    const c=element('div','item-copy');c.append(element('strong','',n.title),element('small','',`${NAMES[n.kind]}${n.recorded_at?' · '+formatDate(n.recorded_at):''}`));
    b.append(c);b.addEventListener('click',()=>select(n.id));list.append(b);
  });
}
function select(id) {selected=id;renderList();renderDetail();$('announcement').textContent=`已选择${nodesById.get(id)?.title||''}`;}
function meta(label,value) {const row=element('div','meta-row');row.append(element('span','',label),element('span','',value));$('detail-meta').append(row);}
function renderDetail() {
  const node=nodesById.get(selected);
  $('detail-empty').classList.toggle('hidden',!!node);$('detail-content').classList.toggle('hidden',!node);
  if(!node){$('detail-index').textContent='/ 000';return;}
  const p=node.payload;
  $('detail-index').textContent=`/ ${String(graph.nodes.indexOf(node)+1).padStart(3,'0')}`;
  $('detail-kind').textContent=`${SYMBOLS[node.kind]}  ${NAMES[node.kind]}`;
  const later=visible.some(n=>n.logical_id && n.logical_id===node.logical_id && new Date(n.recorded_at)>new Date(node.recorded_at));
  $('detail-version').textContent=RECORD_KINDS.has(node.kind) ? (later?'较早版本':'此视角最新记录') : '可追溯';
  $('detail-title').textContent=node.title;$('detail-text').textContent=node.text;
  $('content-heading').textContent=node.kind==='source'?'原始来源':node.kind==='entity'?'实体名称':'记忆正文';
  $('detail-meta').replaceChildren();
  if(node.recorded_at)meta('记录时间',formatDate(node.recorded_at,true));
  if(p.when)meta('发生时间',p.when.start ? formatDate(p.when.start,true) : p.when.description||'时间不明确');
  if(p.when?.precision)meta('时间精度',({exact:'明确时刻',day:'日期',range:'时间范围',approximate:'大致时间',unknown:'未知'})[p.when.precision]);
  if(p.basis)meta('表达依据',p.basis==='explicit'?'用户明确表达':'模型归纳 · 需结合证据');
  if(p.valid_from)meta('开始适用',formatDate(p.valid_from,true));
  if(p.valid_to)meta('适用截止',formatDate(p.valid_to,true));
  if(p.kind)meta('记录类别',({fact:'事实',rule:'规则',plan:'计划'})[p.kind]||p.kind);
  if(p.due_at)meta('计划时间',formatDate(p.due_at,true));
  if(p.category)meta('实体类别',p.category);
  if(p.aliases?.length)meta('别名',p.aliases.join(' · '));
  if(p.role)meta('来源角色',p.role);
  if(p.conversation_id)meta('会话',p.conversation_id);
  meta('记忆空间',graph.namespace);
  const links=visibleEdges.filter(e=>e.from===selected||e.to===selected);
  $('detail-link-count').textContent=pad(links.length);$('detail-links').replaceChildren();
  links.forEach(e=>{
    const target=nodesById.get(e.from===selected?e.to:e.from);
    const b=element('button','detail-link');b.type='button';b.append(element('span',target.kind,SYMBOLS[target.kind]));
    const c=element('div');c.append(element('strong','',target.title),element('small','',`${e.from===selected?'→':'←'} ${RELATIONS[e.kind]||e.kind}`));
    b.append(c,element('span','','↗'));if(e.explanation)b.title=e.explanation;b.addEventListener('click',()=>select(target.id));$('detail-links').append(b);
  });
  if(!links.length)$('detail-links').append(element('p','list-empty','当前视角下没有关联节点'));
  $('detail-json').textContent=JSON.stringify({record_id:node.record_id,...p},null,2);
}
async function load() {
  if(pending)return;pending=true;$('refresh').disabled=true;$('connection-label').textContent='正在同步';
  try {
    const response=await fetch('/api/graph',{cache:'no-store'});const body=await response.json();
    if(!response.ok||!body.ok)throw new Error(body.error?.message||'暂时无法读取记忆');
    graph=body.result;nodesById=new Map(graph.nodes.map(n=>[n.id,n]));
    const times=graph.nodes.filter(n=>RECORD_KINDS.has(n.kind)).map(n=>new Date(n.recorded_at).getTime());
    maxTime=times.length ? Math.max(...times) : Date.now();minTime=times.length ? Math.min(...times) : maxTime-60000;
    if(minTime===maxTime)minTime-=60000;
    cutoff=Infinity;$('time-range').value='1000';$('time-reset').replaceChildren(document.createTextNode('全部时间 '),element('span','','↗'));
    $('time-start').textContent=formatDate(minTime,true);$('time-end').textContent=formatDate(maxTime,true);
    $('timeline-ticks').replaceChildren();
    const bins=new Set(times.map(t=>Math.round((t-minTime)/(maxTime-minTime)*49)));
    for(let i=0;i<50;i++)$('timeline-ticks').append(element('i',bins.has(i)?'event':i%7===0?'major':''));
    $('namespace').textContent=graph.namespace;
    ['all',...Object.keys(NAMES)].forEach(k=>$(`count-${k}`).textContent=pad(k==='all'?graph.nodes.length:graph.nodes.filter(n=>n.kind===k).length));
    $('connection-label').textContent='已连接';$('status-dot').classList.remove('offline');
    $('notice').classList.toggle('hidden',!graph.truncated);
    $('notice').textContent=graph.truncated?`当前展示最近 ${graph.limit} 个记忆版本及其连接，共 ${graph.total_records} 个版本。时间筛选只作用于已加载记录。`:'';
    positionNodes();filterGraph();
  }catch(error){
    $('connection-label').textContent='连接中断';$('status-dot').classList.add('offline');
    $('notice').textContent=`${error.message}。确认 Neo4j 正在运行后，点击右上角刷新。${graph.nodes.length?' 当前保留上次加载的画面。':''}`;
    $('notice').classList.remove('hidden');$('scene-state').textContent='OFFLINE · 等待重新连接';
  }finally{pending=false;$('refresh').disabled=false;}
}
function project(point) {
  const [x,y,z]=point, cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
  const rx=x*cy-z*sy,rz=x*sy+z*cy,ry=y*cp-rz*sp,depth=y*sp+rz*cp;
  const perspective=700/(700+depth),scale=Math.min(width/650,height/540)*zoom;
  return {x:width*.50+rx*perspective*scale,y:height*.59+ry*perspective*scale,scale:perspective*scale,z:depth};
}
function rgba(hex,alpha) {return `rgba(${parseInt(hex.slice(1,3),16)},${parseInt(hex.slice(3,5),16)},${parseInt(hex.slice(5,7),16)},${alpha})`;}
function roundRect(x,y,w,h,r) {ctx.beginPath();ctx.roundRect(x,y,w,h,r);}
function drawBackdrop(t) {
  for(let i=0;i<85;i++){
    const h=hash(`star-${i}`),x=(h%10000)/10000*width,y=((h>>>10)%10000)/10000*height;
    ctx.fillStyle=`rgba(157,194,188,${.10+(i%5)*.025})`;ctx.beginPath();ctx.arc(x,y,i%11===0?1:.6,0,Math.PI*2);ctx.fill();
  }
  [150,230,310].forEach((radius,index)=>{
    ctx.beginPath();
    for(let i=0;i<=100;i++){const a=i/100*Math.PI*2,p=project([Math.cos(a)*radius,100,Math.sin(a)*radius]);i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y);}
    ctx.strokeStyle=`rgba(128,190,168,${index===1?.13:.065})`;ctx.lineWidth=.6;ctx.stroke();
  });
  for(let i=0;i<8;i++){
    const a=i/8*Math.PI*2,p=project([Math.cos(a)*315,100,Math.sin(a)*315]),q=project([Math.cos(a)*325,100,Math.sin(a)*325]);
    ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.strokeStyle='#7cae9433';ctx.stroke();
  }
  const center=project([0,100,0]);
  const halo=ctx.createRadialGradient(center.x,center.y,0,center.x,center.y,Math.min(width,height)*.37);
  halo.addColorStop(0,'#8ce0b70a');halo.addColorStop(1,'#8ce0b700');ctx.fillStyle=halo;ctx.fillRect(0,0,width,height);
}
function draw(time) {
  const dt=Math.min(32,time-last||16);last=time;
  if(rotating&&!pointer&&!document.hidden)yaw+=dt*.00006;
  ctx.clearRect(0,0,width,height);drawBackdrop(time);projection=new Map();
  visible.forEach(n=>{
    const target=n[layout];for(let i=0;i<3;i++)n.position[i]+=(target[i]-n.position[i])*.08;
    projection.set(n.id,project(n.position));
  });
  const adjacent=new Set([selected]);visibleEdges.forEach(e=>{if(e.from===selected)adjacent.add(e.to);if(e.to===selected)adjacent.add(e.from);});
  visibleEdges.forEach((e,index)=>{
    const a=projection.get(e.from),b=projection.get(e.to);if(!a||!b)return;
    const isRelated=e.from===selected||e.to===selected,alpha=isRelated?.34:.10;
    const bend=18+Math.abs(a.x-b.x)*.05,cx=(a.x+b.x)/2,cy=(a.y+b.y)/2-bend;
    ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.quadraticCurveTo(cx,cy,b.x,b.y);ctx.strokeStyle=rgba(COLORS[nodesById.get(e.to).kind],alpha);ctx.lineWidth=isRelated?1:.65;ctx.stroke();
    if(!reducedMotion&&isRelated){const f=(time*.00009+index*.23)%1,x=(1-f)**2*a.x+2*(1-f)*f*cx+f*f*b.x,y=(1-f)**2*a.y+2*(1-f)*f*cy+f*f*b.y;ctx.fillStyle='#c1efd2a0';ctx.beginPath();ctx.arc(x,y,1.5,0,Math.PI*2);ctx.fill();}
    if(isRelated && (e.from===hovered||e.to===hovered)){
      ctx.font='8px -apple-system, sans-serif';ctx.textAlign='center';ctx.fillStyle='#719b8c';ctx.fillText(RELATIONS[e.kind]||e.kind,cx,cy-5);
    }
  });
  const labelBoxes=[];
  [...visible].sort((a,b)=>projection.get(b.id).z-projection.get(a.id).z).forEach(n=>{
    const p=projection.get(n.id),active=n.id===selected,hover=n.id===hovered,col=COLORS[n.kind];
    const base=RECORD_KINDS.has(n.kind)?21:12,r=Math.max(4,base*p.scale)*(active?1.12:1);
    p.radius=r;
    ctx.globalAlpha=matches.has(n.id)?1:.37;
    if(selected&&!adjacent.has(n.id))ctx.globalAlpha*=.5;
    const glow=ctx.createRadialGradient(p.x,p.y,r*.4,p.x,p.y,r*(active?5:3));glow.addColorStop(0,rgba(col,active?.20:.10));glow.addColorStop(1,rgba(col,0));ctx.fillStyle=glow;ctx.beginPath();ctx.arc(p.x,p.y,r*5,0,Math.PI*2);ctx.fill();
    if(active||hover){ctx.strokeStyle=rgba(col,.35);ctx.lineWidth=.7;ctx.beginPath();ctx.arc(p.x,p.y,r+7,0,Math.PI*2);ctx.stroke();ctx.save();ctx.translate(p.x,p.y);ctx.rotate(time*.00007);ctx.setLineDash([3,8]);ctx.beginPath();ctx.ellipse(0,0,r+17,r+13,-.4,0,Math.PI*2);ctx.strokeStyle=rgba(col,.21);ctx.stroke();ctx.restore();}
    const sphere=ctx.createRadialGradient(p.x-r*.32,p.y-r*.4,r*.05,p.x,p.y,r*1.13);sphere.addColorStop(0,'#e9fff3');sphere.addColorStop(.18,col);sphere.addColorStop(.52,rgba(col,.6));sphere.addColorStop(1,'#132c2b');
    ctx.fillStyle=sphere;ctx.beginPath();ctx.arc(p.x,p.y,r,0,Math.PI*2);ctx.fill();ctx.strokeStyle=rgba(col,.65);ctx.lineWidth=.7;ctx.stroke();
    ctx.beginPath();ctx.ellipse(p.x,p.y,r*.95,r*.30,-.5,0,Math.PI*2);ctx.strokeStyle=rgba(col,.25);ctx.stroke();
    if(visible.length<35||active||hover){
      const title=n.title.length>19?n.title.slice(0,18)+'…':n.title;ctx.font=`${active?'500 ':'400 '}${active?11:10}px -apple-system, "PingFang SC", sans-serif`;
      const w=ctx.measureText(title).width+19,x=Math.max(8,Math.min(width-w-8,p.x-w/2));
      let y=p.y+r+14;
      for(let attempt=0;attempt<6 && labelBoxes.some(b=>x<b.x+b.w+5&&x+w+5>b.x&&y<b.y+b.h+5&&y+24+5>b.y);attempt++)y+=29;
      labelBoxes.push({x,y,w,h:active?43:24});
      roundRect(x,y,w,24,5);ctx.fillStyle=active?'#152a25e8':'#111c20c9';ctx.fill();ctx.strokeStyle=rgba(col,active?.3:.12);ctx.lineWidth=.6;ctx.stroke();
      ctx.textAlign='center';ctx.fillStyle=active?'#d5eddf':'#9fbab3';ctx.fillText(title,x+w/2,y+15);
      if(active){ctx.font='7px ui-monospace, monospace';ctx.fillStyle='#6c9b86';ctx.fillText(`${NAMES[n.kind]} / ${n.recorded_at?formatDate(n.recorded_at):n.payload.category||'ENTITY'}`,x+w/2,y+38);}
    }
    ctx.globalAlpha=1;
  });
  requestAnimationFrame(draw);
}
function resize() {const rect=$('canvas-area').getBoundingClientRect();width=rect.width;height=rect.height;const dpr=Math.min(devicePixelRatio||1,2);canvas.width=width*dpr;canvas.height=height*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);}
function hit(event) {const r=canvas.getBoundingClientRect(),x=event.clientX-r.left,y=event.clientY-r.top;return [...visible].sort((a,b)=>projection.get(a.id)?.z-projection.get(b.id)?.z).find(n=>{const p=projection.get(n.id);return p&&Math.hypot(p.x-x,p.y-y)<Math.max(17,p.radius+9);});}
function setRotation(value){rotating=value;$('rotate').setAttribute('aria-pressed',String(value));$('rotate').replaceChildren(document.createTextNode(value?'Ⅱ ':'▷ '),element('span','',value?'暂停旋转':'自动旋转'));}
canvas.addEventListener('pointerdown',e=>{pointer={x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,moved:false};canvas.setPointerCapture(e.pointerId);$('tooltip').classList.add('hidden');});
canvas.addEventListener('pointermove',e=>{
  if(pointer){const dx=e.clientX-pointer.x,dy=e.clientY-pointer.y;pointer.moved ||= Math.hypot(e.clientX-pointer.startX,e.clientY-pointer.startY)>4;yaw+=dx*.006;pitch=Math.max(-1,Math.min(1,pitch+dy*.005));pointer.x=e.clientX;pointer.y=e.clientY;return;}
  const node=hit(e);hovered=node?.id||null;canvas.style.cursor=node?'pointer':'grab';$('tooltip').classList.toggle('hidden',!node);
  if(node){$('tooltip').textContent=node.title;const r=canvas.getBoundingClientRect();$('tooltip').style.left=`${Math.max(10,Math.min(width-270,e.clientX-r.left+15))}px`;$('tooltip').style.top=`${Math.max(5,e.clientY-r.top-38)}px`;}
});
canvas.addEventListener('pointerup',e=>{if(pointer&&!pointer.moved){const n=hit(e);if(n)select(n.id);}pointer=null;});
canvas.addEventListener('pointercancel',()=>{pointer=null;});
canvas.addEventListener('pointerleave',()=>{hovered=null;$('tooltip').classList.add('hidden');});
canvas.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.45,Math.min(2.8,zoom-e.deltaY*.001));},{passive:false});
canvas.addEventListener('keydown',e=>{if(e.key==='ArrowLeft')yaw-=.12;else if(e.key==='ArrowRight')yaw+=.12;else if(e.key==='ArrowUp')pitch=Math.max(-1,pitch-.1);else if(e.key==='ArrowDown')pitch=Math.min(1,pitch+.1);else if(e.key==='+')zoom=Math.min(2.8,zoom+.1);else if(e.key==='-')zoom=Math.max(.45,zoom-.1);else return;e.preventDefault();});
$('rotate').addEventListener('click',()=>setRotation(!rotating));
$('reset').addEventListener('click',()=>{yaw=-.28;pitch=-.48;zoom=1;});
$('refresh').addEventListener('click',load);$('empty-refresh').addEventListener('click',()=>{kind='all';query='';$('search').value='';document.querySelectorAll('.layer').forEach(b=>{b.classList.toggle('active',b.dataset.kind==='all');b.setAttribute('aria-pressed',String(b.dataset.kind==='all'));});load();});
$('search').addEventListener('input',e=>{query=e.target.value.trim().toLocaleLowerCase();filterGraph();});
window.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){e.preventDefault();$('search').focus();}if(e.key==='Escape'){$('search').value='';query='';filterGraph();}});
document.querySelectorAll('.layer').forEach(b=>b.addEventListener('click',()=>{kind=b.dataset.kind;document.querySelectorAll('.layer').forEach(x=>{x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',String(x===b));});filterGraph();}));
document.querySelectorAll('.view-tab').forEach(b=>b.addEventListener('click',()=>{layout=b.dataset.layout;document.querySelectorAll('.view-tab').forEach(x=>{x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',String(x===b));});}));
$('time-range').addEventListener('input',e=>{const fraction=Number(e.target.value)/1000;cutoff=fraction===1?Infinity:minTime+(maxTime-minTime)*fraction;$('time-reset').textContent=fraction===1?'全部时间 ↗':formatDate(cutoff,true);filterGraph();});
$('time-reset').addEventListener('click',()=>{$('time-range').value='1000';cutoff=Infinity;$('time-reset').textContent='全部时间 ↗';filterGraph();});
new ResizeObserver(resize).observe($('canvas-area'));setRotation(rotating);resize();requestAnimationFrame(draw);load();
