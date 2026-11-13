/**
 * @fileoverview Swarm pane Overview and Members views.
 *
 * Renders the narrator's goal, threads, running tasks and progress (Overview)
 * and the member list (Members) from the /api/swarm snapshot app.js already
 * polls. Members are drawn as pixel bees. app.js calls
 * window.SwarmHive.update(snapshot, element, {view: 'overview' | 'cards'}).
 * Every string from the board or the narrator goes through esc().
 */
(function() {
  let S = {}, P = [];
  const state = {view: 'overview'};
  const $ = (s, el = document) => el.querySelector(s);
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const people = sc => Object.entries(sc.members).filter(([n, x]) => !x.system);
  const ORDER = {needs_you: 0, working: 1, waiting_on: 2, parked: 3};

  /* ---------- pixel bees ---------- */
  const HUE = {coordinator:46};
  function hsl2hex(h,s,l){ h=((h%360)+360)%360; s/=100; l/=100; const k=n=>(n+h/30)%12; const a=s*Math.min(l,1-l); const f=n=>l-a*Math.max(-1,Math.min(k(n)-3,Math.min(9-k(n),1))); return '#'+[f(0),f(8),f(4)].map(x=>Math.round(x*255).toString(16).padStart(2,'0')).join(''); }
  const shiftHue=(h,target,amt)=>{ const d=((target-h+540)%360)-180; return h+d*amt; };
  function ramp(n){ const h=hueOf(n); return {
    spec: hsl2hex(shiftHue(h,55,0.28), 96, 90), hi: hsl2hex(shiftHue(h,55,0.18), 90, 74), mid: hsl2hex(h, 78, 60),
    sh: hsl2hex(shiftHue(h,265,0.22), 58, 44), ol: hsl2hex(shiftHue(h,265,0.35), 42, 19),
    skh: hsl2hex(shiftHue(h,265,0.15), 20, 30), sk: hsl2hex(shiftHue(h,265,0.3), 24, 18), skd: hsl2hex(shiftHue(h,265,0.45), 32, 10) }; }
  const tcol = n => hsl2hex(hueOf(n), 78, 60);
  const hueOf = n => HUE[n] ?? [12,150,262,330,196,228,96,28,290,175,350,60][hash(String(n))%12];
  const hash = s => [...s].reduce((a,c)=>(a*31+c.charCodeAt(0))>>>0, 7);
  const HEXD = (s => encodeURIComponent(s).replace(/'/g, '%27'))(`<svg xmlns='http://www.w3.org/2000/svg' width='28' height='24.25' viewBox='0 0 28 24.25'><path d='M14 0l12.12 7v14l-12.12 7L1.88 21V7z' fill='none' stroke='rgba(255,255,255,0.08)' stroke-width='1.2'/></svg>`);
  // Tiles tint the host card color so they work in light and dark themes.
  const tileTint = n => `color-mix(in srgb, ${tcol(n)} 22%, var(--sw-card, #fff))`;
  const hexBG = n => n==='coordinator' ? `background:#1C1B22 url('data:image/svg+xml,${HEXD}')` : `background:${tileTint(n)}`;

  function drawBee(cv, name, state, frame){
    const W=22,H=18; cv.width=W; cv.height=H; const c=cv.getContext('2d'); c.imageSmoothingEnabled=false; c.clearRect(0,0,W,H);
    const R=ramp(name), queen=name==='coordinator', landed=(state==='parked'||state==='landed'), asleep=state==='parked';
    const P=(x,y,col)=>{ if(x<0||y<0||x>=W||y>=H) return; c.fillStyle=col; c.fillRect(x,y,1,1); };
    const cx=11, cy=10.5, rx=7.3, ry=4.8;
    const inside=(x,y)=>{ const nx=(x+0.5-cx)/rx, ny=(y+0.5-cy)/ry; return nx*nx+ny*ny<=1; };
    let L=[-0.42,-0.62,0.66]; const Ln=Math.hypot(...L); L=L.map(v=>v/Ln);
    const N4=[[1,0],[-1,0],[0,1],[0,-1]];
    const rows=(spec)=>{ const pts=[]; spec.forEach(([y,x0,x1])=>{ for(let x=x0;x<=x1;x++) pts.push([x,y]); }); return pts; };
    const drawWing=(pts, glint)=>{ const set=new Set(pts.map(p=>p.join(','))); pts.forEach(([x,y])=>{ const edge=N4.some(([dx,dy])=>!set.has((x+dx)+','+(y+dy))); P(x,y, edge?'rgba(140,160,192,0.9)':'rgba(255,255,255,0.72)'); });
      if(glint){ const tl=pts.filter(([x,y])=>!N4.some(([dx,dy])=>!set.has((x+dx)+','+(y+dy)))).sort((a,b)=>(a[1]-b[1])||(a[0]-b[0])); if(tl[0]) P(tl[0][0],tl[0][1],'#FFFFFF'); } };
    let back=null, front;
    if(landed){ front=rows([[5,14,17],[6,13,18]]); }
    else if(frame===0){ back=rows([[1,16,17],[2,16,18],[3,15,18],[4,15,17],[5,15,16]]); front=rows([[0,13,14],[1,12,15],[2,11,15],[3,11,14],[4,11,13],[5,11,12]]); }
    else { back=rows([[3,16,18],[4,15,19],[5,16,18]]); front=rows([[3,13,16],[4,12,17],[5,12,17],[6,13,15]]); }
    if(back) drawWing(back,false);
    const stripe=(x,y)=>{ const ny=(y+0.5-cy)/ry; const u=x+0.5+1.15*ny*ny; return (u>10.6&&u<12.5)||(u>14.3&&u<16.1)||(u>17.9); };
    for(let y=0;y<H;y++) for(let x=0;x<W;x++){
      if(!inside(x,y)) continue;
      const nx=(x+0.5-cx)/rx, ny=(y+0.5-cy)/ry, rr=Math.min(1,nx*nx+ny*ny), nz=Math.sqrt(1-rr);
      const I=nx*L[0]+ny*L[1]+nz*L[2];
      P(x,y, stripe(x,y) ? (I>0.80?R.skh : I>0.30?R.sk : R.skd) : (I>0.965?R.spec : I>0.78?R.hi : I>0.26?R.mid : R.sh));
    }
    for(let y=0;y<H;y++) for(let x=0;x<W;x++){
      if(inside(x,y)) continue;
      const nbs=N4.filter(([dx,dy])=>inside(x+dx,y+dy)); if(!nbs.length) continue;
      const nx=(x+0.5-cx)/rx, ny=(y+0.5-cy)/ry, n=Math.hypot(nx,ny); const d=(nx/n)*L[0]+(ny/n)*L[1];
      const nextToStripe=nbs.some(([dx,dy])=>stripe(x+dx,y+dy));
      P(x,y, (d>0.5 && !nextToStripe) ? R.sh : R.ol);
    }
    // stinger
    P(19,10,R.ol); P(20,10,R.ol);
    // face
    if(asleep){ P(6,10,R.ol); P(7,10,R.ol); }
    else { P(6,9,'#FFFFFF'); P(7,9,R.skd); P(6,10,R.skd); P(7,10,R.skd); }
    P(5,11,'rgba(255,128,150,0.55)'); P(4,11,'rgba(255,128,150,0.35)');
    // antennae or crown
    if(queen){ const g1='#F7CE5B', g2='#FFE9A6', g3='#C98A2B'; [[5,5],[6,5],[7,5],[8,5],[9,5]].forEach(([x,y])=>P(x,y,g1)); P(5,4,g2); P(7,4,g2); P(9,4,g2); P(7,3,g2); P(5,5,g3); P(9,5,g3); P(7,5,'#E4566A'); }
    else { [[7,5],[6,4],[5,3]].forEach(([x,y])=>P(x,y,R.ol)); P(4,3,R.sk); [[9,5],[9,4],[9,3]].forEach(([x,y])=>P(x,y,R.ol)); P(8,2,R.sk); }
    drawWing(front,true);
    if(asleep){ const z='#8C90A6'; [[0,0],[1,0],[2,0],[3,0],[2,1],[1,2],[0,3],[1,3],[2,3],[3,3]].forEach(([x,y])=>P(x,y,z)); }
  }
  const spriteTag = (n, state) => `<span class="beebox" data-state="${esc(state||'working')}"><span class="beewrap" data-bee="${esc(n)}" data-state="${esc(state||'working')}"><canvas></canvas><canvas></canvas></span><i class="gsh"></i></span>`;
  function paintSprites(el){ el.querySelectorAll('.beewrap').forEach(w=>{ const [a,b]=w.querySelectorAll('canvas'); drawBee(a, w.dataset.bee, w.dataset.state, 0); drawBee(b, w.dataset.bee, w.dataset.state, 1); }); }

  // Board avatars: one still (landed) bee per name, drawn once and reused as an <img>.
  const beeImgs = new Map();
  function beeImg(n){
    if(!beeImgs.has(n)){ const cv=document.createElement('canvas'); drawBee(cv, n, 'landed', 0); beeImgs.set(n, cv.toDataURL()); }
    return beeImgs.get(n);
  }

  // Nudge each bee onto the device pixel grid so the sprites stay crisp. Reads
  // all positions first (without the previous nudge), then writes.
  function snapBees(root){
    const dpr=window.devicePixelRatio||1;
    const boxes=[...root.querySelectorAll('.beebox')];
    const rects=boxes.map(b=>{ const r=b.getBoundingClientRect(); const m=/translate\(([-\d.]+)px,\s*([-\d.]+)px\)/.exec(b.style.transform||''); return {x:r.left-(m?+m[1]:0), y:r.top-(m?+m[2]:0)}; });
    boxes.forEach((b,i)=>{ const fx=(rects[i].x*dpr)%1, fy=(rects[i].y*dpr)%1; const dx=(fx>0.5?1-fx:-fx)/dpr, dy=(fy>0.5?1-fy:-fy)/dpr;
      b.style.transform = (Math.abs(dx)>0.01||Math.abs(dy)>0.01) ? `translate(${dx}px,${dy}px)` : ''; });
  }
  let snapQueued=false;
  const snapAll=()=>{ if(snapQueued) return; snapQueued=true; requestAnimationFrame(()=>{ snapQueued=false; document.querySelectorAll('.pane').forEach(snapBees); }); };
  window.addEventListener('scroll', snapAll, {passive:true, capture:true});
  window.addEventListener('resize', snapAll);

  const COPY_UI = { team:'The team', lead:'Coordinator', blocked:'Blocked', running:'Running', planned:'Planned next', done:'Done',
    more:'+{n} more', goal:'Goal', threads:'Threads', now:'Now', progress:'Progress', compare:'Compare with now', expand:'Open full timeline', none_blocked:'Nothing blocked', none_running:'Nothing running', none_planned:'Nothing planned', reply:'Reply', open:'Open' };
  // Short copy the narrator writes next to each item (view.json `short` and `now`).
  function copy(){ const a=(window.SwarmHive||{}).viewCopy||{};
    return { ui:a.ui||{}, lead:a.lead||{}, members:a.members||{}, items:a.items||{} }; }
  function ui(k){ const c=copy().ui||{}; return c[k] || COPY_UI[k]; }
  function clipWords(s, n){ s=String(s||'').replace(/\s*\([^)]*\)/g,'').replace(/\s+/g,' ').trim(); const w=s.split(' '); return w.length>n ? w.slice(0,n).join(' ').replace(/[,;:.]$/,'')+'…' : s.replace(/\.$/,''); }
  function firstClause(s){ return String(s||'').split(/(?<=[.!?])\s+(?=[A-Z])|;\s|\s[–—-]\s/)[0]; }
  function itemCopy(id, title, line){ const c=(copy().items||{})[id]||{}; return { title: c.title || clipWords(title, 7), line: c.line || clipWords(firstClause(line), 12) }; }
  function memberLine(n, x){ const c=copy().members||{}; if(c[n]) return c[n]; if(n==='coordinator'){ const l=(copy().lead||{}).doing; if(l) return l; } return clipWords(firstClause(x.now||''), 6); }
  function cleanNote(s, fallback){
    let x = String(s||'').replace(/\s*\(#\d+(?:\s*,\s*#\d+)*\)\s*\.?$/,'').replace(/\s+/g,' ').trim();
    if(x) x = x.charAt(0).toUpperCase() + x.slice(1);
    return x || String(fallback||'').trim();
  }

  /* ---------- Shared bee badges, compact ring, Running card, and Done disclosure ---------- */
  function workerBadges(owners, max, sc){
    const lim = max || 2;
    const list = (owners||[]).slice(0, lim);
    const extra = Math.max(0, (owners||[]).length - list.length);
    return `<span class="o2-wchips">${list.map(n=>{
      const m = sc && sc.members && sc.members[n];
      const st = (sc && !sc.finished && m && !m.killed && m.status==='working') ? 'working' : 'landed';
      return `<span class="o2-wchip bee" title="@${esc(n)}">${spriteTag(n, st)}<span>${esc(n)}</span></span>`;
    }).join('')}${extra>0?`<span class="o2-wchip more">+${extra}</span>`:''}</span>`;
  }

  function taskProgressPill(w){
    if(w && w.progress && w.progress.total>0){
      const pct = Math.min(100, Math.max(0, Math.round((w.progress.done / w.progress.total) * 100)));
      const lbl = w.progress.label || `${w.progress.done}/${w.progress.total}`;
      return `<span class="o2-tprog" title="${esc(lbl)} done"><span class="o2-tpbar"><i style="width:${pct}%"></i></span><span>${esc(lbl)}</span></span>`;
    }
    if(w && w.rawState==='review') return `<span class="o2-tprog rev">In review</span>`;
    return '';
  }

  function ringSvg(tot){
    const sum = tot.done + tot.running + tot.blocked + tot.queued;
    if(sum > 0 && tot.done === sum){
      return `<span class="o2-ring full" aria-hidden="true">✓</span>`;
    }
    const total = sum || 1;
    const C = 43.98;
    let off = 0;
    const segs = ['done','running','blocked'].map(k=>{
      if(!tot[k]) return '';
      const len = (tot[k] / total) * C;
      const s = `<circle cx="9" cy="9" r="7" fill="none" class="seg ${k}" stroke-width="2.2" stroke-dasharray="${len.toFixed(2)} ${C.toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}"/>`;
      off += len;
      return s;
    }).join('');
    return `<svg class="o2-ring" width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><circle cx="9" cy="9" r="7" fill="none" class="trk" stroke-width="2.2"/>${segs}</svg>`;
  }

  function miniBar(n){
    const sum = n.done + n.running + n.blocked + n.queued || 1;
    return `<span class="o2-mbar" aria-hidden="true">${['done','running','blocked','queued'].map(k=>n[k]?`<i class="${k}" style="flex:${n[k]}"></i>`:'').join('')}</span>`;
  }

  function runningCard(sc, runningWs, summaryText, goalTitle, goalLine){
    if(!runningWs.length || sc.finished) return '';
    let sum = sc.runningSummary || '';
    if(!sum && sc.headline && sc.headline !== goalTitle && sc.headline !== goalLine) sum = sc.headline;
    if(!sum && !sc.fromPlan) sum = summaryText || '';
    const lim = state.o2runAll ? runningWs.length : 8;
    const vis = runningWs.slice(0, lim);
    const more = runningWs.length - vis.length;
    const ph = sc.phase && sc.phase.total > 1 ? `<span class="o2-runph">${esc(sc.phase.label)} · ${sc.phase.index}/${sc.phase.total}</span>` : '';
    const rows = vis.map(({w, st})=>{
      const c = itemCopy(w.id, w.title, w.note);
      const note = cleanNote(w.note, c.line);
      return `<li class="o2-runrow ${st}">
        <span class="o2-ti ${st}" aria-label="${st}"></span>
        <div class="o2-runmain">
          <div class="o2-runtop">
            <b class="o2-runt">${esc(c.title)}</b>
            ${taskProgressPill(w)}
            ${workerBadges(w.owners, 2, sc)}
          </div>
          ${note ? `<p class="o2-runnote" title="${esc(w.note||note)}">${esc(note)}</p>` : ''}
        </div>
      </li>`;
    }).join('');
    return `<section class="o2-run">
      <div class="o2-runh"><span class="o2-rundot" aria-hidden="true"></span><b>${ui('running')}</b><em>${runningWs.length}</em>${ph}</div>
      ${sum ? `<p class="o2-runsum">${esc(sum)}</p>` : ''}
      <ul class="o2-runlist">${rows}</ul>
      ${more>0 ? `<button class="o2-more" data-o2="runAll">${ui('more').replace('{n}', more)}</button>` : ''}
    </section>`;
  }

  function progressCard(sc, tot, doneWs, queuedWs, openThreads, openAsks){
    const totalCount = tot.done + tot.running + tot.blocked + tot.queued;
    const open = !!state.o2doneOpen;
    const chips = [];
    if(openAsks > 0) chips.push(`<span class="o2-pchip warn"><i></i>${openAsks} need${openAsks===1?'s':''} you</span>`);
    else if(tot.blocked > 0) chips.push(`<span class="o2-pchip warn"><i></i>${tot.blocked} blocked</span>`);
    if(openThreads !== null) chips.push(`<span class="o2-pchip">${openThreads} thread${openThreads===1?'':'s'} open</span>`);
    else if(tot.running > 0) chips.push(`<span class="o2-pchip info"><i></i>${tot.running} active</span>`);
    const doneOrdered = doneWs.slice().sort((a,b)=>(b.seq||0)-(a.seq||0));
    const lim = state.o2doneAll ? doneOrdered.length : 8;
    const visDone = doneOrdered.slice(0, lim);
    const more = doneOrdered.length - visDone.length;
    const doneBody = visDone.length
      ? `<ul class="o2-donelist">${visDone.map(w=>{
          const c = itemCopy(w.id, w.title, w.note);
          const sub = c.line || cleanNote(w.note, '');
          return `<li><span class="o2-ti done" aria-label="done">✓</span><div class="o2-donem"><b>${esc(c.title)}</b>${sub?`<span>${esc(sub)}</span>`:''}</div>${workerBadges(w.owners, 2, sc)}</li>`;
        }).join('')}</ul>${more>0?`<button class="o2-more" data-o2="doneAll">${ui('more').replace('{n}', more)}</button>`:''}`
      : `<p class="o2-doneempty">No tasks completed yet.</p>`;
    const queuedBody = queuedWs.length
      ? `<div class="o2-queuedsub"><div class="o2-qhdr">Up next · ${queuedWs.length}</div><ul class="o2-donelist queued">${queuedWs.slice(0,6).map(w=>{
          const c = itemCopy(w.id, w.title, w.note);
          return `<li><span class="o2-ti queued" aria-label="queued"></span><div class="o2-donem"><b>${esc(c.title)}</b></div>${workerBadges(w.owners, 2, sc)}</li>`;
        }).join('')}</ul></div>`
      : '';
    return `<section class="o2-prog ${open?'open':''}">
      <button type="button" class="o2-progh" data-o2="doneOpen" aria-expanded="${open}">
        ${ringSvg(tot)}
        <span class="o2-progt"><b>${tot.done} of ${totalCount}</b> tasks done</span>
        <span class="o2-progr">${chips.join('')}<span class="o2-chev" aria-hidden="true"></span></span>
      </button>
      ${open ? `<div class="o2-progb">${doneBody}${queuedBody}</div>` : ''}
    </section>`;
  }

  // Progress reel: only when the narrator listed frames (it does so for UI work only).
  function reelCard(sc){
    const R = sc.reel;
    if(!R || R.length<2) return '';
    const postAt = s => { const p=P.find(q=>q.s>=s); return p ? p.t : sc.now; };
    const doneAt = s => sc.workstreams.filter(w=>w.state==='done' && w.seq<=s).length;
    const total = sc.workstreams.length;
    const F = R.map(f=>({src:f.src, cap:f.caption, when:new Date(postAt(f.seq)).toLocaleString([], {weekday:'short', hour:'numeric', minute:'2-digit'}), done:doneAt(f.seq), total}));
    const i = Math.min(F.length-1, Math.max(0, state.o2reel ?? F.length-1)); const f = F[i];
    return `<section class="o2-reel" data-reel="${esc(JSON.stringify(F))}">
      <div class="o2-rh"><span class="o2-glbl">${ui('progress')}</span><span class="o2-rwhen">${esc(f.when)}</span><button class="o2-btn o2-rcmp" aria-pressed="${!!state.o2reelCmp}">${state.o2reelCmp ? 'Hide compare' : ui('compare')}</button></div>
      <div class="o2-rstage"><img class="o2-rnow" src="${esc(F[F.length-1].src)}" alt=""><img class="o2-rimg" src="${esc(f.src)}" alt="${esc(f.cap)}"><i class="o2-rline" ${state.o2reelCmp?'':'hidden'}></i><input class="o2-rswipe" type="range" min="0" max="100" value="${state.o2reelSwipe ?? 50}" aria-label="Compare then and now" ${state.o2reelCmp?'':'hidden'}><span class="o2-rtag">${i===F.length-1?ui('now'):`${i+1} / ${F.length}`}</span><button class="o2-rexp" type="button" aria-label="${ui('expand')}" title="${ui('expand')}"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M9.5 2.5h4v4M13.5 2.5 9 7M6.5 13.5h-4v-4M2.5 13.5 7 9" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div>
      <p class="o2-rcap">${esc(f.cap)}</p>
      <p class="o2-rmeta">${f.done} of ${total} tasks done</p>
      <div class="o2-rtrack">${F.map((x,k)=>`<i style="left:${F.length>1?(k/(F.length-1)*100).toFixed(2):0}%"></i>`).join('')}<input class="o2-rscrub" type="range" min="0" max="${F.length-1}" value="${i}" step="1" aria-label="Scrub through progress"></div>
    </section>`;
  }

  function threadModel(sc){
    const goal = sc.goal || null;
    const threads = (sc.threads || []).filter(t=>t && Array.isArray(t.workstreams));
    return threads.length ? {goal, threads} : null;
  }
  const TSTATE = { done:'done', active:'running', review:'running', blocked:'blocked', failed:'blocked', queued:'queued' };
  function threadsView(sc, TM){
    const wsById = new Map(sc.workstreams.map(w=>[w.id, w]));
    const attById = new Map(sc.attention.filter(a=>a.id).map(a=>[a.id, a]));
    const openAsk = a => !sc.finished && a && a.kind!=='fyi';
    if (state.o2who && (!sc.members[state.o2who] || sc.members[state.o2who].killed)) state.o2who = null;
    const who = state.o2who || null;
    const stOf = w => { if(w.state!=='done' && w.state!=='blocked'){ const m=sc.members[w.owners[0]]; if(m && m.status==='waiting_on') return 'blocked'; } return TSTATE[w.state]||'queued'; };
    const all = []; const used = new Set();
    const runningWs = [], doneWs = [], queuedWs = [];
    const seenWs = new Set();
    const collectWs = w => {
      if(seenWs.has(w.id)) return;
      seenWs.add(w.id);
      const s = stOf(w);
      all.push(s);
      if(s==='running' || s==='blocked') runningWs.push({w, st:s});
      else if(s==='done') doneWs.push(w);
      else queuedWs.push(w);
    };
    const rows = TM.threads.map(t=>{
      const ws = t.workstreams.map(id=>wsById.get(id)).filter(Boolean).filter(w=>!who || w.owners.includes(who));
      ws.forEach(w=>{ used.add(w.id); collectWs(w); });
      const asks = (t.attention||[]).map(id=>attById.get(id)).filter(openAsk).filter(a=>!who || a.from===who);
      const n = {done:0, running:0, blocked:0, queued:0}; ws.forEach(w=>{ n[stOf(w)]++; });
      const state = asks.length ? 'needs' : n.blocked ? 'blocked' : (n.running ? 'running' : (n.queued && !n.done ? 'queued' : (n.queued ? 'running' : 'done')));
      return {t, ws, asks, n, state};
    }).filter(r=>r.ws.length || r.asks.length);
    // Asks no thread lists (system alerts included) still need to be seen.
    const listed = new Set(TM.threads.flatMap(t=>t.attention||[]));
    const loose = sc.attention.filter(openAsk).filter(a=>!a.id || !listed.has(a.id)).filter(a=>!who || a.from===who);
    if(loose.length) rows.push({t:{id:'t-asks', title:'Needs you', goal:'', now:''}, ws:[], asks:loose, n:{done:0,running:0,blocked:0,queued:0}, state:'needs'});
    const rest = sc.workstreams.filter(w=>!used.has(w.id) && (!who || w.owners.includes(who)));
    if(rest.length){ const n={done:0,running:0,blocked:0,queued:0}; rest.forEach(w=>{ collectWs(w); n[stOf(w)]++; }); rows.push({t:{id:'t-other', title: TM.threads.length ? 'Other' : 'Tasks', goal:'', now:''}, ws:rest, asks:[], n, state: n.running||n.blocked ? 'running' : (n.queued ? 'queued' : 'done')}); }
    const order = {needs:0, blocked:1, running:2, queued:3, done:4};
    rows.sort((a,b)=>order[a.state]-order[b.state]);
    runningWs.sort((a,b)=>(a.st==='blocked'?0:1)-(b.st==='blocked'?0:1));
    const tot = {done:0,running:0,blocked:0,queued:0}; all.forEach(s=>tot[s]++);
    const LABEL = {needs:'Needs you', blocked:'Blocked', running:'Moving', queued:'Not started', done:'Done'};
    const ICON = {done:'✓', running:'', blocked:'', queued:''};
    const goalCopy = TM.goal || {};
    const goalTitle = goalCopy.title || clipWords(sc.headline, 9);
    const g = `<section class="o2-goal">
      <div class="o2-glbl">${ui('goal')}</div>
      <b>${esc(goalTitle)}</b>
      ${goalCopy.line ? `<p>${esc(goalCopy.line)}</p>` : ''}
    </section>`;
    const threadSummaryFallback = rows.filter(r=>r.state!=='done' && r.t.now).map(r=>r.t.now).slice(0,2).join(' · ');
    const openThreads = rows.filter(r=>r.state!=='done').length;
    const openAsks = rows.reduce((acc,r)=>acc+r.asks.length, 0);
    const runHtml = runningCard(sc, runningWs, threadSummaryFallback, goalTitle, goalCopy.line||'');
    const progHtml = progressCard(sc, tot, doneWs, queuedWs, openThreads, openAsks);
    const thread = r => { const id=r.t.id; const open = state['o2t:'+id] ?? (r.state==='needs' || r.state==='blocked');
      const sum = r.n.done+r.n.running+r.n.blocked+r.n.queued;
      const asks = r.asks.map(a=>{ const c=itemCopy(a.id, a.title, a.why); return `<div class="o2-ask"><span class="o2-askd" aria-hidden="true"></span><div><b>${esc(c.title)}</b><p>${esc(c.line)}</p></div><button class="o2-btn" data-draft="@${esc(a.from)} re #${a.seq}: ">${ui('reply')}</button></div>`; }).join('');
      const tasks = r.ws.slice().sort((a,b)=>({blocked:0,running:1,queued:2,done:3}[stOf(a)]-({blocked:0,running:1,queued:2,done:3}[stOf(b)]))).map(w=>{ const s=stOf(w); const c=itemCopy(w.id, w.title, w.note); return `<li class="${s}"><span class="o2-ti ${s}" aria-label="${s}">${ICON[s]}</span><span class="o2-tt">${esc(c.title)}</span>${taskProgressPill(w)}${workerBadges(w.owners, 2, sc)}</li>`; }).join('');
      return `<section class="o2-th ${r.state} ${open?'open':''}">
        <button class="o2-thh" data-o2t="${esc(id)}" aria-expanded="${open}">
          <span class="o2-thd ${r.state}" aria-hidden="true"></span>
          <span class="o2-tht"><b>${esc(r.t.title)}</b>${LABEL[r.state] !== r.t.title ? `<em>${LABEL[r.state]}</em>` : ''}</span>
          <span class="o2-thc">${sum ? `${miniBar(r.n)}<span>${r.n.done}/${sum}</span>` : `<span>${r.asks.length}</span>`}</span><span class="o2-chev" aria-hidden="true"></span>
        </button>
        ${r.t.goal ? `<p class="o2-thg">${esc(r.t.goal)}</p>` : ''}
        ${r.t.now && r.state!=='done' ? `<p class="o2-thn"><span>${ui('now')}</span>${esc(r.t.now)}</p>` : (r.t.now ? `<p class="o2-thn done">${esc(r.t.now)}</p>` : '')}
        ${asks}
        ${open ? `<ul class="o2-tasks">${tasks}</ul>` : ''}
      </section>`; };
    return {goal:g, running:runHtml, progress:progHtml, reel: reelCard(sc), threads: rows.map(thread).join('')};
  }

  /* ---------- Members view: Board-style separated rows with Bees tiles ---------- */
  // Shared remote board: where the data comes from and whether it is current.
  function sourceChip(src, onlyProblems){
    if(!src || (onlyProblems && src.state==='live')) return '';
    const owners = Array.isArray(src.owners) ? src.owners : [];
    const label = src.label || 'Remote';
    const text = {live:`${label} · ${owners.length} machine${owners.length===1?'':'s'}`, stale:`${label} · reconnecting`, connecting:`${label} · connecting`, error:`${label} · unavailable`}[src.state] || label;
    const tip = [`Shared ${label} board ${src.swarm_id||''}`, src.db||'', ...owners.map(o=>o&&o.label?String(o.label):''), src.error?`Error: ${src.error}`:''].filter(Boolean).join('\n');
    return `<div class="o2-src ${esc(src.state||'')}" data-testid="swarm-source" title="${esc(tip)}"><i aria-hidden="true"></i>${esc(text)}</div>`;
  }
  const agoText = ms => { const m=Math.round(ms/60000); if(m<1) return 'seen just now'; if(m<60) return `last seen ${m}m ago`; const hr=Math.round(m/60); return hr<48 ? `last seen ${hr}h ago` : `last seen ${Math.round(hr/24)}d ago`; };
  // Owner machine, last seen (for members on another machine) and channels.
  function memberTags(x){
    const tags = [];
    if(x.owner) tags.push(`<span class="o2-mtag" data-testid="swarm-member-owner" title="${esc(`Runs on ${x.owner.label}`)}">${esc(x.owner.label.includes('@') ? x.owner.label : '@'+x.owner.label)}</span>`);
    if(!x.local && x.last_seen) tags.push(`<span class="o2-mtag" data-testid="swarm-member-seen">${esc(agoText(Date.now()-x.last_seen))}</span>`);
    const chans = (x.channels||[]).filter(c=>c && c!=='main');
    if(chans.length) tags.push(`<span class="o2-mtag" data-testid="swarm-member-channels">${esc(chans.includes('*') ? 'all channels' : chans.map(c=>'#'+c).join(' '))}</span>`);
    return tags.length ? `<div class="o2-mtags">${tags.join('')}</div>` : '';
  }

  function membersView(sc){
    state.style = 'bees';
    const fmtMod = m => String(m||'').replace(/-(?:dropdown|high|medium)$/i,'');
    const allEntries = Object.entries(sc.members).filter(([,x])=>!x.system);
    const lead = sc.members.coordinator || {rawName:'coordinator', role:'coordinator', title:'Coordinator', status:'parked', now:'', posts:0};
    const workers = allEntries.filter(([n])=>n!=='coordinator');
    const liveWorkers = workers.filter(([,x])=>!x.killed);
    const retiredWorkers = workers.filter(([,x])=>x.killed).sort((a,b)=>(b[1].posts||0)-(a[1].posts||0));
    const isAct = x => !sc.finished && (x.status==='needs_you' || x.status==='working' || x.status==='waiting_on');
    const actOrder = {needs_you:0, working:1, waiting_on:2, parked:3};
    const activeWorkers = liveWorkers.filter(([,x])=>isAct(x)).sort((a,b)=>actOrder[a[1].status]-actOrder[b[1].status] || (b[1].posts||0)-(a[1].posts||0));
    const standbyWorkers = liveWorkers.filter(([,x])=>!isAct(x)).sort((a,b)=>((b[1].last?.seq||0)-(a[1].last?.seq||0)) || a[0].localeCompare(b[0]));

    const searchIcon = `<svg class="icon" width="15" height="15" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.25" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="m10.25 10.25 3.25 3.25" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`;
    const topBar = `${sourceChip(sc.source)}<div class="o2-mtop">
      <label class="o2-msearch">${searchIcon}<input type="search" class="o2-msin" placeholder="Search members" aria-label="Search members" autocomplete="off" value="${esc(state.o2mq||'')}"></label>
    </div>`;

    const leftBeeTile = (n, x) => {
      const st = sc.finished || x.killed ? '' : x.status;
      const dot = (st==='working' || st==='needs_you' || st==='waiting_on') ? `<i class="o2-mavd ${st}" aria-hidden="true"></i>` : '';
      return `<span class="o2-mav bee" style="${hexBG(n)}" aria-hidden="true">${spriteTag(n, x.killed?'landed':(sc.finished?'landed':x.status))}${dot}</span>`;
    };

    const rowHtml = (n, x) => {
      const isCoord = n==='coordinator';
      const st = (sc.finished || x.killed) ? 'parked' : x.status;
      const metaParts = [];
      const mod = fmtMod(x.model);
      if(mod) metaParts.push(mod);
      if(x.last && x.last.seq) metaParts.push('#'+x.last.seq);
      else if(x.posts) metaParts.push(x.posts + (x.posts===1?' post':' posts'));
      const meta = metaParts.join(' · ');

      let statTag = '';
      if(isCoord){
        statTag = st==='working' ? `<span class="o2-mstat info">Coordinator · Working</span>` : `<span class="o2-mstat">Coordinator</span>`;
      } else if(x.killed){
        statTag = x.title ? `<span class="o2-mstat">${esc(x.title)}</span>` : `<span class="o2-mstat">Retired</span>`;
      } else if(st==='needs_you'){
        statTag = `<span class="o2-mstat warn">Needs you</span>`;
      } else if(st==='working'){
        statTag = `<span class="o2-mstat info">Working</span>`;
      } else if(st==='waiting_on' && x.waiting_on){
        statTag = `<span class="o2-mstat warn">→ @${esc(x.waiting_on)}</span>`;
      } else if(x.title){
        statTag = `<span class="o2-mstat">${esc(x.title)}</span>`;
      }

      let bodyLine = isCoord ? (x.now || memberLine('coordinator', x)) : (x.now || '');
      if(!isCoord && st==='needs_you' && x.ask){
        const ac = itemCopy(x.ask.id, x.ask.title, x.ask.why);
        bodyLine = ac.line ? `${ac.title} — ${ac.line}` : ac.title;
      }
      const showRoleSub = !isCoord && !x.killed && (st==='needs_you' || st==='working' || st==='waiting_on') && x.title &&
        !(bodyLine === x.title || bodyLine === `${n}: ${x.title}` || bodyLine === `${n} — ${x.title}`);

      const actBtn = (!isCoord && st==='needs_you' && x.ask)
        ? `<button type="button" class="o2-btn xs vis" data-draft="@${esc(n)} re #${x.ask.seq}: ">${ui('reply')}</button>`
        : '';

      const hay = `${n} ${n} ${x.title||''} ${x.model||''} ${bodyLine||''} ${st}`;
      const openAttr = `data-open-member="${esc(x.rawName || n)}"`;
      const cls = `o2-mrow bees ${st} ${x.killed?'retired':''} ${isCoord?'coord':''}`;

      return `<article class="${cls}" ${openAttr} data-mhay="${esc(hay)}">
        ${leftBeeTile(n, x)}
        <div class="o2-mcol">
          <header class="o2-mhead">
            <span class="o2-mwho"><span class="o2-mauth">@${esc(isCoord?'coordinator':n)}</span>${statTag}</span>
            <span class="o2-mright">${meta?`<span class="o2-mts">${esc(meta)}</span>`:''}${actBtn}</span>
          </header>
          ${showRoleSub ? `<div class="o2-mrole">${esc(x.title)}</div>` : ''}
          ${memberTags(x)}
          ${bodyLine ? `<div class="o2-mnow">${esc(bodyLine)}</div>` : ''}
        </div>
      </article>`;
    };

    const groupHtml = (label, list) => list.length
      ? `<section class="o2-mgroup"><h3 class="o2-msec"><span>${label}</span><em>${list.length}</em></h3><div class="o2-mlist">${list.map(([n,x])=>rowHtml(n,x)).join('')}</div></section>`
      : '';

    const retOpen = !!state.o2mRetired;
    const retiredHtml = retiredWorkers.length
      ? `<section class="o2-mgroup o2-mretgrp ${retOpen?'open':''}">
          <button type="button" class="o2-mret" data-o2="mRetired" aria-expanded="${retOpen}">
            <span>Retired</span><em>${retiredWorkers.length}</em><span class="o2-chev" aria-hidden="true"></span>
          </button>
          ${retOpen ? `<div class="o2-mlist">${retiredWorkers.map(([n,x])=>rowHtml(n,x)).join('')}</div>` : ''}
        </section>`
      : '';

    return `<div class="o2 o2-members">
      ${topBar}
      <section class="o2-mgroup o2-mcoord">${rowHtml('coordinator', lead)}</section>
      ${groupHtml('Active', activeWorkers)}
      ${groupHtml(sc.finished ? 'Team' : 'Standing by', standbyWorkers)}
      ${retiredHtml}
    </div>`;
  }

  // First run: no narrator view and no worker has posted yet. Shows how a swarm
  // works with the swarm's own bees (placeholders until workers join).
  function onboardView(sc){
    const real = people(sc).filter(([n,x])=>n!=='coordinator' && !x.killed).map(([n])=>n).slice(0,3);
    const ws = [...real, ...['worker-a','worker-b','worker-c']].slice(0,3);
    const Y = [30, 78, 126];
    const path = (y,i) => `<path id="o2-onb-p${i}" d="M84 78 C150 78 160 ${y} 226 ${y}"/>`;
    const dot = (i, fill, back, begin) => `<circle r="3" fill="${fill}" opacity="0"><animateMotion dur="2.4s" begin="${begin}s" repeatCount="indefinite" keyPoints="${back?'1;0':'0;1'}" keyTimes="0;1" calcMode="linear"><mpath href="#o2-onb-p${i}"/></animateMotion><animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.15;0.85;1" dur="2.4s" begin="${begin}s" repeatCount="indefinite"/></circle>`;
    const bee = (n, cls, style) => `<span class="o2-mav bee ${cls}" style="${hexBG(n)};${style}">${spriteTag(n,'working')}</span>`;
    const icon = n => `<span class="o2-onb-i" style="${hexBG(n)}"><img src="${beeImg(n)}" alt="" width="22" height="18"></span>`;
    return `<section class="o2-onb" data-testid="swarm-onboarding">
      <div class="o2-onb-stage" aria-hidden="true">
        <svg class="o2-onb-paths" viewBox="0 0 320 156" width="320" height="156">
          <g class="o2-onb-lines">${Y.map(path).join('')}</g>
          ${ws.map((n,i)=>dot(i, tcol('coordinator'), false, i*0.8) + dot(i, tcol(n), true, i*0.8+1.2)).join('')}
        </svg>
        ${bee('coordinator', 'o2-onb-q', 'left:24px;top:56px')}
        ${ws.map((n,i)=>bee(n, `o2-onb-w s${i}`, `left:236px;top:${Y[i]-22}px`)).join('')}
      </div>
      <h2 class="o2-onb-h">Your swarm is starting</h2>
      <ol class="o2-onb-steps">
        <li>${icon('coordinator')}<div><b>One goal</b><span>The coordinator splits it into tasks.</span></div></li>
        <li>${icon(ws[0])}<div><b>Many workers</b><span>Each takes a task, and they run in parallel.</span></div></li>
        <li>${icon(ws[1])}<div><b>One board</b><span>Updates land here. You step in only when asked.</span></div></li>
      </ol>
      <p class="o2-onb-wait"><span class="animate-shimmer-text">Waiting for the first update</span></p>
    </section>`;
  }

  function overviewView(sc){
    state.style = 'bees';
    const hot = sc.finished ? [] : sc.attention.filter(a=>a.kind!=='fyi');
    const open = sc.workstreams.filter(w=>w.state!=='done');
    const items = [];
    hot.forEach(a=>items.push({id:a.id||('ask-'+a.seq), title:a.title, line:a.why, owner:a.from, owners:[a.from], why:{kind:'you', ask:a}, group:'blocked'}));
    open.forEach(w=>{
      let why=null;
      if(w.state==='blocked') why={kind:'agent', who:w.owners[0], on:w.blocked_on||null, seq:w.seq};
      else if(w.state==='active'){ for(const o of w.owners){ const m=sc.members[o]; if(m && m.status==='waiting_on'){ why={kind:'agent', who:o, on:m.waiting_on, seq:m.waiting_seq}; break; } } }
      items.push({id:w.id, title:w.title, line:w.note, owner:w.owners[0], owners:w.owners, why, group: why ? 'blocked' : (w.state==='queued' ? 'planned' : 'running')});
    });
    if (state.o2who && (!sc.members[state.o2who] || sc.members[state.o2who].killed)) state.o2who = null;
    const who = state.o2who || null;
    const mine = list => who ? list.filter(i=>(i.owners||[i.owner]).includes(who)) : list;
    const G = k => mine(items.filter(i=>i.group===k));
    const running=G('running');

    const M = n => sc.members[n] || {status:'parked', now:''};
    const art = (n) => { const x=M(n); return spriteTag(n, x.killed ? 'landed' : (sc.finished?'landed':x.status)); };
    const tile = (n, cls) => `<span class="o2-tile ${cls||''} bees" style="${hexBG(n)}">${art(n)}</span>`;

    // The team (Bees only, no style picker)
    const lead = M('coordinator');
    const crew = [...people(sc)].filter(([n,x])=>n!=='coordinator' && !x.killed)
      .sort((a,b)=>ORDER[a[1].status]-ORDER[b[1].status]);
    const MAXT = 5; let shown = crew.length>MAXT+1 ? crew.slice(0,MAXT) : crew;
    if(who && !shown.some(([n])=>n===who)){ const sel=crew.find(([n])=>n===who); if(sel) shown=[...shown.slice(0,MAXT-1), sel]; }
    const extra = crew.length-shown.length;
    const trio = crew.slice(0,3).map(([n])=>n);
    const swarmArt = `<span class="o2-swarm">${trio.map((n,i)=>`<span class="o2-sb s${i}">${spriteTag(n, sc.finished?'landed':'working')}</span>`).join('')}</span>`;
    const allTile = `<button class="o2-m all ${who?'':'on'}" aria-pressed="${!who}" title="All workers" data-o2who=""><span class="o2-tile sm all bees">${swarmArt}</span><span>All</span></button>`;
    const team = `<section class="o2-team">
      <h3 class="o2-h">${ui('team')}</h3>
      <div class="o2-lead">${tile('coordinator','lg')}<div class="o2-lb"><b>${ui('lead')}</b><p>${esc(memberLine('coordinator', lead))}</p></div></div>
      ${crew.length ? `<div class="o2-crew" role="group" aria-label="Filter by worker">${allTile}${shown.map(([n,x])=>`<button class="o2-m ${sc.finished?'':x.status} ${who===n?'on':''}" aria-pressed="${who===n}" title="${esc(n)}: ${esc(memberLine(n,x))}" data-o2who="${esc(n)}">${tile(n,'sm')}<i class="o2-st" aria-hidden="true"></i><span>@${esc(n)}</span></button>`).join('')}${extra>0?`<button class="o2-m more" id="o2More"><span class="o2-tile sm plus">+${extra}</span><span>&nbsp;</span></button>`:''}</div>` : ''}
    </section>`;

    const card = it => { const c=itemCopy(it.id, it.title, it.line); const y=it.why;
      const btn = it.group==='blocked' && y && y.kind==='you' ? `<button class="o2-btn" data-draft="@${esc(y.ask.from)} re #${y.ask.seq}: ">${ui('reply')}</button>`
        : it.group==='blocked' && y && y.on ? `<button class="o2-btn" data-draft="@coordinator please unblock @${esc(y.who)}, waiting on @${esc(y.on)} since #${y.seq||''}">${ui('open')}</button>` : '';
      return `<div class="o2-card">${tile(it.owner,'md')}<div class="o2-cb"><b>${esc(c.title)}</b><p>${esc(c.line)}</p><div class="o2-who">@${esc(it.owner)}${btn}</div></div></div>`; };
    const branch = (k, list, none, limit) => { const lim=state['o2'+k]?list.length:limit; const vis=list.slice(0,lim); const more=list.length-vis.length;
      return `<section class="o2-br ${k}"><h3 class="o2-h"><i></i>${ui(k)}<em>${list.length}</em></h3>
        ${list.length ? `<div class="o2-cards">${vis.map(card).join('')}</div>${more>0?`<button class="o2-more" data-o2="${k}">${ui('more').replace('{n}',more)}</button>`:''}` : `<p class="o2-none">${ui(none)}</p>`}</section>`; };

    if(sc.starting) return `<div class="o2">${sourceChip(sc.source, true)}${onboardView(sc)}${team}</div>`;
    // One layout for every swarm: goal, running, done, timeline, threads, the
    // team. Without narrator or plan threads, the tasks sit in one group.
    // Builds without the built-in Overview keep the card layout until a
    // narrator writes threads.
    const TM = threadModel(sc);
    if(!TM && !sc.builtin){
      const stOf = w => { if(w.state!=='done' && w.state!=='blocked'){ const m=sc.members[w.owners[0]]; if(m && m.status==='waiting_on') return 'blocked'; } return TSTATE[w.state]||'queued'; };
      const tot = {done:0, running:0, blocked:0, queued:0};
      const run = [], done = [], queued = [];
      sc.workstreams.filter(w=>!who || w.owners.includes(who)).forEach(w=>{ const st=stOf(w); tot[st]++; if(st==='running'||st==='blocked') run.push({w, st}); else if(st==='done') done.push(w); else queued.push(w); });
      const asks = mine(hot.map(a=>({id:a.id||('ask-'+a.seq), title:a.title, line:a.why, owner:a.from, owners:[a.from], why:{kind:'you', ask:a}, group:'blocked'})));
      return `<div class="o2">${sourceChip(sc.source, true)}${asks.length ? branch('blocked', asks, 'none_blocked', 3) : ''}${runningCard(sc, run, '', '', '') || branch('running', running, 'none_running', 3)}${progressCard(sc, tot, done, queued, null, asks.length)}${reelCard(sc)}${team}</div>`;
    }
    const v = threadsView(sc, TM || {goal: sc.goal, threads: []});
    return `<div class="o2">${sourceChip(sc.source, true)}${v.goal}${v.running}${v.progress}${v.reel}<section class="o2-threads"><h3 class="o2-h">${ui('threads')}</h3>${v.threads}</section>${team}</div>`;
  }

  /* ---------- render ---------- */
  function render(el, view) {
    const sc = S.live;
    if (!sc) return;
    const v = view === 'cards' ? 'cards' : 'overview';
    const active = document.activeElement;
    const typing = active && active.classList && active.classList.contains('o2-msin') && el.contains(active)
        ? {start: active.selectionStart, end: active.selectionEnd} : null;
    el.innerHTML = v === 'cards' ? membersView(sc) : overviewView(sc);
    el.dataset.view = v;
    paintSprites(el);
    requestAnimationFrame(() => snapBees(el));
    bind(el);
    if (typing) {
      const inp = el.querySelector('.o2-msin');
      if (inp) { inp.focus({preventScroll: true}); inp.setSelectionRange(typing.start, typing.end); }
    }
  }

  // Full view of the progress timeline: the whole screenshot (not cropped),
  // every frame in a filmstrip, arrow keys to step, Esc to close. Lives on
  // <body> so the 2 s re-render of the Overview does not tear it down.
  function closeReel() { const lb = $('.o2-tv'); if (lb) (lb.closeReel || (() => lb.remove()))(); }

  function openReel(F, start, onMove) {
    closeReel();
    if (!F.length) return;
    const back = document.activeElement;
    const lb = document.createElement('div');
    lb.className = 'o2-tv'; lb.setAttribute('role', 'dialog'); lb.setAttribute('aria-modal', 'true'); lb.setAttribute('aria-label', ui('progress'));
    lb.innerHTML = `<div class="o2-tvh"><b>${ui('progress')}</b><span class="o2-tvpos"></span><span class="o2-tvwhen"></span><button class="o2-tvx" type="button" aria-label="Close">\u2715</button></div>
      <div class="o2-tvstage"><div class="o2-tvscroll"><img class="o2-tvimg" alt=""></div><button class="o2-tvnav prev" type="button" aria-label="Previous">\u2039</button><button class="o2-tvnav next" type="button" aria-label="Next">\u203a</button></div>
      <div class="o2-tvtext"><p class="o2-tvcap"></p><p class="o2-tvmeta"></p></div>
      <div class="o2-tvstrip" role="listbox" aria-label="Frames">${F.map((f, k) => `<button class="o2-tvth" type="button" role="option" data-k="${k}" aria-label="${esc(`${k + 1}. ${f.cap || ''}`)}"><img src="${esc(f.src)}" alt="" loading="lazy"><span>${k + 1}</span></button>`).join('')}</div>`;
    document.body.appendChild(lb);
    const img = $('.o2-tvimg', lb), scroller = $('.o2-tvscroll', lb), strip = $('.o2-tvstrip', lb);
    let i = -1;
    // Only real steps re-render the card; opening and pressing past either end
    // leave it alone (a re-render would detach the opener and drop zoom).
    const go = (k, opening) => {
      const n = Math.max(0, Math.min(F.length - 1, k));
      if (n === i) return;
      i = n; const f = F[i];
      scroller.classList.remove('zoom');
      img.src = f.src; img.alt = f.cap || '';
      $('.o2-tvpos', lb).textContent = i === F.length - 1 ? `${ui('now')} \u00b7 ${i + 1} / ${F.length}` : `${i + 1} / ${F.length}`;
      $('.o2-tvwhen', lb).textContent = f.when || '';
      $('.o2-tvcap', lb).textContent = f.cap || '';
      $('.o2-tvmeta', lb).textContent = f.total ? `${f.done} of ${f.total} tasks done` : '';
      $('.o2-tvnav.prev', lb).disabled = i === 0; $('.o2-tvnav.next', lb).disabled = i === F.length - 1;
      strip.querySelectorAll('.o2-tvth').forEach((b, k2) => { const on = k2 === i; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); if (on) b.scrollIntoView({block: 'nearest', inline: 'center'}); });
      if (onMove && !opening) onMove(i);
    };
    // The Overview may have re-rendered since this opened, so focus falls back
    // to the live expand button when the opener is gone.
    const close = () => {
      document.removeEventListener('keydown', key, true); lb.remove();
      const to = back && back.isConnected ? back : $('.o2-reel .o2-rexp');
      if (to && to.focus) to.focus({preventScroll: true});
    };
    lb.closeReel = close;
    const key = e => {
      if (e.key === 'Escape') { e.preventDefault(); close(); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); go(i - 1); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); go(i + 1); }
      else if (e.key === 'Home') { e.preventDefault(); go(0); }
      else if (e.key === 'End') { e.preventDefault(); go(F.length - 1); }
    };
    document.addEventListener('keydown', key, true);
    $('.o2-tvx', lb).onclick = close;
    $('.o2-tvnav.prev', lb).onclick = () => go(i - 1);
    $('.o2-tvnav.next', lb).onclick = () => go(i + 1);
    strip.querySelectorAll('.o2-tvth').forEach(b => b.onclick = () => go(+b.dataset.k));
    // Click the picture to see it at full size; click again to fit.
    img.onclick = () => scroller.classList.toggle('zoom');
    lb.onclick = e => { if (e.target === lb || e.target === scroller) close(); };
    go(start, true);
    lb.tabIndex = -1; lb.focus({preventScroll: true});
  }

  function bind(el) {
    const hooks = (window.SwarmHive && window.SwarmHive.hooks) || {};
    el.querySelectorAll('.o2-reel').forEach(r => {
      let F = [];
      try { F = JSON.parse(r.dataset.reel || '[]'); } catch (_) {}
      const img = r.querySelector('.o2-rimg'), scrub = r.querySelector('.o2-rscrub'), sw = r.querySelector('.o2-rswipe'),
          line = r.querySelector('.o2-rline'), cmp = r.querySelector('.o2-rcmp');
      const show = i => {
        const f = F[i]; if (!f) return;
        img.src = f.src; img.alt = f.cap;
        r.querySelector('.o2-rcap').textContent = f.cap;
        r.querySelector('.o2-rwhen').textContent = f.when;
        r.querySelector('.o2-rmeta').textContent = r.querySelector('.o2-rmeta').textContent.replace(/^\d+/, f.done);
        r.querySelector('.o2-rtag').textContent = i === F.length - 1 ? 'Now' : `${i + 1} / ${F.length}`;
        state.o2reel = i;
      };
      const clip = () => {
        const on = cmp.getAttribute('aria-pressed') === 'true';
        img.style.clipPath = on ? `inset(0 ${100 - sw.value}% 0 0)` : 'none';
        line.style.left = sw.value + '%';
      };
      scrub.oninput = () => show(+scrub.value);
      sw.oninput = () => { state.o2reelSwipe = +sw.value; clip(); };
      if (state.o2reelCmp) clip();
      // Steps go through state and a re-render: a poll may have replaced this
      // card's nodes while the viewer is open.
      const open = () => openReel(F, state.o2reel ?? F.length - 1, i => { state.o2reel = i === F.length - 1 ? null : i; sync(); });
      r.querySelector('.o2-rexp').onclick = (e) => { e.stopPropagation(); open(); };
      img.onclick = () => { if (cmp.getAttribute('aria-pressed') !== 'true') open(); };
      cmp.onclick = () => {
        const on = cmp.getAttribute('aria-pressed') !== 'true';
        state.o2reelCmp = on;
        cmp.setAttribute('aria-pressed', on);
        cmp.textContent = on ? 'Hide compare' : 'Compare with now';
        sw.hidden = !on; line.hidden = !on; clip();
      };
    });
    el.querySelectorAll('[data-o2t]').forEach(b => b.onclick = (e) => {
      e.stopPropagation(); state['o2t:' + b.dataset.o2t] = b.getAttribute('aria-expanded') !== 'true'; sync();
    });
    el.querySelectorAll('[data-o2who]').forEach(b => b.onclick = (e) => {
      e.stopPropagation(); const n = b.dataset.o2who; state.o2who = (n && state.o2who !== n) ? n : null; sync();
    });
    el.querySelectorAll('[data-o2]').forEach(b => b.onclick = (e) => {
      e.stopPropagation(); const k = 'o2' + b.dataset.o2; state[k] = !state[k]; sync();
    });
    const om = $('#o2More', el);
    if (om) om.onclick = () => { if (hooks.members) hooks.members(); };
    el.querySelectorAll('[data-open-member]').forEach(b => b.onclick = (e) => {
      if (e.target.closest('button,[data-draft]')) return;
      if (hooks.member) hooks.member(b.dataset.openMember);
    });
    el.querySelectorAll('.o2-msin').forEach(inp => {
      const apply = () => {
        const q = inp.value.trim().toLowerCase();
        state.o2mq = inp.value;
        el.querySelectorAll('.o2-mrow').forEach(row => {
          row.style.display = (!q || (row.dataset.mhay || '').toLowerCase().includes(q)) ? '' : 'none';
        });
        el.querySelectorAll('.o2-mgroup').forEach(grp => {
          grp.style.display = (!q || [...grp.querySelectorAll('.o2-mrow')].some(r => r.style.display !== 'none')) ? '' : 'none';
        });
      };
      inp.oninput = apply;
      if (inp.value) apply();
    });
    el.querySelectorAll('[data-draft]').forEach(b => b.onclick = (e) => {
      e.stopPropagation(); if (hooks.draft) hooks.draft(b.dataset.draft);
    });
  }

  /* ================= adapter: plugin snapshot -> view model ================= */
  function toModel(snap){
    const byName = new Map(snap.members.map(m=>[m.name,m]));
    const isRole = n => { const m=byName.get(n); return !!(m && (m.roleWorker || m.narrator)); };
    const leadM = snap.members.find(m=>m.role==='coordinator');
    const L = leadM ? leadM.name : 'coordinator';
    const key = n => n===L ? 'coordinator' : n;
    window.SwarmHive = window.SwarmHive || {}; window.SwarmHive.leadName = L;
    const msgs = snap.messages.filter(m=>!isRole(m.agent) && !(m.control && typeof m.control==='object'));
    P = msgs.map(m=>({ s:m.seq, a:key(m.agent), t:new Date(m.ts || Date.now()).toISOString(),
      to:(m.all?['*']:(m.mentions||[]).filter(n=>n!==m.agent && !isRole(n)).map(key)),
      x:String(m.text||'').replace(/\s+/g,' ').trim().slice(0,400) }));
    const head = snap.head || (P.length?P[P.length-1].s:0);
    const narratorView = snap.view && snap.view.json && typeof snap.view.json==='object' ? snap.view.json : null;
    // Without a narrator view, the coordinator's plan.json supplies the goal,
    // the threads (lists of worker names) and each worker's task name. Live
    // status still comes from the board.
    const plan = !narratorView && snap.plan && typeof snap.plan==='object' ? snap.plan : null;
    const planTasks = plan && plan.tasks && typeof plan.tasks==='object' ? plan.tasks : {};
    const vj = narratorView || (plan ? {
      goal: plan.goal,
      threads: (Array.isArray(plan.threads)?plan.threads:[]).filter(t=>t && t.title && Array.isArray(t.tasks))
          .map((t,i)=>({id: t.id || ('t'+i), title: t.title, goal: t.goal || '', workstreams: t.tasks.filter(n=>typeof n==='string').map(n=>n===L?'coordinator':n)})),
    } : {});
    const team = snap.members.filter(m=>!m.roleWorker && !m.narrator);
    const cleanPath = str => String(str||'').replace(/(?:~\/\.gemini\/[\w.-]+\/brain|\/(?:usr\/local\/google\/home|google\/src\/cloud))\/[^\s`'")]+/g, m=>{
      const parts=m.split('/').filter(Boolean); return parts.length>=2 ? '…/'+parts.slice(-2).join('/') : m;
    });
    const clipW = (str, maxW) => {
      const w = String(str||'').replace(/\s+/g,' ').trim().split(' ').filter(Boolean);
      return w.length > maxW ? w.slice(0, maxW).join(' ').replace(/[,;:.]+$/,'') + '…' : w.join(' ').replace(/[.:]+$/,'');
    };
    const briefOf = n => { const re=new RegExp('^@'+n.replace(/[.*+?^${}()|[\]\\-]/g,'\\$&')+'\\b'); const p=msgs.find(m=>m.agent===L && re.test(String(m.text||''))); if(!p) return '';
      let s=String(p.text).replace(/^@\S+[\s,:]*/,'').replace(/\*\*([^*]+)\*\*/g,'$1').split(/(?<=[.!?])\s|\n/)[0];
      s=s.replace(/\s*\((?:you\b|your\b)[^)]*\)/gi,'').replace(/\s*\((?:you\b|your\b)[^)]*$/gi,'');
      s=s.replace(/^(?:you\s+(?:are|own|handle|drive|run|lead|manage)|your\s+(?:role|job|task|assignment|goal|focus)\s+is(?:\s+to)?|assignment\s*:|role\s*:|task\s*:)\s*/i,'');
      s=s.replace(/^(?:turning|making|building|auditing|fixing)\s+(?:the\s+)?/i, m=>m.charAt(0).toUpperCase()+m.slice(1));
      s=s.replace(/^(?:the|a|an|this\s+swarm's)\s+/i,'');
      s=clipW(cleanPath(s), 8);
      return s ? s.charAt(0).toUpperCase()+s.slice(1) : ''; };
    const lastPostBy = n => { for(let i=P.length-1;i>=0;i--) if(P[i].a===n) return P[i]; return null; };
    const KINDS=['decision','failure','review','stuck','quota','error','fyi'];
    const attention = (Array.isArray(vj.attention)?vj.attention:[]).filter(a=>a&&a.title).map(a=>{
      const refs=Array.isArray(a.refs)?a.refs:[]; const seqRef=refs.find(r=>r&&r.seq); const memRef=refs.find(r=>r&&r.member&&byName.has(r.member)&&byName.get(r.member).role!=='coordinator');
      const p=seqRef?P.find(q=>q.s===seqRef.seq):null; const url=(Array.isArray(a.actions)?a.actions:[]).find(x=>x&&x.type==='url');
      return { id: a.id ? String(a.id) : null, kind:KINDS.includes(a.kind)?a.kind:'fyi', severity:a.severity||'low', title:String(a.title).slice(0,160), why:String(a.why||'').slice(0,300), from: memRef?key(memRef.member):(p?p.a:'coordinator'), seq: (seqRef && Number(seqRef.seq)) || head, url: url?url.target:null }; });
    (Array.isArray(snap.systemAttention)?snap.systemAttention:[]).forEach(s=>{ if(s && s.member && !isRole(s.member)) attention.push({id: String(s.id || `sys-${s.member}-${s.kind}`), kind: s.kind==='quota'?'quota':s.kind==='stuck'?'stuck':'error', severity:'high', title: s.kind==='quota'?`@${s.member} ran out of quota`:s.kind==='stuck'?`@${s.member} looks stuck`:`@${s.member} hit an error`, why: String(s.text||s.detail||'').slice(0,200), from:key(s.member), seq:head}); });
    // Short copy the narrator writes next to each item (see swarm_narrator: `short`, `now`).
    (()=>{ const items={}, members={}; const sh=x=>x&&typeof x.short==='object'&&x.short?{title:String(x.short.title||'').slice(0,60), line:String(x.short.line||'').slice(0,90)}:null;
      (Array.isArray(vj.attention)?vj.attention:[]).forEach(a=>{ const s=sh(a); if(s&&a.id) items[String(a.id)]=s; });
      (Array.isArray(vj.workstreams)?vj.workstreams:[]).forEach(w=>{ const s=sh(w); if(s&&w.id) items[String(w.id)]=s; });
      if(vj.now && typeof vj.now==='object') Object.entries(vj.now).forEach(([n,v])=>{ if(typeof v==='string') members[n===L?'coordinator':n]=v.slice(0,48); });
      window.SwarmHive = window.SwarmHive || {}; window.SwarmHive.viewCopy = {items, members, lead: members.coordinator?{doing:members.coordinator}:{} }; })();
    const askByFrom = new Map();
    const SEV = {high:0, med:1, medium:1, low:2};
    attention.sort((a,b)=>(SEV[a.severity]??2)-(SEV[b.severity]??2));
    attention.filter(a=>a.kind!=='fyi').forEach(a=>{ if(!askByFrom.has(a.from)) askByFrom.set(a.from, a); });
    const needsYou = new Set(askByFrom.keys());
    const members = {};
    team.forEach(m=>{ const n=key(m.name); const lp=lastPostBy(n);
      let status = m.killed ? 'parked' : (m.status==='working' ? 'working' : 'parked'); let waiting_on=null, waiting_seq=null;
      if(status==='parked' && !m.killed && lp && lp.to.length && !lp.to.includes('*') && /\?|could you|can you|please|ping me|waiting on/i.test(lp.x)){ const t=lp.to[0]; if(!P.some(q=>q.s>lp.s && q.a===t)){ status='waiting_on'; waiting_on=t; waiting_seq=lp.s; } }
      if(needsYou.has(n) && !m.killed) status='needs_you';
      const rawNow = (m.status==='working' && m.activity) ? String(m.activity) : (lp ? lp.x.replace(/^(@[A-Za-z0-9_-]+[\s,:]*)+/,'') : 'No posts yet');
      const now = cleanPath(rawNow).replace(/`([^`]+)`/g,'$1').slice(0,180);
      const ask = !m.killed ? (askByFrom.get(n) || null) : null;
      members[n] = { rawName:m.name, role:m.role, title: n==='coordinator' ? 'Coordinator' : ((typeof planTasks[m.name]==='string' && planTasks[m.name].trim()) ? String(planTasks[m.name]).trim().slice(0,120) : (briefOf(m.name) || '')), model:m.model||'', status, waiting_on, waiting_seq, waiting_for:'', now, ask, steps:m.steps||0, posts:m.posts||0, killed:!!m.killed, last: lp?{seq:lp.s,time:lp.t,text:cleanPath(lp.x)}:null,
        owner: m.owner && m.owner.label ? {label:String(m.owner.label), kind:String(m.owner.kind||'')} : null, local: m.local!==false,
        last_seen: Number.isFinite(m.last_seen) ? m.last_seen : null, channels: Array.isArray(m.channels) ? m.channels.map(String) : [] }; });
    if(!members.coordinator) members.coordinator = { rawName:L, role:'coordinator', title:'Coordinator', model:'', status:'parked', now:'', ask:null, steps:0, posts:0, last:null };
    snap.members.filter(m=>m.roleWorker||m.narrator).forEach(m=>{ members['~'+m.name]={system:true, status:'parked', model:m.model||'', title:m.role||'role'}; });
    const STATES=['queued','active','blocked','review','done','failed'];
    const parseProg = (w)=>{
      if(w.progress && typeof w.progress==='object' && Number(w.progress.total)>0){
        const tot=Math.max(1,Number(w.progress.total)||1), dn=Math.max(0,Math.min(tot,Number(w.progress.done)||0));
        return {done:dn, total:tot, label:String(w.progress.label||(dn+'/'+tot)).slice(0,24)};
      }
      if(typeof w.progress==='number' && w.progress>=0 && w.progress<=100){
        const p=Math.round(w.progress); return {done:p, total:100, label:p+'%'};
      }
      const fm=/(?<!cl\/)(?<!\d\.)\b(\d{1,2})\s*(?:\/|of)\s*(\d{1,2})\b(?!\s*(?:AM|PM|am|pm))/.exec(String(w.note||''));
      if(fm){ const d=+fm[1], t=+fm[2]; if(t>=2 && t<=60 && d<=t) return {done:d, total:t, label:`${d}/${t}`}; }
      return null;
    };
    let workstreams = (Array.isArray(vj.workstreams)?vj.workstreams:[]).filter(w=>w&&w.title).map((w,i)=>{ const seqs=(Array.isArray(w.refs)?w.refs:[]).map(r=>r&&r.seq).filter(Boolean); const rawState=STATES.includes(w.state)?w.state:'active';
      return { id:w.id||('w'+i), title:String(w.title).slice(0,120), owners:(Array.isArray(w.owners)?w.owners:[]).filter(o=>byName.has(o)&&!isRole(o)).map(key), state: rawState==='failed'?'blocked':rawState, rawState, progress:parseProg(w), note:String(w.note||'').slice(0,200), seq: seqs.length?Math.max(...seqs):head }; });
    workstreams.forEach(w=>{ if(!w.owners.length) w.owners=['coordinator']; });
    // Without a narrator view: killed, or parked after posting, is done; waiting on a
    // peer is blocked; only a worker that has not started yet is up next.
    const fallbackState = (x, lp) => x.killed ? 'done' : x.status==='waiting_on' ? 'blocked' :
        (x.status==='working' || x.status==='needs_you') ? 'active' : lp ? 'done' : 'queued';
    if(!workstreams.length) workstreams = team.filter(m=>m.role!=='coordinator').map(m=>{ const n=key(m.name); const lp=lastPostBy(n); return {id:n, title: (typeof planTasks[m.name]==='string' && planTasks[m.name].trim()) ? String(planTasks[m.name]).slice(0,120) : (members[n].title||'Task'), owners:[n], state: fallbackState(members[n], lp), blocked_on: members[n].waiting_on||null, note: members[n].now, seq: lp?lp.s:head}; });
    Object.entries(members).forEach(([n,x])=>{
      if(!x.system && n!=='coordinator' && !x.title){
        const ow = workstreams.filter(w=>w.owners.includes(n));
        if(ow.length) x.title = clipW(ow[ow.length-1].title, 7);
      }
    });
    const done = workstreams.filter(w=>w.state==='done').length;
    const anyWorking = Object.values(members).some(x=>!x.system && x.status==='working');
    const lastP = P[P.length-1];
    const finished = !anyWorking && !attention.some(a=>a.kind!=='fyi') && !!lastP && lastP.a==='coordinator' && /^\[done\]/i.test(lastP.x);
    const nowIso = new Date().toISOString();
    // Plan threads have no narrator line: use what the first working member in
    // the thread last said, clipped.
    const planNow = ids => { const x = ids.map(n=>members[n]).find(m=>m && !m.killed && m.status==='working'); return x ? clipW(x.now, 8) : ''; };
    return { title: snap.title||'Untitled swarm',
      headline: vj.headline ? String(vj.headline).slice(0,220) : (plan && vj.goal && vj.goal.line) ? String(vj.goal.line).slice(0,220) : (lastP ? `@${lastP.a}: ${lastP.x.replace(/^(@[A-Za-z0-9_-]+[\s,:]*)+/,'').slice(0,140)}` : 'Waiting for the first post.'),
      phase: vj.phase && vj.phase.label ? {label:String(vj.phase.label).slice(0,40), index:Number(vj.phase.index)||1, total:Number(vj.phase.total)||1} : {label: finished?'Done':'In progress', index:1, total:1},
      done, total: workstreams.length||1, as_of: head, board_head: head, now: nowIso, started: P.length?P[0].t:nowIso, narrator_seq: Number(vj.as_of_seq)||head, finished,
      builtin: snap.builtinOverview === true,
      starting: snap.builtinOverview === true && !finished && !narratorView && !attention.length && !P.some(p=>p.a!=='coordinator'),
      latest: lastP?{agent:lastP.a,seq:lastP.s,time:lastP.t,text:lastP.x}:{agent:'coordinator',seq:0,time:nowIso,text:''},
      attention, workstreams, members,
      source: snap.source && typeof snap.source==='object' ? snap.source : null,
      // Progress reel (UI work only): frames the narrator picked; images come from /api/frame.
      reel: (()=>{ const fr=(vj.reel && Array.isArray(vj.reel.frames) && vj.reel.frames.length) ? vj.reel.frames : (Array.isArray(snap.frames) ? snap.frames : []); const conv=snap.conversationId||''; const tok=new URLSearchParams(location.search).get('token')||'';
        const out=fr.filter(f=>f && typeof f.path==='string' && f.path.startsWith('/')).slice(0,40).map(f=>({ src:`/api/frame?conversationId=${encodeURIComponent(conv)}&path=${encodeURIComponent(f.path)}&seq=${Number(f.seq)||0}${tok?`&token=${encodeURIComponent(tok)}`:''}`, seq:Number(f.seq)||0, caption:String(f.caption||'').slice(0,90) }));
        return out.length>=2 ? out : null; })(),
      goal: vj.goal && typeof vj.goal==='object' && vj.goal.title ? {title:String(vj.goal.title).slice(0,80), line:String(vj.goal.line||'').slice(0,120)} : null,
      fromPlan: !!plan,
      runningSummary: vj.running_summary ? String(vj.running_summary).slice(0,200) : '',
      threads: (Array.isArray(vj.threads)?vj.threads:[]).filter(t=>t&&t.title&&Array.isArray(t.workstreams)).slice(0,12).map(t=>({id:String(t.id||t.title).slice(0,48), title:String(t.title).slice(0,40), goal:String(t.goal||'').slice(0,100), now:String(t.now||(plan ? planNow(t.workstreams) : '')).slice(0,80), state:String(t.state||''), workstreams:t.workstreams.map(String), attention:(Array.isArray(t.attention)?t.attention:[]).map(String)})),
    };
  }


  const ROOTS = new Map();   // container element -> {root, view, etag}
  function sync() { ROOTS.forEach((r, el) => { const tab = el.closest('.tab-body'); if (r.root.isConnected && (!tab || tab.classList.contains('active'))) render(r.root, r.view); }); }
  window.SwarmHive = Object.assign(window.SwarmHive || {}, {
    // Agent color shared with the Board avatars, so a name has one color everywhere.
    colorOf(n) { return tcol(n === (window.SwarmHive.leadName || 'coordinator') ? 'coordinator' : n); },
    // Board avatar: the member's bee on its tile color (the coordinator wears the crown).
    avatarHtml(name) {
      const n = name === (window.SwarmHive.leadName || 'coordinator') ? 'coordinator' : String(name || '?');
      return `<span class="msg-av bee" style="${hexBG(n)}" aria-hidden="true"><img src="${beeImg(n)}" alt="" width="22" height="18"></span>`;
    },
    // Render the swarm into `el` as `opts.view` ('overview' | 'cards').
    update(snap, el, opts) {
      if (!snap || !el) return;
      const view = (opts && opts.view) || 'overview';
      window.SwarmHive.embedded = true;
      let r = ROOTS.get(el);
      if (!r || !r.root.isConnected || r.root.parentNode !== el) {
        el.innerHTML = '';
        const root = document.createElement('div');
        root.className = 'pane embedded';
        el.appendChild(root);
        r = {root, view};
        ROOTS.set(el, r);
      }
      if (snap.id !== S.swarm) {   // another swarm: drop filters, selections and open sections
        closeReel();
        for (const k of Object.keys(state)) delete state[k];
        state.view = 'overview';
        S.swarm = snap.id;
        ROOTS.forEach((x) => { x.etag = ''; });
      }
      if (r.etag && r.etag === snap.etag && r.view === view) return;   // unchanged: keep animations running
      r.view = view;
      const tab = el.closest('.tab-body');
      if (tab && !tab.classList.contains('active')) return;   // hidden: render when its tab opens
      try {
        if (S.etag !== snap.etag) { S.live = toModel(snap); S.etag = snap.etag; }
        r.etag = snap.etag;
        render(r.root, view);
      } catch (e) { console.warn('SwarmHive', e); }
    },
  });
})();
