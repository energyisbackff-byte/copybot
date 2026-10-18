/* Zetrax bot room: a live, animated 3D (isometric) picture of what the bot is doing (served at /room.js, data from /api/room).
   Rule: nothing moves without a reason. Every drive, bubble and number comes from the bot's own records:
   - a buy proposal travels the real gate order of paper.Paper.on_buy: rules -> storm -> contract check (CA) -> quote ->
     exit test -> vault. Where the bot refused it, the coin stops at that desk and the sweeper takes it to the bin.
   - a sale leaves the vault for the reason the bot recorded (stop-loss, take-profit, time limit, trader sold...).
   - duties follow real timers: bookkeeping when a new equity mark appears (every 5 min), the exit check every 30 s
     while positions are open, the trader board when the bot re-selects, the radar turns only while the wallet stream
     is connected, the heart monitor beats when the bot's heartbeat file is touched.
   - the CT desk sleeps because that feed is not built yet. When the bot is offline, the whole room sleeps.
   The robots drive on wheels along real paths around the furniture (grid path-finding), fast.
   Read-only: the room never sends anything to the bot. */
(function(){"use strict";
// world: X runs to the lower right, Y to the lower left, Z up. Back walls at Y=0 (right) and X=0 (left).
const RX=270,RY=190,WH=92,K=.7,VX0=-6,VY0=-WH-6,VW=RX+12,VH=WH+RY*K+14,SPEED=240,WSOL="So11111111111111111111111111111111111111112";
// a flat front view (the back wall faces the screen, no side walls): depth (Y) is drawn upward at K, height (Z) straight up
const iso=(x,y,z=0)=>[x,y*K-z];
const P={g:"#00ff9c",gd:"#00b36d",c:"#00e5ff",a:"#ffb000",r:"#ff3b5c",mu:"#5f8f7b",tx:"#c8ffe6",gold:"#ffd23f",paper:"#eef8f3",wood:"#8a5a33",
  scr:"#02150e",ink:"#05110c"};
const BOOKCOL={copy:P.g,consensus:P.c,trend:P.a,"trend-live":P.r};
const DEF={
  chief:{name:"CHIEF",role:"runs the room, real-money switch",col:"#f1f1f1",eye:"#ffd23f",shape:"ghost",home:[244,120]},
  scout:{name:"SCOUT",role:"finds traders, watches their wallets",col:"#3fa9ff",eye:"#7ff6ff",shape:"blob",home:[54,158]},
  guard:{name:"GUARD",role:"rules, limits, stop-loss",col:"#ff9f1a",eye:"#ff5d3b",shape:"hex",home:[94,100]},
  vet:{name:"VET",role:"checks each coin's contract (CA)",col:"#d9e4ff",eye:"#7ff6ff",shape:"dome",home:[133,70]},
  trader:{name:"TRADER",role:"quotes, buys and sells",col:"#2bdc7a",eye:"#d6ffe9",shape:"blob",home:[190,110]},
  chart:{name:"CHART",role:"Trend Rider: SOL daily",col:"#00c8e0",eye:"#00ff9c",shape:"square",home:[64,38]},
  sweeper:{name:"SWEEPER",role:"housekeeping: bin, books, retries",col:"#ff4fb8",eye:"#ffe0f3",shape:"tri",home:[74,168]},
  ct:{name:"CT",role:"reads Crypto Twitter",col:"#ff3b5c",eye:"#ffd0d8",shape:"square",home:[26,126]}};
const ORDER=["chief","scout","guard","vet","trader","chart","sweeper","ct"];
const NEAR={guard:[108,102],vet:[147,72],trader:[176,112],exit:[218,12],shredder:[105,141],ledger:[133,142],board:[170,16],rules:[68,105]};
// furniture footprints [x0,y0,x1,y1] = obstacles for the path-finder
const DESKS=[{k:"chart",x:30,y:6,w:50,d:16,lbl:"TREND"},{k:"vet",x:100,y:40,w:40,d:16,lbl:"CA CHECK"},{k:"chief",x:214,y:92,w:30,d:16,lbl:"CHIEF"},
  {k:"guard",x:64,y:72,w:40,d:16,lbl:"RULES"},{k:"trader",x:160,y:80,w:40,d:16,lbl:"TRADE"},{k:"ct",x:4,y:98,w:30,d:16,lbl:"CT"}];
const OBST=DESKS.map(k=>[k.x,k.y,k.x+k.w,k.y+k.d]).concat([[238,4,266,28],[8,149,40,181],[96,150,114,166],[124,152,142,166],[160,150,262,172]]);
const PAD=5,CELL=4,GW=Math.ceil(RX/CELL),GH=Math.ceil(RY/CELL);
function blocked(x,y){if(x<7||y<7||x>RX-7||y>RY-7)return true;for(const o of OBST)if(x>o[0]-PAD&&x<o[2]+PAD&&y>o[1]-PAD&&y<o[3]+PAD)return true;return false}
let GRID=null;
function grid(){if(!GRID){GRID=new Uint8Array(GW*GH);for(let j=0;j<GH;j++)for(let i=0;i<GW;i++)GRID[j*GW+i]=blocked(i*CELL+CELL/2,j*CELL+CELL/2)?1:0}return GRID}
function los(a,b){const dx=b[0]-a[0],dy=b[1]-a[1],n=Math.ceil(Math.hypot(dx,dy)/2);for(let i=1;i<n;i++)if(blocked(a[0]+dx*i/n,a[1]+dy*i/n))return false;return true}
function findPath(a,b){
  // A* on a 4-unit grid (8 directions, no corner cutting), then string-pulled so the robots drive in straight lines
  if(los(a,b))return [b];
  const G=grid(),ci=p=>Math.max(0,Math.min(GW-1,Math.floor(p[0]/CELL))),cj=p=>Math.max(0,Math.min(GH-1,Math.floor(p[1]/CELL)));
  const s=cj(a)*GW+ci(a),e=cj(b)*GW+ci(b),gs=new Float32Array(GW*GH).fill(1e9),from=new Int32Array(GW*GH).fill(-1),closed=new Uint8Array(GW*GH);
  const ex=e%GW,ey=(e/GW)|0,h=n=>{const dx=Math.abs(n%GW-ex),dy=Math.abs(((n/GW)|0)-ey);return Math.max(dx,dy)+.414*Math.min(dx,dy)};
  gs[s]=0;const open=[s];let found=false,guard=0;
  while(open.length&&guard++<20000){
    let bi=0,bf=1e9;for(let i=0;i<open.length;i++){const f=gs[open[i]]+h(open[i]);if(f<bf){bf=f;bi=i}}
    const c=open[bi];open[bi]=open[open.length-1];open.pop();if(c===e){found=true;break}closed[c]=1;
    const cx=c%GW,cy=(c/GW)|0;
    for(let dy=-1;dy<=1;dy++)for(let dx=-1;dx<=1;dx++){if(!dx&&!dy)continue;const nx=cx+dx,ny=cy+dy;if(nx<0||ny<0||nx>=GW||ny>=GH)continue;const n=ny*GW+nx;
      if(closed[n]||(G[n]&&n!==e))continue;if(dx&&dy&&(G[cy*GW+nx]||G[ny*GW+cx]))continue;
      const ng=gs[c]+(dx&&dy?1.414:1);if(ng<gs[n]){if(gs[n]>=1e9)open.push(n);gs[n]=ng;from[n]=c}}
  }
  if(!found)return [b];
  const cells=[];for(let n=e;n!==-1&&n!==s;n=from[n])cells.push([(n%GW)*CELL+CELL/2,((n/GW)|0)*CELL+CELL/2]);cells.reverse();cells.push(b);
  const out=[];let cur=a,i=0;
  while(i<cells.length){let j=cells.length-1;while(j>i&&!los(cur,cells[j]))j--;out.push(cells[j]);cur=cells[j];i=j+1}
  return out;
}
const shade=(hex,f)=>{const n=parseInt(hex.slice(1),16),t=f<0?0:255,a=Math.abs(f);let r=n>>16,g=n>>8&255,b=n&255;
  r=Math.round(r+(t-r)*a);g=Math.round(g+(t-g)*a);b=Math.round(b+(t-b)*a);return "#"+((1<<24)|(r<<16)|(g<<8)|b).toString(16).slice(1)};
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fm=v=>v==null?"--":(v<0?"-$":"$")+Math.abs(v).toFixed(2);
const fsm=v=>v==null?"--":(v<0?"-$":"+$")+Math.abs(v).toFixed(2);
const fp=(v,d=1)=>v==null?"--":(v>=0?"+":"")+(100*v).toFixed(d)+"%";
const fpp=(v,d=0)=>v==null?"--":(100*v).toFixed(d)+"%";
const fk=v=>v==null?"--":v>=1e6?"$"+(v/1e6).toFixed(1)+"M":v>=1e3?"$"+Math.round(v/1e3)+"k":"$"+Math.round(v);
const coin=m=>m===WSOL?"SOL":m?m.slice(0,4)+"…"+m.slice(-4):"?";
const coin3=m=>m===WSOL?"SOL":String(m||"?").slice(0,3).toUpperCase();
const dur=s=>s==null?"--":s<90?Math.round(s)+"s":s<5400?Math.round(s/60)+"m":s<172800?Math.floor(s/3600)+"h"+String(Math.floor(s%3600/60)).padStart(2,"0")+"m":Math.round(s/86400)+"d";
const hhmm=t=>{const d=new Date(t*1000);return String(d.getUTCHours()).padStart(2,"0")+":"+String(d.getUTCMinutes()).padStart(2,"0")};
const dday=t=>new Date(t*1000).toLocaleDateString("en-GB",{day:"numeric",month:"short",timeZone:"UTC"});
const EXITNAME={stop_loss:"stop-loss",take_profit:"take-profit",max_hold:"time limit",trader_sold:"trader sold",trend_break:"trend exit",
  write_off:"written off",panic:"panic",strategy_replaced:"v1 closed"};
// refusal reasons from paper.py / veto.py / trend.py in plain words
function plain(r){
  r=String(r||"");let m;
  if(/^passed/i.test(r))return "passed every check";
  if((m=r.match(/^liquidity \$([\d,]+) < \$([\d,]+)/)))return `liquidity only $${m[1]} (need $${m[2]})`;
  if((m=r.match(/top 10 holders own (\d+%)/)))return `the top 10 holders own ${m[1]} of the coin`;
  if(/creator can still mint/.test(r))return "the creator can still mint more coins";
  if(/holders can be frozen/.test(r))return "the creator can freeze holders' coins";
  if((m=r.match(/([\d.]+%) transfer fee/)))return `it takes a ${m[1]} fee on every transfer`;
  if(/transfer hook/.test(r))return "it runs extra code on every transfer (hook)";
  if(/permanentDelegate/.test(r))return "someone can move holders' coins (permanent delegate)";
  if(/nonTransferable|pausableConfig/.test(r))return "transfers can be blocked";
  if(/veto: check failed/.test(r))return "the contract check could not finish, so no buy (safe default)";
  if(/mint not found|not an SPL token|zero supply/.test(r))return r.replace(/^safety veto: /,"");
  if((m=r.match(/stale signal: seen (\d+)s/)))return `too late: we saw the trade ${m[1]}s after it happened`;
  if(/already holding/.test(r))return "we already hold this coin";
  if(/max open positions/.test(r))return "the book already has its maximum open positions";
  if(/coin exposure cap/.test(r))return "too much of this coin across the books already";
  if((m=r.match(/below \$(\d+) minimum/)))return `too small: below the $${m[1]} minimum`;
  if(/below minimum/.test(r))return "not enough cash for a minimum order";
  if((m=r.match(/costs \$([\d.]+) > (\d+%)/)))return `trading costs ($${m[1]}) would eat more than ${m[2]} of the planned risk`;
  if((m=r.match(/price impact ([\d.]+%) > ([\d.]+%)/)))return `our buy would move the price ${m[1]} (max ${m[2]})`;
  if(/no Jupiter route/i.test(r))return "no route to buy it on Jupiter";
  if((m=r.match(/buy then sell loses ([\d.]+%)/)))return `buying then selling right away loses ${m[1]}`;
  if(/no sell route/.test(r))return "no way to sell it back";
  if((m=r.match(/SOL -?([\d.]+%) in 1h/)))return `SOL fell ${m[1]} within an hour, so buying is paused`;
  if(/storm/.test(r))return "SOL is falling fast, so buying is paused";
  if(/^late:/.test(r))return "too late: "+r.slice(5).trim();
  if(/panic/.test(r))return "the PANIC stop is on";
  if(/paused/.test(r))return "this book is paused";
  if(/switched off/.test(r))return "this book is switched off";
  if(/drawdown/.test(r))return "the drawdown switch is tripped";
  if(/daily loss/.test(r))return "today's loss limit is reached";
  if(/weekly loss/.test(r))return "this week's loss limit is reached";
  return r.replace(/^(safety veto|FILL FAILED|sell check|market storm): ?/i,"");
}
const say=(b,text,ms,o)=>({t:"say",b,text,ms:ms||3000,o:o||{}}),walk=(b,to)=>({t:"walk",b,to}),wait=ms=>({t:"wait",ms}),
  doit=fn=>({t:"do",fn}),par=(...steps)=>({t:"par",steps}),seq=(...steps)=>({t:"seq",steps});
const CSS=`.rm{display:flex;flex-direction:column}.rm-cards{order:9;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin-top:8px}
.rm-tagline{order:1}.rm-stage{order:2}.rm-cap{order:3}.rm-recent{order:4}
.rm-card{border:1px solid rgba(0,255,156,.16);border-left:3px solid var(--bc);background:rgba(0,0,0,.35);padding:4px 7px 5px;min-width:0}
.rm-n{font-weight:800;letter-spacing:.5px;color:var(--bc);font-size:11.5px}
.rm-v{display:block;font-weight:600;color:#c8ffe6;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;letter-spacing:0;font-variant-numeric:tabular-nums}
.rm-rl{color:#5f8f7b;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rm-m{height:3px;background:rgba(255,255,255,.07);margin:4px 0 3px;position:relative;overflow:hidden}.rm-m i{position:absolute;inset:0 auto 0 0;width:0;background:var(--bc)}
.rm-q{font-size:10.5px;color:#c8ffe6;opacity:.82;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-height:1.3em}
.rm-stage{position:relative;margin:0 auto;background:#020605;border:1px solid rgba(0,255,156,.2);overflow:hidden;max-width:100%}
.rm-stage canvas{position:absolute;inset:0;width:100%;height:100%}
.rm-bub{position:absolute;left:0;top:0;width:max-content;max-width:min(178px,62%);padding:3px 6px 4px;font-size:10px;line-height:1.32;background:rgba(1,12,8,.94);
 border:1px solid var(--bc);color:#dfffee;pointer-events:none;opacity:0;visibility:hidden;z-index:2;box-shadow:0 0 10px rgba(0,0,0,.6)}
.rm-bub.on{opacity:1;visibility:visible;transition:opacity .18s}.rm-bub b{color:var(--bc);font-weight:800;margin-right:3px}.rm-bub.bad{border-color:#ff3b5c}.rm-bub.good{border-color:#00ff9c}
.rm-bub:after{content:"";position:absolute;left:var(--tail,50%);bottom:-5px;width:7px;height:7px;background:inherit;border-right:1px solid;border-bottom:1px solid;
 border-color:inherit;transform:translateX(-50%) rotate(45deg)}
.rm-bub.below:after{bottom:auto;top:-5px;transform:translateX(-50%) rotate(225deg)}
.rm-tagline{align-self:center;margin-bottom:5px;font-size:10px;font-weight:800;letter-spacing:.5px;padding:1px 6px;color:#00e5ff;border:1px solid #00e5ff;display:none}
.rm-banner{position:absolute;left:0;right:0;bottom:0;z-index:3;padding:3px 6px;font-weight:800;font-size:10.5px;text-align:center;display:none;background:rgba(0,0,0,.85)}
.rm-banner.bad{color:#ff3b5c;border-top:1px solid #ff3b5c}.rm-banner.warn{color:#ffb000;border-top:1px solid #ffb000}
.rm-cap{margin-top:7px;font-size:12px;min-height:2.9em;line-height:1.4}.rm-cap .rm-h,.rm-t{color:#5f8f7b}.rm-cap b{font-weight:800}
.rm-tag{color:#00e5ff;font-size:10px;font-weight:800;border:1px solid currentColor;padding:0 3px;margin-right:2px}
.rm-recent{margin-top:5px;font-size:11px;color:#c8ffe6;border-top:1px dashed rgba(0,255,156,.16);padding-top:4px}
.rm-recent div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:1px 0}.rm-recent .g{color:#00ff9c}.rm-recent .r{color:#ff3b5c}.rm-recent .c{color:#00e5ff}
@media(max-width:699px){.rm-bub{font-size:9.5px;max-width:min(160px,56%);padding:2px 5px 3px}}
@media(min-width:700px){.rm-cards{order:0;margin:0 0 8px;grid-template-columns:repeat(4,minmax(0,1fr))}.rm-bub{font-size:11px;max-width:min(240px,46%)}}`;

const READ=.72;   // speech time factor
// where each robot goes to read the number it reports (idle status lines)
const SRC={chief:[252,36],scout:[170,16],guard:[210,142],vet:[105,141],trader:[184,142],chart:[98,28],sweeper:[133,142]};
const FONT="'JetBrains Mono','Fira Code',ui-monospace,Menlo,Consolas,monospace";
class Bot{
  constructor(k){this.k=k;this.d=DEF[k];this.x=this.d.home[0];this.y=this.d.home[1];this.path=[];this.carry=null;this.pose=null;this.face=0;this.bub=null;this.odo=0;this.sdx=0}
  goto(x,y,instant){
    if(instant){this.x=x;this.y=y;this.path=[];return}
    this.path=Math.hypot(x-this.x,y-this.y)<.5?[]:findPath([this.x,this.y],[x,y]);   // drives around the furniture
  }
  step(dt,sp){
    let left=SPEED*sp*dt;
    while(left>0&&this.path.length){const [tx,ty]=this.path[0],dx=tx-this.x,dy=ty-this.y,dd=Math.hypot(dx,dy);if(dd>.01)this.sdx=dx/dd;
      if(dd<=left){this.x=tx;this.y=ty;this.path.shift();left-=dd;this.odo+=dd}else{this.x+=dx/dd*left;this.y+=dy/dd*left;this.odo+=left;left=0}}
    this.face=this.path.length?Math.sign(this.sdx):0;
  }
}

class Room{
  constructor(root,o){
    this.root=root;this.o=Object.assign({duties:true,rounds:true,autoReplay:true,manual:false,preview:""},o||{});
    this.rm=!!(window.matchMedia&&matchMedia("(prefers-reduced-motion: reduce)").matches);
    this.motion=true;/* always moving (owner's choice) */this.t=0;this.d=null;this.recvAt=0;this.visible=true;this.onTick=null;
    this.reset();this.build();
    if(!this.o.manual){this.loop=this.loop.bind(this);requestAnimationFrame(this.loop)}
  }
  reset(){
    this.seen=new Set();this.queue=[];this.cur=null;this.bots={};for(const k of ORDER)this.bots[k]=new Bot(k);
    this.jars=[];this.items=[];this.fx=[];this.beats=[];this.blips=[];this.binCount=0;this.door=null;this.vetRows=null;
    this.shredAt=-1e9;this.ledgerAt=-1e9;this.vetAt=-1e9;this.boardAt=-1e9;this.windowAt=-1e9;this.antennaAt=-1e9;this.ringAt=-1e9;
    this.quotes={};this.recent=[];this.capHead="";this.lastIdle=-1e9;this.idleI=0;this.lastRound=this.t;this.idleBub=null;this.d=null;this.speed=1;
    if(this.recentEl){this.recentEl.innerHTML="";this.capEl.innerHTML=""}
  }
  // ---- DOM --------------------------------------------------------------------------------------------------
  build(){
    if(!document.getElementById("rm-css")){const s=document.createElement("style");s.id="rm-css";s.textContent=CSS;document.head.appendChild(s)}
    const r=this.root;
    r.classList.add("rm");r.innerHTML=`<div class="rm-cards"></div><div class="rm-tagline"></div><div class="rm-stage"><canvas role="img" aria-label="Animated room: each robot is one part of the bot, moving only when that part really works"></canvas><div class="rm-banner"></div></div><div class="rm-cap" aria-live="polite"></div><div class="rm-recent"></div>`;
    this.stage=r.querySelector(".rm-stage");this.cv=r.querySelector("canvas");this.ctx=this.cv.getContext("2d");this.bannerEl=r.querySelector(".rm-banner");
    this.tagEl=r.querySelector(".rm-tagline");this.capEl=r.querySelector(".rm-cap");this.recentEl=r.querySelector(".rm-recent");
    if(this.o.preview){this.tagEl.textContent=this.o.preview;this.tagEl.style.display="block"}
    const cards=r.querySelector(".rm-cards");this.cardEls={};
    cards.innerHTML=ORDER.map(k=>`<div class="rm-card" data-k="${k}" style="--bc:${DEF[k].col}"><div class="rm-n">${DEF[k].name}</div><span class="rm-v"></span><div class="rm-rl">${esc(DEF[k].role)}</div><div class="rm-m"><i></i></div><div class="rm-q"></div></div>`).join("");
    for(const el of cards.children)this.cardEls[el.dataset.k]={v:el.querySelector(".rm-v"),m:el.querySelector(".rm-m i"),q:el.querySelector(".rm-q")};
    this.bubEl={};for(const k of ORDER){const el=document.createElement("div");el.className="rm-bub";el.style.setProperty("--bc",DEF[k].col);this.stage.appendChild(el);this.bubEl[k]=el}
    this.resize();
    if(window.ResizeObserver)new ResizeObserver(()=>this.resize()).observe(r);else addEventListener("resize",()=>this.resize());
    if(window.IntersectionObserver)new IntersectionObserver(es=>{this.visible=es.some(e=>e.isIntersecting)}).observe(this.stage);
  }
  setMotion(){this.motion=true}
  // ---- clock ------------------------------------------------------------------------------------------------
  snow(){return this.d?this.d.now+(this.t-this.recvAt)/1000:Date.now()/1000}
  get awake(){return !this.d||this.d.online!==false}
  cfg(){return Object.assign({top10:.3,roundtrip:.06,max_age:120,min_liq:50000,max_impact:.03,exit_check_s:30,exits:{}},(this.d&&this.d.cfg)||{})}
  busy(){return !!(this.cur&&!this.cur.it.idle)||this.queue.some(q=>!q.idle)}
  push(it){
    // a real event always wins over an idle status trip: the trip stops and that robot drives straight home
    this.queue=this.queue.filter(q=>!q.idle);
    if(this.cur&&this.cur.it.idle){for(const k of ORDER){const b=this.bots[k],h=DEF[k].home;if(Math.hypot(b.x-h[0],b.y-h[1])>1)b.goto(h[0],h[1],!this.motion)}this.cur=null;this.clearBubbles()}
    this.queue.push(it);
  }
  loop(ts){
    requestAnimationFrame(this.loop);
    if(this.lastTs==null)this.lastTs=ts;
    const dt=Math.min(.1,(ts-this.lastTs)/1000);this.lastTs=ts;
    if(document.hidden||!this.visible)return;
    this.advance(dt*1000,ts-(this.lastDraw||0)>=(this.motion?33:250));
  }
  advance(ms,draw=true){
    const dt=ms/1000;this.t+=ms;
    if(this.onTick)this.onTick(this.t);
    this.speed=this.queue.length>3?2.2:this.queue.length>1?1.5:1;
    for(const k in this.bots)this.bots[k].step(dt,this.speed);
    this.tickSeq(dt);
    if(!this.cur&&!this.queue.length)this.idle();
    if(draw){this.lastDraw=this.lastTs;this.draw();this.place();this.banner()}
  }
  // ---- data in ------------------------------------------------------------------------------------------------
  update(d){
    const prev=this.d;this.d=d;this.recvAt=this.t;
    if(!prev){
      for(const e of d.events||[])this.seen.add(e.id);
      this.binCount=(d.today||{}).refused||0;this.syncJars();
      const hb=d.heartbeat_age;if(hb!=null&&hb<40)this.beats.push(this.t-hb*1000);
      const last=(d.events||[]).filter(x=>!String(x.kind).startsWith("act_")).slice(-1)[0];
      if(last&&this.o.autoReplay&&d.now-last.ts<900)this.enqueueEvent(last,{tag:"LATEST · "+dur(d.now-last.ts)+" ago"});
      this.renderCards();this.banner();return;
    }
    const all=(d.events||[]).filter(e=>!this.seen.has(e.id));all.forEach(e=>this.seen.add(e.id));
    const isAct=e=>String(e.kind).startsWith("act_"),fresh=all.filter(e=>!isAct(e));
    for(const e of all.filter(isAct).slice(-3))this.activity(e);
    if(fresh.length>6){const old=fresh.splice(0,fresh.length-2);this.push({build:()=>this.catchUpScript(old)})}
    for(const e of fresh)this.enqueueEvent(e,{tag:e.sample?"SAMPLE":""});
    const pv=prev;
    if(d.selection&&d.selection.at&&pv.selection&&d.selection.at!==pv.selection.at)this.push({build:()=>this.selectScript()});
    if(this.o.duties&&d.marks&&d.marks.last&&!d.marks.quiet&&pv.marks&&pv.marks.last&&d.marks.last!==pv.marks.last)this.push({build:()=>this.markScript()});
    const ts=(d.trend||{}).state,pts=(pv.trend||{}).state;if(ts&&ts.t&&pts&&pts.t&&ts.t!==pts.t)this.push({build:()=>this.dailyScript()});
    for(const p of d.pending_sells||[]){const q=(pv.pending_sells||[]).find(x=>x.book===p.book&&x.mint===p.mint);if(q&&p.tries>q.tries)this.push({build:()=>this.retryScript(p)})}
    if(Math.floor(d.now/86400)!==Math.floor(pv.now/86400))this.push({build:()=>this.newDayScript()});
    const n1=(d.ws||{}).notifications,n0=(pv.ws||{}).notifications;if(n1!=null&&n0!=null&&n1>n0)this.wsEvents(n1-n0);
    if(d.last_poll&&pv.last_poll&&d.last_poll!==pv.last_poll)this.pollPulse();
    const h1=d.heartbeat_age!=null?d.now-d.heartbeat_age:null,h0=pv.heartbeat_age!=null?pv.now-pv.heartbeat_age:null;
    if(h1&&h0&&h1>h0+1)this.beats.push(this.t-(d.now-h1)*1000);
    this.beats=this.beats.filter(b=>this.t-b<40000);
    this.renderCards();this.banner();
  }
  activity(e){
    // the bot's activity stream: transactions it saw, selection start, stream changes, errors
    const k=coin(e.mint);
    if(e.kind==="act_tx"){this.wsBlip();this.idleSay("scout",`@${e.trader} ${String(e.side||"tx").toUpperCase()}${e.usd!=null?" "+fm(e.usd):""} · ${k}`+(e.detect_s!=null?` · seen ${(+e.detect_s).toFixed(1)}s after the block`:"")+(e.side==="buy"?" → into the checks":""),3400,`@${e.trader} ${e.side||"tx"} ${k}`)}
    else if(e.kind==="act_select_start")this.push({build:()=>[walk("scout",NEAR.board),doit(()=>{this.boardAt=this.t}),say("scout","re-checking traders on their last 30 days (takes a few minutes; copying waits)",4200,{s:"re-checking traders…"})]});
    else if(e.kind==="act_ws")this.push({build:()=>[doit(()=>{this.ringAt=this.t}),say("scout",e.up?"live wallet stream reconnected":"live wallet stream dropped → backup polling until it is back",3600,{bad:!e.up,good:!!e.up,s:e.up?"stream back ✓":"stream down → polling"})]});
    else if(e.kind==="act_error")this.push({build:()=>[say("chief",`error while ${e.what||"working"}: ${e.msg||""} (the bot keeps running)`,4200,{bad:true,s:"error: "+(e.what||"")}),doit(()=>this.addRecent(e,`ERROR ${e.what||""}: ${e.msg||""}`,"r"))]});
  }
  replay(){const e=((this.d||{}).events||[]).filter(x=>!String(x.kind).startsWith("act_")).slice(-1)[0];if(e)this.enqueueEvent(e,{tag:"REPLAY · "+dur(this.snow()-e.ts)+" ago"});return !!e}
  enqueueEvent(e,o){
    const b=e.kind==="buy"?()=>this.buyScript(e):e.kind==="sell"?()=>this.sellScript(e):e.kind==="sell_failed"?()=>this.failScript(e):null;
    if(b)this.push({build:b,tag:(o&&o.tag)||(e.sample?"SAMPLE":""),ev:e});
  }
  // ---- step runner ------------------------------------------------------------------------------------------
  tickSeq(dt){
    for(let n=0;n<60;n++){
      if(!this.cur){
        if(!this.queue.length)return;
        const it=this.queue.shift();this.clearBubbles();this.idleBub=null;let steps=[];
        try{steps=it.build()||[]}catch(err){console.warn("room script",err)}
        this.cur={steps,i:-1,it};this.capHead=it.ev?`${dday(it.ev.ts)} ${hhmm(it.ev.ts)} UTC · ${it.ev.book}`:"now";this.next();continue;
      }
      if(this.cur.s&&!this.done(this.cur.s,dt))return;
      dt=0;this.next();
    }
  }
  next(){const c=this.cur;c.i++;if(c.i>=c.steps.length){this.cur=null;this.clearBubbles();return}c.s=c.steps[c.i];this.start(c.s)}
  start(s){
    if(s.t==="walk")this.bots[s.b].goto(s.to[0],s.to[1],!this.motion);
    else if(s.t==="say"){this.showSay(s.b,s.text,s.o);s.left=s.ms*READ}
    else if(s.t==="wait")s.left=s.ms;
    else if(s.t==="do"){try{s.fn()}catch(err){console.warn("room step",err)}}
    else if(s.t==="par")s.steps.forEach(x=>this.start(x));
    else if(s.t==="seq"){s.i=0;if(s.steps.length)this.start(s.steps[0])}
  }
  done(s,dt){
    if(s.t==="walk")return !this.bots[s.b].path.length;
    if(s.t==="say"||s.t==="wait"){s.left-=dt*1000*(s.t==="say"?Math.min(this.speed,1.6):this.speed);if(s.left>0)return false;if(s.t==="say"&&!s.o.keep)this.bots[s.b].bub=null;return true}
    if(s.t==="par"){let all=true;for(const x of s.steps)if(!x._done){if(this.done(x,dt))x._done=true;else all=false}return all}
    if(s.t==="seq"){while(s.i<s.steps.length){if(!this.done(s.steps[s.i],dt))return false;dt=0;s.i++;if(s.i<s.steps.length)this.start(s.steps[s.i])}return true}
    return true;
  }
  showSay(b,text,o){
    o=o||{};const tag=this.cur&&this.cur.it.tag;this.bots[b].bub={text,short:o.s,tag,tone:o.bad?"bad":o.good?"good":""};
    this.quotes[b]=text;this.updateCard(b);this.capLine(b,text,tag);
  }
  clearBubbles(){for(const k in this.bots)this.bots[k].bub=null}
  capLine(b,text,tag){
    this.capEl.innerHTML=`<span class="rm-h">${esc(this.capHead)}</span> ${tag?`<span class="rm-tag">${esc(tag)}</span> `:""}<b style="color:${DEF[b].col}">${DEF[b].name}</b> ${esc(text)}`;
  }
  addRecent(e,text,cls){
    this.recent.unshift({ts:e.ts,book:e.book,text,cls,tag:this.cur&&this.cur.it.tag});this.recent=this.recent.slice(0,4);
    this.recentEl.innerHTML=this.recent.map(r=>`<div><span class="rm-t">${esc(dday(r.ts))} ${hhmm(r.ts)}</span> ${r.tag&&!/^LATEST/.test(r.tag)?`<span class="rm-tag">${esc(r.tag.split(" ")[0])}</span> `:""}<span class="${r.cls}">${esc(r.text)}</span> <span class="rm-t">· ${esc(r.book)}</span></div>`).join("");
  }
  // ---- idle: only real duties and real status lines ----------------------------------------------------------
  idle(){
    if(!this.d)return;
    if(!this.awake){for(const k in this.bots)this.bots[k].pose="sleep";if(this.idleBub){this.bots[this.idleBub.b].bub=null;this.idleBub=null}return}
    for(const k in this.bots)if(this.bots[k].pose==="sleep")this.bots[k].pose=null;
    this.syncJars();this.binCount=(this.d.today||{}).refused||0;this.items=[];this.door=null;this.vetRows=null;
    if(this.idleBub&&this.t>this.idleBub.until){this.bots[this.idleBub.b].bub=null;this.idleBub=null}
    // the main loop can be busy for minutes (re-selecting traders); the heartbeat says the bot is alive
    const la=this.loopAge(),busy=la!=null&&la>240,sel=busy&&this.d.selection_due;
    for(const k of ORDER){const b=this.bots[k],h=sel&&k==="scout"?NEAR.board:DEF[k].home;if(!b.path.length&&Math.hypot(b.x-h[0],b.y-h[1])>1)b.goto(h[0],h[1],!this.motion)}
    if(busy){
      if(this.t-this.lastIdle>6500){this.lastIdle=this.t;
        this.idleSay(sel?"scout":"chief",sel?`re-checking traders: the main loop has been on it for ${dur(la)}, copying waits until it is done`:`the main loop has been quiet for ${dur(la)} (the heartbeat is fine)`,4600,sel?"re-checking traders…":`loop quiet ${dur(la)}`)}
      return;
    }
    const c=this.cfg();
    if(this.o.duties&&this.o.rounds&&this.jars.some(j=>j.pos)&&this.t-this.lastRound>c.exit_check_s*1000){this.lastRound=this.t;this.push({build:()=>this.roundScript()});return}
    if(this.t-this.lastIdle>6500){
      this.lastIdle=this.t;const L=this.statusLines();
      if(L.length){const [b,txt,sh]=L[this.idleI++%L.length];
        // the robot drives to where that number lives (safe, radar board, vault, bin, ledger...), reports it, drives back
        if(SRC[b]&&this.motion&&this.o.trips!==false)this.queue.push({idle:true,build:()=>[walk(b,SRC[b]),say(b,txt,3800,{s:sh}),walk(b,DEF[b].home)]});
        else this.idleSay(b,txt,4600,sh)}
    }
  }
  idleSay(b,txt,ms,short){
    if(this.cur||this.queue.length)return;
    if(this.idleBub)this.bots[this.idleBub.b].bub=null;
    this.bots[b].bub={text:txt,short,tone:""};this.idleBub={b,until:this.t+ms};this.quotes[b]=txt;this.updateCard(b);this.capHead="now";this.capLine(b,txt);
  }
  statusLines(){
    // [bot, full sentence (caption, wide rooms), short form (bubbles on a phone)] from the bot's own numbers
    const d=this.d,L=[],sel=d.selection||{},ws=d.ws||{},t=d.today||{},c=this.cfg(),E=c.exits||{},pk=(sel.picked||[]).filter(Boolean),lv=d.live||{};
    L.push(["chief",lv.armed?`REAL MONEY ${lv.mode==="trial"?"TRIAL":"ON"} until ${dday(lv.until)} · every check still applies`:
      "paper trading · real money is available and switched off"+(lv.safety_total?` · its safety checks ${lv.safety_ok}/${lv.safety_total}`:""),lv.armed?`REAL $ ${lv.mode==="trial"?"TRIAL":"ON"}`:"real $ available · off"]);
    L.push(["scout",pk.length?`watching ${pk.length} trader wallet${pk.length>1?"s":""}: ${pk.map(h=>"@"+h).join(" ")} · live stream ${ws.connected?"UP":"DOWN, backup polling"}`:"no trader passed the filters: watching nobody, holding cash",
      pk.length?`watching ${pk.length} wallet${pk.length>1?"s":""} · stream ${ws.connected?"UP":"DOWN"}`:"watching nobody (cash)"]);
    const pos=d.positions||[];
    if(pos.length){const w=pos.slice().sort((a,b)=>(a.ch??0)-(b.ch??0))[0];
      L.push(["guard",`${pos.length} open · weakest ${coin(w.mint)} ${w.ch==null?"(no price)":fp(w.ch)}`+(E.enabled?` · stop -${fpp(E.sl)} · target +${fpp(E.tp)}`:""),`${pos.length} open · weakest ${w.ch==null?coin(w.mint):fp(w.ch)}`])}
    else{const day=(d.books||[]).filter(b=>!b.live&&b.day_pnl!=null).reduce((s,b)=>s+b.day_pnl,0);L.push(["guard",`no open positions · today ${fsm(day)} across the paper books`,"no open positions"])}
    L.push(["vet",t.vet_checked?`today: checked ${t.vet_checked} coin contract${t.vet_checked>1?"s":""}, refused ${(t.by_station||{}).vet||0}`+this.topVet():"no coin reached the contract check yet today",
      t.vet_checked?`checked ${t.vet_checked} · refused ${(t.by_station||{}).vet||0}`:"nothing to check yet"]);
    L.push(["trader",`today: ${t.filled||0} bought · ${t.sold||0} sold`+((d.pending_sells||[]).length?` · ${(d.pending_sells||[]).length} sale retrying`:"")+` · ${t.refused||0} refused by the checks`,
      `today: ${t.filled||0} bought · ${t.sold||0} sold`]);
    const st=(d.trend||{}).state,smp=(d.trend||{}).sample?"SAMPLE · ":"";
    if(st&&st.ready){const v=st.signal?"buy signal":st.uptrend?"uptrend, waiting for a breakout":"no uptrend, no trade";
      L.push(["chart",smp+`SOL ${fm(st.price)} · 20-day high ${fm(st.high20)} · 50-day avg ${fm(st.sma50)} → ${v} · next check ${this.nextClose()}`,smp+`SOL ${fm(st.price)} · ${st.signal?"buy signal":"no breakout yet"}`])}
    else L.push(["chart","waiting for enough daily SOL candles to judge the trend","waiting for candles"]);
    const lm=(d.marks||{}).last;
    L.push(["sweeper",lm?`books written ${dur(this.snow()-lm)} ago, next in ${dur(Math.max(0,lm+300-this.snow()))} · bin: ${t.refused||0} today`:"waiting for the first bookkeeping",
      lm?`books ${dur(this.snow()-lm)} ago · bin ${t.refused||0}`:"waiting for bookkeeping"]);
    L.push(["ct","asleep: the CT/X feed isn't built yet (roadmap phase 5)","asleep: CT feed not built yet"]);
    return L;
  }
  loopAge(){const d=this.d;return d&&d.loop_age!=null?d.loop_age+(this.snow()-d.now):null}
  topVet(){const r=(this.d.today||{}).vet_reasons||{},k=Object.keys(r).sort((a,b)=>r[b]-r[a])[0];return k?` (most: ${k})`:""}
  nextClose(){const n=this.snow(),d0=Math.floor(n/86400)*86400;let t=d0+300;if(n>=t)t+=86400;return `00:05 UTC (in ${dur(t-n)})`}
  wsEvents(n){
    this.antennaAt=this.t;for(let i=0;i<Math.min(n,6);i++)this.blips.push({a:((this.blips.length*137)%360)*Math.PI/180,r:4+(i*3)%8,t0:this.t});
    this.idleSay("scout",`live stream: ${n} new transaction${n>1?"s":""} from watched wallets, each one checked for a buy or sell`,3800,`+${n} wallet event${n>1?"s":""}`);
  }
  pollPulse(){
    this.ringAt=this.t;const n=((this.d.selection||{}).picked||[]).length;
    if(n)this.idleSay("scout",`backup poll: re-read ${n} wallet${n>1?"s":""} in case the live stream missed a trade`,3400,`backup poll: ${n} wallet${n>1?"s":""}`);
  }
  // ---- jars in the vault = open positions --------------------------------------------------------------------
  syncJars(){
    const want=new Map((this.d.positions||[]).map(p=>[p.book+"|"+p.mint,p]));
    this.jars=this.jars.filter(j=>want.has(j.key));
    for(const j of this.jars){j.pos=want.get(j.key);j.temp=false;j.hidden=false;this.jarRules(j)}
    for(const [k,p] of want)if(!this.jars.find(j=>j.key===k)){const s=this.freeSlot();if(s<0)break;this.jars.push(this.mkJar(p,s))}
    const pend=this.d.pending_sells||[];for(const j of this.jars)j.retry=pend.some(p=>p.book===j.book&&p.mint===j.mint);
  }
  mkJar(p,slot){const j={key:p.book+"|"+p.mint,book:p.book,mint:p.mint,slot,pos:p};this.jarRules(j);return j}
  jarRules(j){const p=j.pos||{},E=this.cfg().exits||{};
    if(j.book.startsWith("trend")){j.sl=p.stop&&p.entry?1-p.stop/p.entry:null;j.tp=null}else{j.sl=E.enabled?E.sl:null;j.tp=E.enabled?E.tp:null}}
  freeSlot(){for(let i=0;i<8;i++)if(!this.jars.find(j=>j.slot===i))return i;return -1}
  jarX(j){return 167+j.slot*12}
  jarStand(j){return [this.jarX(j),142]}
  jarFor(e,ratio){
    let j=this.jars.find(x=>x.key===e.book+"|"+e.mint);
    if(!j){let s=this.freeSlot();if(s<0)s=7;j=this.mkJar({book:e.book,mint:e.mint,cost:1,value:ratio??1},s);j.temp=true;this.jars=this.jars.filter(x=>x.slot!==s);this.jars.push(j)}
    return j;
  }
  // ---- scripts: one per real event ------------------------------------------------------------------------------
  buyScript(e){
    const R=this,c=this.cfg(),f=e.fill||{},F=e.features||{},k=coin(e.mint),trend=e.source==="trend-rider";
    const stop=e.decision==="filled"?"vault":e.station,who=trend?"chart":"scout",home=b=>DEF[b].home;
    const card={type:"card",book:e.book,bad:false};const size=f.usd??e.size;
    const S=[doit(()=>{const j=R.jars.find(x=>x.key===e.book+"|"+e.mint);if(j&&stop==="vault")j.hidden=true})];
    if(trend)S.push(say("chart",`SOL closed above its 20-day high in an uptrend → buy signal`+(F.stop_pct?`, stop ${fpp(F.stop_pct,1)} below`:""),3400,{s:"SOL broke out → buy signal"}));
    else S.push(doit(()=>this.wsBlip()),say("scout",e.book==="consensus"?`${e.source.split("+").length} traders bought ${k} within 15 min: ${e.source.split("+").map(h=>"@"+h).join(" + ")}`:
      `@${e.source} just bought ${k}`+(f.detect_ms!=null?` · we saw it ${(f.detect_ms/1000).toFixed(1)}s later`:"")+" → checking it",3400,
      {s:e.book==="consensus"?`${e.source.split("+").length} traders bought ${k}`:`@${e.source} bought ${k}`}));
    S.push(doit(()=>{R.bots[who].carry=card}));
    if(stop==="chart")return S.concat(this.refuseTail(e,who,"chart"));
    // gate 1: book rules (panic, pause, limits, fresh signal, not already held, room for a position, size)
    S.push(walk(who,NEAR.guard),say("guard",stop==="guard"?"✗ "+plain(e.reason):
      `rules OK: ${e.book} book open · ${size!=null?"size "+fm(size):"size set"}`+(f.detect_ms!=null?` · signal ${Math.round(f.detect_ms/1000)}s old (max ${c.max_age}s)`:""),stop==="guard"?4200:2800,
      {bad:stop==="guard",s:stop==="guard"?"✗ "+plain(e.reason):"rules OK ✓"}));
    if(stop==="guard")return S.concat(this.refuseTail(e,who,"guard"));
    // gate 2: storm (window)
    S.push(doit(()=>{R.windowAt=R.t}),say("guard",stop==="window"?"✗ "+plain(e.reason):"storm check: SOL is not crashing ✓",stop==="window"?4000:2200,{bad:stop==="window",s:stop==="window"?"✗ storm: buying paused":"no storm ✓"}));
    if(stop==="window")return S.concat(this.refuseTail(e,who,"guard"));
    // gate 3: the coin's contract (CA) and liquidity
    S.push(walk(who,NEAR.vet),doit(()=>{R.bots.vet.pose="inspect";R.vetRows=R.vetLights(e,stop!=="vet");R.vetAt=R.t}),
      say("vet",this.vetText(e,stop!=="vet"),stop==="vet"?4600:3800,{bad:stop==="vet",s:stop==="vet"?"✗ "+plain(e.reason):`contract ${k} OK ✓`}),doit(()=>{R.bots.vet.pose=null}));
    if(stop==="vet")return S.concat(this.refuseTail(e,who,"vet"));
    // gate 4: a real quote (price impact)
    S.push(walk(who,NEAR.trader),doit(()=>{R.bots.trader.carry=R.bots[who].carry;R.bots[who].carry=null;R.vetRows=null}),
      par(walk(who,home(who)),say("trader",stop==="trader"?"✗ "+plain(e.reason):`quote for ${fm(size)} of ${k}`+(f.impact!=null?` · our buy moves the price ${fpp(f.impact,2)} (max ${fpp(c.max_impact)}) ✓`:" ✓"),stop==="trader"?4200:3000,
      {bad:stop==="trader",s:stop==="trader"?"✗ "+plain(e.reason):"quote OK"+(f.impact!=null?` · impact ${fpp(f.impact,2)}`:"")+" ✓"})));
    if(stop==="trader")return S.concat(this.refuseTail(e,"trader","trader"));
    // gate 5: can we get out again? (buy then sell right away)
    S.push(walk("trader",NEAR.exit),doit(()=>{R.door=stop==="exit"?"jam":"open"}),
      say("trader",stop==="exit"?"✗ "+plain(e.reason)+` (max ${fpp(c.roundtrip)}) → we might not get out`:
        (F.roundtrip!=null?`exit test: buying then selling right now costs ${fpp(F.roundtrip,1)} (max ${fpp(c.roundtrip)}) ✓`:`exit test passed: buying then selling right now costs less than the ${fpp(c.roundtrip)} limit ✓`),stop==="exit"?4400:3000,
        {bad:stop==="exit",s:stop==="exit"?"✗ "+plain(e.reason):"exit test ✓"}),
      doit(()=>{R.door=null}));
    if(stop==="exit")return S.concat(this.refuseTail(e,"trader","trader"));
    // filled: into the vault
    let jar=null;
    S.push(doit(()=>{jar=R.jarFor(e,1);jar.hidden=true}),
      {t:"walk",b:"trader",get to(){return R.jarStand(jar)}},
      doit(()=>{jar.hidden=false;R.bots.trader.carry=null;R.float("+"+k.split("…")[0],R.jarX(jar),161,34,P.g);R.fx.push({type:"ring",x:R.jarX(jar),y:161,z:31,col:P.g,t0:R.t,dur:900})}),
      say("trader",`bought ${fm(size)} of ${k}`+(f.slip_bps!=null?` · paid ${Math.abs(f.slip_bps/100).toFixed(1)}% ${f.slip_bps>=0?"more":"less"} than the trader`:"")+
        (f.total_ms!=null?` · ${(f.total_ms/1000).toFixed(1)}s after their trade`:"")+this.exitPlan(e),4200,{good:true,s:`bought ${fm(size)} of ${k} ✓`}),
      walk("trader",home("trader")),doit(()=>this.addRecent(e,`BOUGHT ${k} ${fm(size)}`+(trend?"":` · @${e.source}`),"g")));
    return S;
  }
  refuseTail(e,holder,deskBot){
    const R=this,k=coin(e.mint);let item=null;
    return [doit(()=>{const b=R.bots[holder];item={x:b.x+(b.x<240?8:-8),y:b.y+5,bad:true};R.items.push(item);b.carry=null;R.stamp(item.x,item.y)}),
      par(walk(holder,DEF[holder].home),seq({t:"walk",b:"sweeper",get to(){return [item.x-8,item.y]}},doit(()=>{R.bots.sweeper.pose="sweep"}),wait(1100),
        doit(()=>{R.bots.sweeper.pose=null;R.items=R.items.filter(x=>x!==item);R.bots.sweeper.carry={type:"card",bad:true}}),walk("sweeper",NEAR.shredder),
        doit(()=>{R.bots.sweeper.carry=null;R.shredAt=R.t;R.binCount++}),say("sweeper",`into the bin: ${k} · ${plain(e.reason)}`,3200,{bad:true,s:`${k} → bin`}),walk("sweeper",DEF.sweeper.home))),
      doit(()=>this.addRecent(e,`REFUSED ${k}: ${plain(e.reason)}`,"r"))];
  }
  exitPlan(e){
    if(e.book.startsWith("trend")){const s=(e.features||{}).stop_pct;return s?` · stop ${fpp(s,1)} below, trails up`:""}
    const E=this.cfg().exits||{};return E.enabled?` · sells at -${fpp(E.sl)} / +${fpp(E.tp)} / ${E.max_hold}h`:" · sells when the trader sells";
  }
  vetText(e,ok){
    const F=e.features||{},c=this.cfg(),k=coin(e.mint),p=[];
    if(e.mint===WSOL)return `${k} is on the safe list: no contract checks needed ✓`;
    if(F.liq!=null)p.push(`liquidity ${fk(F.liq)} ${F.liq>=c.min_liq?"✓":"✗"}`);
    if("mint_auth" in F)p.push(F.mint_auth?"creator can mint ✗":"supply locked ✓");
    if("freeze_auth" in F)p.push(F.freeze_auth?"freeze on ✗":"no freeze ✓");
    if(F.program==="token-2022")p.push("Token-2022 extensions checked");
    if(F.top10!=null)p.push(`top-10 holders ${fpp(F.top10)} ${F.top10<=c.top10?"✓":"✗"}`);
    if(ok)return `CA ${k}: `+(p.length?p.join(" · "):"every contract check passed ✓");
    const good=p.filter(x=>x.endsWith("✓"));return `CA ${k}: `+(good.length?good.join(" · ")+" · ":"")+"✗ "+plain(e.reason);
  }
  vetLights(e,ok){
    const F=e.features||{},c=this.cfg(),L=[];
    L.push(F.liq==null?(ok?1:0):F.liq>=c.min_liq?1:-1);L.push(!("mint_auth" in F)?(ok?1:0):F.mint_auth?-1:1);
    L.push(!("freeze_auth" in F)?(ok?1:0):F.freeze_auth?-1:1);L.push(F.top10==null?(ok?1:0):F.top10<=c.top10?1:-1);
    if(!ok&&!L.includes(-1))L[L.indexOf(0)>=0?L.indexOf(0):3]=-1;return L;
  }
  sellScript(e){
    const R=this,k=coin(e.mint),ex=e.exit||"trader_sold",c=this.cfg(),E=c.exits||{};
    const who={stop_loss:"guard",take_profit:"guard",max_hold:"guard",trader_sold:"scout",trend_break:"chart",strategy_replaced:"chart",write_off:"sweeper",panic:"chief"}[ex]||"guard";
    const trig=((e.note||"").match(/(?:stop-loss|take-profit) ([+-][\d.]+%)/)||[])[1];
    const prior=((this.d||{}).events||[]).filter(x=>x.kind==="sell"&&x.mint===e.mint&&x.exit==="stop_loss"&&x.ts<e.ts&&e.ts-x.ts<86400).length;
    const rep=ex==="stop_loss"&&prior?` · ${prior+1}${["","","nd","rd"][prior+1]||"th"} stop-loss on this coin in 24h`:"";
    const why={stop_loss:`stop-loss: ${k} fell ${trig||fp(e.pct)}`+(E.sl?` (limit -${fpp(E.sl)})`:"")+` → sell`+rep,
      take_profit:`take-profit: ${k} is up ${trig||fp(e.pct)}`+(E.tp?` (target +${fpp(E.tp)})`:"")+" → sell",
      max_hold:`time limit: ${k} held ${dur(e.held)}`+(E.max_hold?` (max ${E.max_hold}h)`:"")+" → sell",
      trader_sold:`@${e.source} sold ${k}, so we sell too`,trend_break:"SOL closed under its 50-day average → Trend Rider exits",
      strategy_replaced:"old v1 position → closing it",write_off:`${k}: no way to sell it for 24h → write it off`,panic:"PANIC stop: sell now"}[ex]||`selling ${k}`;
    const good=(e.pnl||0)>=0;let jar=null;
    const S=[doit(()=>{jar=R.jarFor(e,1+(e.pct||0))})];
    if(ex==="trader_sold")S.push(doit(()=>this.wsBlip()),say("scout",`@${e.source} is selling ${k}`,2600,{s:`@${e.source} sells ${k}`}));
    S.push({t:"walk",b:who,get to(){return R.jarStand(jar)}},say(who,why,3800,{bad:!good,good,s:{stop_loss:`stop-loss ${trig||""} → sell`,take_profit:`take-profit ${trig||""} → sell`,max_hold:"time limit → sell",
      trader_sold:"we sell too",trend_break:"trend over → exit",write_off:"no way out → write off",panic:"panic → sell"}[ex]}),
      doit(()=>{R.jars=R.jars.filter(x=>x!==jar);R.bots[who].carry={type:"jar",book:e.book,up:good}}));
    if(ex==="write_off")return S.concat([walk(who,NEAR.shredder),doit(()=>{R.bots[who].carry=null;R.shredAt=R.t}),
      say(who,`written off: counted as ${fsm(e.pnl)}`,3400,{bad:true,s:`written off ${fsm(e.pnl)}`}),walk(who,DEF[who].home),doit(()=>this.addRecent(e,`WRITTEN OFF ${k} ${fsm(e.pnl)}`,"r"))]);
    S.push(walk(who,NEAR.trader),doit(()=>{R.bots.trader.carry=R.bots[who].carry;R.bots[who].carry=null}),
      par(walk(who,DEF[who].home),seq(say("trader",`sold ${k} for ${fm(e.usd)} → ${fsm(e.pnl)}`+(e.pct!=null?` (${fp(e.pct)} after fees)`:"")+(e.held?` · held ${dur(e.held)}`:""),4200,{good,bad:!good,s:`sold ${k}: ${fsm(e.pnl)}`}),
        doit(()=>{R.bots.trader.carry=null}))),
      doit(()=>{R.float(fsm(e.pnl),DEF.trader.home[0],DEF.trader.home[1],40,good?P.g:P.r);this.addRecent(e,`SOLD ${k} · ${EXITNAME[ex]||ex} · ${fsm(e.pnl)}`,good?"g":"r")}));
    return S;
  }
  failScript(e){
    const R=this,k=coin(e.mint);
    return [say("trader",`selling ${k} failed (${plain(e.reason).replace(/^\w+: /,"")}) → we keep it and retry automatically`,4000,{bad:true,s:"sale failed → retrying"}),
      doit(()=>{const j=R.jars.find(x=>x.mint===e.mint&&x.book===e.book);if(j)j.retry=true;this.addRecent(e,`SALE FAILED ${k}: retrying`,"r")})];
  }
  roundScript(){
    const js=this.jars.filter(j=>j.pos);if(!js.length)return [];
    const E=this.cfg().exits||{},srt=js.slice().sort((a,b)=>(a.pos.ch??0)-(b.pos.ch??0)),mid=js[Math.floor(js.length/2)];
    const part=j=>{const p=j.pos;if(p.book.startsWith("trend"))return `SOL ${fm(p.price)}, stop ${fm(p.stop)}`+(p.price&&p.stop?` (${fpp(1-p.stop/p.price,1)} away)`:"");
      return `${coin(p.mint)} ${p.ch==null?"(no price)":fp(p.ch)}`+(p.age!=null&&p.max_hold?`, ${dur(p.age)} of ${p.max_hold}h`:"")};
    let txt=js.length===1?`exit check: ${part(js[0])}`:`exit check: ${js.length} open · weakest ${part(srt[0])} · strongest ${part(srt[srt.length-1])}`;
    if(E.enabled&&js.some(j=>!j.book.startsWith("trend")))txt+=` · sells at -${fpp(E.sl)} / +${fpp(E.tp)}`;
    return [walk("guard",this.jarStand(mid)),say("guard",txt+" · nothing to do yet",4000,{s:js.length===1?`exit check: ${part(js[0])}`:`exit check: ${js.length} open`}),walk("guard",DEF.guard.home)];
  }
  markScript(){
    const B=(this.d.books||[]);
    return [walk("sweeper",NEAR.ledger),doit(()=>{this.ledgerAt=this.t}),say("sweeper","books written: "+B.map(b=>`${b.book} ${fm(b.equity)}`).join(" · "),3800,{s:"books written ✓"}),walk("sweeper",DEF.sweeper.home)];
  }
  selectScript(){
    const s=this.d.selection||{},pk=(s.picked||[]).map(h=>"@"+h);
    const txt=(s.checked?`re-checked ${s.checked} wallets on their last 30 days: ${s.qualified} passed`:"trader picks updated")+(pk.length?` → watching ${pk.join(" ")}`:" → none good enough, so watching nobody (cash)");
    return [walk("scout",NEAR.board),doit(()=>{this.boardAt=this.t}),say("scout",txt,4600,{s:pk.length?`watching ${pk.length} trader${pk.length>1?"s":""}`:"picked nobody (cash)"}),walk("scout",DEF.scout.home)];
  }
  dailyScript(){
    const st=(this.d.trend||{}).state||{};
    if(!st.ready)return [say("chart","daily close is in, but there are not enough candles yet to judge the trend",3400)];
    return [say("chart",`daily close SOL ${fm(st.close)} · 20-day high ${fm(st.high20)} · 50-day avg ${fm(st.sma50)} → `+
      (st.signal?"BUY signal":st.trend_exit?"trend exit":st.uptrend?"uptrend, no breakout: no trade today":"no uptrend: no trade today"),4600,
      {s:"daily close: "+(st.signal?"BUY":st.trend_exit?"exit":"no trade")})];
  }
  retryScript(p){
    const R=this,j=this.jars.find(x=>x.book===p.book&&x.mint===p.mint);
    return (j?[walk("trader",this.jarStand(j))]:[]).concat([say("trader",`retry #${p.tries} selling ${coin(p.mint)}${p.why?" ("+p.why+")":""} · next try in ${dur(p.next_in)}`,3600,{bad:true,s:`retry #${p.tries}: ${coin(p.mint)}`}),walk("trader",DEF.trader.home)]);
  }
  newDayScript(){return [walk("sweeper",NEAR.rules),say("sweeper","new UTC day: the daily loss limits start fresh",3200,{s:"new day: limits reset"}),walk("sweeper",DEF.sweeper.home)]}
  catchUpScript(evs){
    const n=x=>evs.filter(x).length,b=n(e=>e.kind==="buy"&&e.decision==="filled"),r=n(e=>e.kind==="buy"&&e.decision!=="filled"),s=n(e=>e.kind==="sell");
    return [say("chief",`while this page was closed: ${b} bought, ${r} refused, ${s} sold (latest ones follow)`,4200,{s:`while away: ${b} bought, ${r} refused, ${s} sold`}),
      doit(()=>{for(const e of evs.slice(-4)){const k=coin(e.mint);this.addRecent(e,e.kind==="sell"?`SOLD ${k} ${fsm(e.pnl)}`:e.decision==="filled"?`BOUGHT ${k}`:`REFUSED ${k}: ${plain(e.reason)}`,e.kind==="sell"?((e.pnl||0)>=0?"g":"r"):e.decision==="filled"?"g":"r")}})];
  }
  // ---- small effects (each one marks something that just happened) -----------------------------------------
  wsBlip(){this.antennaAt=this.t;this.blips.push({a:((this.blips.length*137)%360)*Math.PI/180,r:6,t0:this.t})}
  stamp(x,y){this.fx.push({type:"stamp",x,y,t0:this.t,dur:1400})}
  float(text,x,y,z,col){this.fx.push({type:"float",text,x,y,z,col,t0:this.t,dur:2200})}
  // ---- cards ----------------------------------------------------------------------------------------------------
  renderCards(){for(const k of ORDER)this.updateCard(k)}
  updateCard(k){
    const el=this.cardEls&&this.cardEls[k];if(!el||!this.d)return;const [v,m,mc]=this.cardVal(k);
    el.v.textContent=v;el.m.style.width=Math.max(0,Math.min(1,m||0))*100+"%";el.m.style.background=mc||DEF[k].col;el.q.textContent=this.quotes[k]||"";
  }
  cardVal(k){
    const d=this.d,t=d.today||{},lv=d.live||{},ws=d.ws||{},sel=d.selection||{};
    if(k==="chief")return [lv.armed?`REAL $ ${lv.mode==="trial"?"TRIAL":"ON"}`:"PAPER · real $ available",lv.safety_total?lv.safety_ok/lv.safety_total:0,lv.armed?P.a:P.g];
    if(k==="scout"){const n=(sel.picked||[]).length;return [`${n} wallet${n===1?"":"s"} · stream ${ws.connected?"UP":"DOWN"}`,ws.connected?1:.25,ws.connected?P.g:P.r]}
    if(k==="guard"){const B=(d.books||[]).filter(b=>!b.live&&b.day_pnl!=null),day=B.reduce((s,b)=>s+b.day_pnl,0);
      const used=Math.max(0,...B.filter(b=>b.daily_limit&&b.start).map(b=>-b.day_pnl/(b.start*b.daily_limit)));
      return [`${(d.positions||[]).length} open · day ${fsm(day)}`,used,used>.8?P.r:used>.5?P.a:P.g]}
    if(k==="vet"){const c=t.vet_checked||0,r=(t.by_station||{}).vet||0;return [`${c} checked · ${r} refused`,c?r/c:0,P.a]}
    if(k==="trader")return [`${t.filled||0} bought · ${t.sold||0} sold`,t.proposals?(t.filled||0)/t.proposals:0,P.g];
    if(k==="chart"){const st=(d.trend||{}).state||{};return st.ready?[((d.trend||{}).sample?"SAMPLE ":"")+`SOL ${fm(st.price)} · ${st.uptrend?"uptrend":"no uptrend"}`,st.high20?Math.min(1,st.price/st.high20):0,st.signal?P.g:P.c]:["waiting for candles",0]}
    if(k==="sweeper"){const lm=(d.marks||{}).last;return [`bin ${t.refused||0} · ${(d.pending_sells||[]).length} retrying`,lm?Math.min(1,(this.snow()-lm)/300):0,P.c]}
    return ["not connected",0,P.r];
  }
  banner(){
    const d=this.d;let txt="",cls="";
    if(d){
      if(!this.awake){txt=`BOT OFFLINE · last heartbeat ${d.heartbeat_age!=null?dur(d.heartbeat_age)+" ago":"unknown"} · the room sleeps because nothing is running`;cls="bad"}
      else if(d.panic){txt="PANIC STOP · no new buys anywhere · sells still allowed";cls="bad"}
      else if(d.storm_until&&this.snow()<d.storm_until){txt=`STORM PAUSE · SOL fell fast · new buys paused ${dur(d.storm_until-this.snow())} more`;cls="warn"}
      else if(this.loopAge()>240){txt=d.selection_due?`MAIN LOOP BUSY ${dur(this.loopAge())} · re-checking traders (about once a day)`:`MAIN LOOP QUIET ${dur(this.loopAge())} · heartbeat OK`;cls="warn"}
      else if((d.live||{}).approval){txt=`REAL-MONEY BUY WAITING FOR YOUR TAP IN TELEGRAM · ${fm(d.live.approval.size)}`;cls="warn"}
    }
    if(txt!==this._bn){this._bn=txt;this.bannerEl.textContent=txt;this.bannerEl.className="rm-banner "+cls;this.bannerEl.style.display=txt?"block":"none"}
  }
  // ---- bubbles over the canvas ----------------------------------------------------------------------------------
  place(){
    const sw=this.stageW||VW,sc=sw/VW;
    for(const k of ORDER){
      const b=this.bots[k],el=this.bubEl[k];
      if(!b.bub){if(el.classList.contains("on"))el.classList.remove("on");continue}
      const txt=sw<520&&b.bub.short?b.bub.short:b.bub.text,key=txt+"|"+b.bub.tag+"|"+b.bub.tone;
      if(el._t!==key){el._t=key;el.innerHTML=`<b>${DEF[k].name}</b>${b.bub.tag==="SAMPLE"?`<span class="rm-tag">SAMPLE</span> `:""}${esc(txt)}`;
        el.className="rm-bub"+(b.bub.tone?" "+b.bub.tone:"");el._w=el.offsetWidth;el._h=el.offsetHeight}
      const hd=iso(b.x,b.y,40*(this.bs||1)),ft=iso(b.x,b.y,0),cx=(hd[0]-VX0)*sc;let top=(hd[1]-VY0)*sc-el._h-6,below=false;if(top<2){top=(ft[1]-VY0)*sc+8;below=true}
      const left=Math.max(2,Math.min(sw-el._w-2,cx-el._w/2));
      el.style.transform=`translate(${Math.round(left)}px,${Math.round(top)}px)`;el.style.setProperty("--tail",Math.round(cx-left)+"px");
      el.classList.toggle("below",below);el.classList.add("on");
    }
  }
  // ---- drawing: vector isometric scene at full screen resolution ---------------------------------------------------
  resize(){
    const cw=Math.max(240,Math.min(this.root.clientWidth||340,980)),dpr=Math.min(3,window.devicePixelRatio||1),ch=cw*VH/VW;
    this.cv.width=Math.round(cw*dpr);this.cv.height=Math.round(ch*dpr);this.stage.style.width=cw+"px";this.stage.style.height=ch+"px";
    this.stageW=cw;this.dpr=dpr;this.sc=cw*dpr/VW;this.bs=cw<520?.95:.9;this.bg=null;for(const b in this.bubEl)this.bubEl[b]._t=null;this.draw();
  }
  world(g){g.setTransform(this.sc,0,0,this.sc,-VX0*this.sc,-VY0*this.sc)}
  rr(x,y,w,h,r,g){g=g||this.ctx;g.beginPath();g.moveTo(x+r,y);g.arcTo(x+w,y,x+w,y+h,r);g.arcTo(x+w,y+h,x,y+h,r);g.arcTo(x,y+h,x,y,r);g.arcTo(x,y,x+w,y,r);g.closePath()}
  quad(pts,fill,g){g=g||this.ctx;g.beginPath();pts.forEach((p,i)=>{const q=iso(p[0],p[1],p[2]||0);i?g.lineTo(q[0],q[1]):g.moveTo(q[0],q[1])});g.closePath();g.fillStyle=fill;g.fill()}
  box(x,y,z,w,d,h,c,trim){
    // three visible faces: front-left (+Y), front-right (+X), top
    this.quad([[x,y+d,z],[x+w,y+d,z],[x+w,y+d,z+h],[x,y+d,z+h]],c.l);
    if(h>3){const g=this.ctx,[fx,fy]=iso(x,y+d,z+h),sh=g.createLinearGradient(fx,fy,fx,fy+h);sh.addColorStop(0,"rgba(255,255,255,.06)");sh.addColorStop(1,"rgba(0,0,0,.25)");g.fillStyle=sh;g.fillRect(fx,fy,w,h)}
    this.quad([[x,y,z+h],[x+w,y,z+h],[x+w,y+d,z+h],[x,y+d,z+h]],c.t);
    if(trim){const g=this.ctx;g.save();g.strokeStyle=trim;g.globalAlpha=.8;g.lineWidth=.7;g.beginPath();g.moveTo(...iso(x,y+d,z+h));g.lineTo(...iso(x+w,y+d,z+h));g.stroke();g.restore()}
  }
  plane(type,ax,ay,z1,fn){
    // draw on a vertical surface: "Y" = facing the lower left (back-right wall, desk fronts), "X" = facing the lower right (left wall)
    const g=this.ctx;g.save();g.transform(1,0,0,1,ax,ay*K-z1);fn(g);g.restore();
  }
  cyl(cx,cy,z,r,h,side,top){
    const g=this.ctx,[x,y]=iso(cx,cy,z),rx=r*1.3,ry=r*1.3*K;
    g.beginPath();g.moveTo(x-rx,y-h);g.lineTo(x-rx,y);g.ellipse(x,y,rx,ry,0,Math.PI,0,true);g.lineTo(x+rx,y-h);g.closePath();g.fillStyle=side;g.fill();
    g.beginPath();g.ellipse(x,y-h,rx,ry,0,0,Math.PI*2);g.fillStyle=top;g.fill();
  }
  label(s,x,y,z,col,size){const g=this.ctx,[sx,sy]=iso(x,y,z);g.font=`700 ${size||5.5}px ${FONT}`;g.textAlign="center";g.fillStyle=col||P.mu;g.fillText(s,sx,sy);g.textAlign="left"}
  paintBase(){
    const c=document.createElement("canvas");c.width=this.cv.width;c.height=this.cv.height;const g=c.getContext("2d");this.world(g);
    g.fillStyle="#020605";g.fillRect(VX0,VY0,VW,VH);
    // the back wall, flat on the screen
    let gr=g.createLinearGradient(0,-WH,0,0);gr.addColorStop(0,"#142a22");gr.addColorStop(1,"#0a1813");g.fillStyle=gr;g.fillRect(0,-WH,RX,WH);
    g.strokeStyle="rgba(0,255,156,.05)";g.lineWidth=.6;for(let x=18;x<RX;x+=18){g.beginPath();g.moveTo(x,-WH);g.lineTo(x,0);g.stroke()}
    g.fillStyle="rgba(0,229,255,.3)";g.fillRect(0,-WH,RX,.8);g.fillStyle="rgba(0,0,0,.35)";g.fillRect(0,-5,RX,5);
    // the floor, seen from above at an angle
    const FH=RY*K;gr=g.createLinearGradient(0,0,0,FH);gr.addColorStop(0,"#0e231c");gr.addColorStop(1,"#060f0c");g.fillStyle=gr;g.fillRect(0,0,RX,FH);
    g.strokeStyle="rgba(0,255,156,.06)";g.lineWidth=.5;
    for(let x=15;x<RX;x+=15){g.beginPath();g.moveTo(x,0);g.lineTo(x,FH);g.stroke()}
    for(let y=15;y<RY;y+=15){g.beginPath();g.moveTo(0,y*K);g.lineTo(RX,y*K);g.stroke()}
    const rg=g.createRadialGradient(RX/2,FH/2,6,RX/2,FH/2,170);rg.addColorStop(0,"rgba(0,255,156,.09)");rg.addColorStop(1,"rgba(0,255,156,0)");g.fillStyle=rg;g.fillRect(0,0,RX,FH);
    g.save();g.shadowColor=P.g;g.shadowBlur=5*this.dpr;g.strokeStyle="rgba(0,255,156,.65)";g.lineWidth=1.1;g.beginPath();g.moveTo(0,0);g.lineTo(RX,0);g.stroke();g.restore();
    g.strokeStyle="rgba(0,229,255,.25)";g.lineWidth=.9;g.strokeRect(0,-WH,RX,WH+FH);
    // the room's name, top left of the back wall
    this.ctx2=this.ctx;this.ctx=g;
    this.plane("Y",14,0,90,q=>{q.save();q.shadowColor=P.g;q.shadowBlur=7*this.dpr;q.font=`800 9px ${FONT}`;q.fillStyle=P.g;q.fillText("ZETRAX",0,9);q.restore();
      q.font=`700 4.4px ${FONT}`;q.fillStyle="rgba(0,229,255,.75)";q.fillText("BOT ROOM · LIVE DATA",40,8.4)});
    this.ctx=this.ctx2;
    return c;
  }
  draw(){
    const g=this.ctx;if(!g)return;const t=this.t;
    g.setTransform(1,0,0,1,0,0);if(!this.bg)this.bg=this.paintBase();g.drawImage(this.bg,0,0);this.world(g);
    this.drawWindow(t);this.drawScreen();this.drawHeart(t);this.drawClock();this.drawBoard(t);this.drawDoor(t);
    const L=[];
    for(const dk of DESKS)L.push([dk.y+dk.d,()=>this.drawDesk(dk,t)]);
    L.push([28,()=>this.drawSafe()],[170,()=>this.drawRadar(t)],[150,()=>this.drawDock()],[166,()=>this.drawBin(t)],[165,()=>this.drawLedger(t)],[172,()=>this.drawVault(t)]);
    for(const it of this.items)L.push([it.y-.1,()=>this.drawFloorCard(it)]);
    for(const k of ORDER){const b=this.bots[k];L.push([b.y+.5,()=>this.drawBot(b,t)])}
    L.sort((a,b)=>a[0]-b[0]);for(const x of L)x[1]();
    this.drawFx(t);
    g.setTransform(1,0,0,1,0,0);
    if(!this.awake){g.fillStyle="rgba(0,0,0,.5)";g.fillRect(0,0,this.cv.width,this.cv.height)}
    else if(this.d&&this.d.panic&&Math.floor(t/600)%2){g.fillStyle="rgba(255,30,60,.13)";g.fillRect(0,0,this.cv.width,this.cv.height)}
  }
  // ---- wall pieces ------------------------------------------------------------------------------------------------
  drawWindow(t){
    // SOL weather: the 24 h move from Jupiter; a storm only while the bot's storm pause is really on
    const d=this.d||{},ch=(d.sol||{}).ch24,storm=!!(d.storm_until&&this.snow()<d.storm_until),mv=this.motion;
    this.plane("Y",236,0,80,g=>{g.scale(.6,.6);
      this.rr(0,0,52,40,2,g);g.fillStyle="#1d3530";g.fill();
      const sky=storm?["#1c1038","#0b0618"]:ch==null?["#0b1736","#050b1c"]:ch<-2?["#1d2c44","#0e1626"]:ch<0?["#284056","#14212f"]:ch<2?["#2f6fa3","#173a5c"]:["#4aa3e0","#1f5f96"];
      const lg=g.createLinearGradient(0,2,0,38);lg.addColorStop(0,sky[0]);lg.addColorStop(1,sky[1]);g.fillStyle=lg;g.fillRect(2,2,48,36);
      if(storm||(ch!=null&&ch<-2)){g.strokeStyle=storm?"rgba(170,175,255,.8)":"rgba(120,150,200,.7)";g.lineWidth=.6;
        for(let i=0;i<14;i++){const x=2+((i*13+(mv?t/(storm?9:16):0))%48),y=2+((i*7+(mv?t/(storm?5:9):0))%34);g.beginPath();g.moveTo(x,y);g.lineTo(x-1.2,y+3.5);g.stroke()}}
      else if(ch!=null&&ch<0){g.fillStyle="#7f93a6";for(const [x,y,r] of[[14,14,5],[20,12,6],[27,14,5],[33,24,4],[38,22,5]]){g.beginPath();g.arc(x,y,r,0,7);g.fill()}}
      else if(ch!=null){g.save();g.shadowColor=P.gold;g.shadowBlur=10*this.dpr;g.fillStyle=P.gold;g.beginPath();g.arc(36,13,ch>=2?6:4.5,0,7);g.fill();g.restore();
        if(ch<2){g.fillStyle="rgba(220,230,240,.85)";for(const [x,y,r] of[[12,24,4],[17,22,5],[22,24,4]]){g.beginPath();g.arc(x,y,r,0,7);g.fill()}}}
      else{g.fillStyle="#fff";for(const [x,y] of[[9,8],[30,13],[18,26],[42,7],[37,30]])g.fillRect(x,y,.8,.8)}
      if(storm&&mv&&t%2600<110){g.fillStyle="rgba(255,255,255,.6)";g.fillRect(2,2,48,36)}
      g.fillStyle="#1d3530";g.fillRect(25,2,1.4,36);g.fillRect(2,19.5,48,1.4);
      if(t-this.windowAt<1600){g.strokeStyle=P.a;g.lineWidth=1.4;g.strokeRect(-1,-1,54,42)}
      g.font=`800 6px ${FONT}`;g.fillStyle=storm||(ch!=null&&ch<0)?P.r:P.g;g.textAlign="center";
      g.fillText(storm?"STORM PAUSE":ch==null?"SOL --":"SOL 24H "+(ch>=0?"+":"")+ch.toFixed(1)+"%",26,48);g.textAlign="left";
    });
  }
  drawScreen(){
    // Trend Rider's real daily SOL closes, 20-day high, 50-day average and an open position's stop
    const d=this.d||{},T=d.trend||{},st=T.state||{},cl=(T.closes||[]).map(x=>x[1]).filter(v=>v>0);
    this.plane("Y",14,0,76,g=>{
      this.rr(0,0,94,46,2.5,g);g.fillStyle="#18302a";g.fill();g.fillStyle=P.scr;g.fillRect(2.5,2.5,89,41);
      g.strokeStyle="rgba(0,255,156,.4)";g.lineWidth=.5;g.strokeRect(2.5,2.5,89,41);
      g.font=`700 5px ${FONT}`;g.fillStyle=P.mu;g.fillText("SOL · 1D",6,9);g.font=`700 3.6px ${FONT}`;g.fillText("TREND RIDER",6,41.5);g.font=`700 5px ${FONT}`;
      g.textAlign="right";if(T.sample){g.fillStyle=P.a;g.fillText("SAMPLE",88,9)}else if(st.price){g.fillStyle=P.g;g.fillText("$"+st.price.toFixed(2),88,9)}g.textAlign="left";
      if(cl.length<5){g.fillStyle=P.mu;g.fillText("WAITING FOR DAILY CANDLES",14,27);return}
      const tp=(d.positions||[]).find(p=>p.book.startsWith("trend")&&p.stop),vals=cl.concat([st.high20,st.sma50,tp&&tp.stop].filter(v=>v>0));
      let mn=Math.min(...vals),mx=Math.max(...vals);const pad=(mx-mn)*.08||1;mn-=pad;mx+=pad;
      const X0=6,X1=88,Y0=13,Y1=41,X=i=>X0+i*(X1-X0)/(cl.length-1),Y=v=>Y1-(v-mn)/(mx-mn)*(Y1-Y0);
      const line=(v,col,dash)=>{g.save();g.setLineDash(dash);g.strokeStyle=col;g.lineWidth=.5;g.beginPath();g.moveTo(X0,Y(v));g.lineTo(X1,Y(v));g.stroke();g.restore()};
      if(st.high20)line(st.high20,"rgba(0,229,255,.8)",[2,1.5]);if(st.sma50)line(st.sma50,"rgba(255,176,0,.75)",[.6,1.4]);if(tp)line(tp.stop,"rgba(255,59,92,.85)",[2,1.5]);
      const ag=g.createLinearGradient(0,Y0,0,Y1);ag.addColorStop(0,"rgba(0,255,156,.35)");ag.addColorStop(1,"rgba(0,255,156,0)");
      g.beginPath();cl.forEach((v,i)=>i?g.lineTo(X(i),Y(v)):g.moveTo(X(i),Y(v)));g.lineTo(X1,Y1);g.lineTo(X0,Y1);g.closePath();g.fillStyle=ag;g.fill();
      g.save();g.shadowColor=P.g;g.shadowBlur=4*this.dpr;g.strokeStyle=P.g;g.lineWidth=.8;g.beginPath();cl.forEach((v,i)=>i?g.lineTo(X(i),Y(v)):g.moveTo(X(i),Y(v)));g.stroke();g.restore();
      g.fillStyle="#fff";g.beginPath();g.arc(X1,Y(cl[cl.length-1]),1.2,0,7);g.fill();
    });
  }
  drawHeart(t){
    // a spike for each time the bot touched its heartbeat file (every ~30 s while it runs)
    const ok=this.awake;
    this.plane("Y",114,0,76,g=>{this.rr(0,0,26,20,2,g);g.fillStyle="#18302a";g.fill();g.fillStyle=P.scr;g.fillRect(2,2,22,16);
      g.font=`700 3.8px ${FONT}`;g.fillStyle=ok?P.mu:P.r;g.fillText(ok?"PULSE":"NO PULSE",3.5,6.5);
      g.strokeStyle=ok?P.g:P.r;g.lineWidth=.6;g.beginPath();g.moveTo(2,13);
      if(ok)for(const tb of this.beats.slice().sort((a,b)=>b-a)){const x=23-(t-tb)/1000*.7;if(x<4||x>23)continue;g.lineTo(x-2,13);g.lineTo(x-1,8);g.lineTo(x,16);g.lineTo(x+1,12);g.lineTo(x+1.6,13)}
      g.lineTo(24,13);g.stroke()});
  }
  drawClock(){this.plane("Y",114,0,52,g=>{this.rr(0,0,26,12,2,g);g.fillStyle="#18302a";g.fill();g.fillStyle=P.scr;g.fillRect(2,2,22,8);
    g.font=`800 6.5px ${FONT}`;g.fillStyle=P.g;g.textAlign="center";g.fillText(this.d?hhmm(this.snow()):"--:--",13,8.5);g.textAlign="left"})}
  drawBoard(t){
    // the traders the bot picked; it lights up when the bot re-selects
    const pk=((this.d||{}).selection||{}).picked||[],fl=t-this.boardAt<2600;
    this.plane("Y",146,0,76,g=>{this.rr(0,0,52,42,2,g);g.fillStyle="#3b2817";g.fill();g.fillStyle="#6b4a2b";g.fillRect(2,2,48,38);
      g.font=`800 5px ${FONT}`;g.fillStyle="#f6dfb8";g.textAlign="center";g.fillText("TRADERS",26,8);
      if(!pk.length)g.fillText("NONE",26,24);
      pk.slice(0,3).forEach((h,i)=>{const x=4+i*16,y=11;g.fillStyle=fl?"#ffffff":P.paper;g.fillRect(x,y,14,24);g.fillStyle="#3fa9ff";g.fillRect(x,y,14,4);
        g.fillStyle=P.r;g.beginPath();g.arc(x+7,y,1.2,0,7);g.fill();g.fillStyle=P.ink;g.font=`800 3.8px ${FONT}`;g.fillText(String(h).slice(0,6),x+7,y+10);
        g.fillStyle="#9aa9b0";g.fillRect(x+3,y+15,8,.7);g.fillRect(x+3,y+18,6,.7)});
      g.textAlign="left"});
  }
  drawDoor(t){
    this.plane("Y",208,0,64,g=>{g.save();g.shadowColor=P.g;g.shadowBlur=6*this.dpr;g.font=`800 6px ${FONT}`;g.fillStyle=P.g;g.textAlign="center";g.fillText("EXIT",12,6);g.restore();g.textAlign="left"});
    this.plane("Y",208,0,54,g=>{g.fillStyle="#18302a";g.fillRect(0,0,24,54);
      if(this.door==="open"){const lg=g.createLinearGradient(2,0,22,0);lg.addColorStop(0,"#d8fff0");lg.addColorStop(1,"#3ad692");g.fillStyle=lg;g.fillRect(2,2,20,52);g.fillStyle="#0f241c";g.fillRect(2,2,6,52)}
      else{g.fillStyle=this.door==="jam"?"#4a0f1c":"#0f241c";g.fillRect(2,2,20,52);g.fillStyle="rgba(255,255,255,.08)";g.fillRect(4,6,16,18);g.fillRect(4,28,16,22);
        g.fillStyle=P.gold;g.beginPath();g.arc(18,30,1,0,7);g.fill()}
      if(this.door==="jam"){g.strokeStyle=P.r;g.lineWidth=1.6;g.beginPath();g.moveTo(5,16);g.lineTo(19,36);g.moveTo(19,16);g.lineTo(5,36);g.stroke()}});
  }
  // ---- furniture -----------------------------------------------------------------------------------------------------
  drawDesk(dk,t){
    const {k,x,y,w,d}=dk,h=16,col=DEF[k].col,D=this.d||{};
    this.box(x,y,0,w,d,h,{t:"#2b463c",l:"#182a24",r:"#101d19"},col);
    if(k==="ct"){this.box(x+9,y+4,h,12,8,1.6,{t:"#2d3532",l:"#1c2220",r:"#151a18"});const [lx,ly]=iso(x+22,y+12,h+1.6);this.ctx.fillStyle=P.r;this.ctx.fillRect(lx-.6,ly-.6,1.2,1.2);
      this.plane("Y",x+1,y+d,h-4,g=>{g.font=`800 4.6px ${FONT}`;g.fillStyle=col;g.fillText("CT",1,5)});return}
    this.plane("Y",x+3,y+d,h-4,g=>{g.font=`800 4.8px ${FONT}`;g.fillStyle=col;g.globalAlpha=.85;g.fillText(dk.lbl,0,5)});
    if(k==="chart"){this.box(x+12,y+4,h,24,7,1.2,{t:"#2f4a3c",l:"#1c3027",r:"#16251f"});return}
    // a monitor showing that desk's live numbers
    const mx=x+w/2-9,my=y+3;this.box(mx,my,h,18,3,14,{t:"#1c2b26",l:"#0c1512",r:"#08100d"});
    this.plane("Y",mx+1,my+3,h+13,g=>{g.fillStyle=P.scr;g.fillRect(0,0,16,11);g.font=`800 3.6px ${FONT}`;
      if(k==="vet"){const L=this.vetRows;for(let i=0;i<4;i++){const v=L&&(!this.motion||t-this.vetAt>300+i*450)?L[i]:0;
        g.fillStyle=v>0?P.g:v<0?P.r:"#0d4030";g.beginPath();g.arc(2.5,2+i*2.5,.8,0,7);g.fill();g.fillStyle=v>0?"#0b8a52":v<0?"#8a0b2a":"#0b2a1d";g.fillRect(4.5,1.5+i*2.5,10,1)}}
      else if(k==="guard"){(D.books||[]).filter(b=>!b.live).slice(0,3).forEach((b,i)=>{const u=b.day_pnl!=null&&b.daily_limit&&b.start?Math.max(0,-b.day_pnl/(b.start*b.daily_limit)):0;
        g.fillStyle="#0b2a1d";g.fillRect(2,2+i*3,12,1.8);g.fillStyle=u>.8?P.r:u>.5?P.a:BOOKCOL[b.book]||P.g;g.fillRect(2,2+i*3,Math.max(.6,12*Math.min(1,u)),1.8)})}
      else if(k==="trader"){const tt=D.today||{};g.fillStyle=P.g;g.fillText("BUY "+(tt.filled||0),1.5,4.5);g.fillStyle=P.c;g.fillText("SELL "+(tt.sold||0),1.5,9)}
      else if(k==="chief"){(D.books||[]).slice(0,4).forEach((b,i)=>{g.fillStyle=b.state==="ok"?(BOOKCOL[b.book]||P.g):b.state==="off"?"#203028":P.r;g.beginPath();g.arc(3+i*3.4,3,1,0,7);g.fill()});
        const lv=D.live||{};g.fillStyle=lv.armed?P.a:P.g;g.fillText(lv.armed?"REAL ON":"PAPER",1.5,9)}});
    if(k==="chief"){const [px,py]=iso(x+w-5,y+d-4,h);const g=this.ctx;g.fillStyle=D.panic?P.r:"#5a1020";g.beginPath();g.ellipse(px,py-.6,2.6,1.3,0,0,7);g.fill()}
  }
  drawSafe(){
    // the real-money wallet: READY (off) until you switch it on in Telegram
    const lv=(this.d||{}).live||{},on=!!lv.armed;
    this.box(238,4,0,28,24,38,{t:"#3a4852",l:"#26323a",r:"#1b242a"},on?P.a:"#5f7480");
    this.plane("Y",238,28,36,g=>{g.strokeStyle="rgba(255,255,255,.18)";g.lineWidth=.6;g.strokeRect(2,2,24,32);
      g.fillStyle="#55636e";g.beginPath();g.arc(20,10,3.2,0,7);g.fill();g.fillStyle="#9aa7b2";g.beginPath();g.arc(20,10,1.3,0,7);g.fill();
      g.fillStyle="#0e1418";this.rr(4,6,10,6,3,g);g.fill();g.fillStyle=on?P.a:"#46545c";g.beginPath();g.arc(on?11:7,9,2.3,0,7);g.fill();
      g.font=`800 4.6px ${FONT}`;g.fillStyle="#b9c7cf";g.fillText("REAL $",4,22);g.fillStyle=on?P.a:P.g;g.fillText(on?(lv.mode==="trial"?"TRIAL ON":"ON"):"OFF",4,28);
      g.font=`700 3.4px ${FONT}`;g.fillStyle="#8fa39b";g.fillText(on?"switch: /live off":"available",4,33)});
  }
  drawRadar(t){
    // turns only while the live wallet stream is connected; a dot for each real wallet transaction
    const g=this.ctx,on=!!((this.d||{}).ws||{}).connected&&this.awake,cx=24,cy=165;
    this.cyl(cx,cy,0,8,9,"#122822","#1c3a30");
    const [x,y]=iso(cx,cy,12);g.save();g.translate(x,y);g.scale(1,K);const R=20;
    const rg=g.createRadialGradient(0,0,2,0,0,R);rg.addColorStop(0,"#0c3a27");rg.addColorStop(1,"#041a10");g.fillStyle=rg;g.beginPath();g.arc(0,0,R,0,7);g.fill();
    g.strokeStyle=on?"rgba(0,255,156,.55)":"rgba(255,59,92,.5)";g.lineWidth=1;g.beginPath();g.arc(0,0,R,0,7);g.stroke();
    g.strokeStyle="rgba(0,255,156,.18)";for(const r of[7,13]){g.beginPath();g.arc(0,0,r,0,7);g.stroke()}
    if(on){const a=this.motion?t/1000*Math.PI*.8:-.6;const sg=g.createLinearGradient(0,0,Math.cos(a)*R,Math.sin(a)*R);sg.addColorStop(0,"rgba(0,255,156,.05)");sg.addColorStop(1,"rgba(0,255,156,.55)");
      g.fillStyle=sg;g.beginPath();g.moveTo(0,0);g.arc(0,0,R,a-.7,a);g.closePath();g.fill();g.strokeStyle=P.g;g.lineWidth=1.2;g.beginPath();g.moveTo(0,0);g.lineTo(Math.cos(a)*R,Math.sin(a)*R);g.stroke()}
    this.blips=this.blips.filter(b=>t-b.t0<5000);
    for(const b of this.blips){const al=1-(t-b.t0)/5000;g.fillStyle=`rgba(0,229,255,${al.toFixed(2)})`;g.beginPath();g.arc(Math.cos(b.a)*b.r*1.6,Math.sin(b.a)*b.r*1.6,2.2,0,7);g.fill()}
    if(t-this.ringAt<1400){g.strokeStyle=`rgba(0,229,255,${(1-(t-this.ringAt)/1400).toFixed(2)})`;g.lineWidth=1.2;g.beginPath();g.arc(0,0,3+(t-this.ringAt)/70,0,7);g.stroke()}
    g.restore();this.label("WALLETS",cx+12,cy+14,0,on?P.mu:P.r);
  }
  drawDock(){
    const g=this.ctx,b=this.bots.sweeper,home=Math.hypot(b.x-DEF.sweeper.home[0],b.y-DEF.sweeper.home[1])<1,[x,y]=iso(74,168,0);
    g.save();g.translate(x,y);g.scale(1,K);g.fillStyle="#13221d";g.beginPath();g.arc(0,0,15,0,7);g.fill();
    g.strokeStyle=home?"rgba(255,79,184,.8)":"rgba(255,79,184,.35)";g.lineWidth=1.4;g.beginPath();g.arc(0,0,15,0,7);g.stroke();g.restore();
  }
  drawBin(t){
    const n=this.binCount;this.box(96,150,0,18,16,26,{t:"#2a3833",l:"#1b2723",r:"#131c19"},n?P.r:null);
    this.quad([[100,154,26],[110,154,26],[110,156,26],[100,156,26]],"#030605");
    this.plane("Y",97,166,22,g=>{g.font=`800 5px ${FONT}`;g.fillStyle=P.mu;g.fillText("BIN",3,6);g.font=`800 8px ${FONT}`;g.fillStyle=n?P.r:P.mu;g.fillText(String(n),4,16)});
    const a=t-this.shredAt;if(a<1300){const g=this.ctx;g.fillStyle=P.paper;for(let i=0;i<7;i++){const [px,py]=iso(101+i*1.3,155,26+18-((a/40+i*5)%18));g.fillRect(px,py,.6,2.2)}}
  }
  drawLedger(t){
    const fl=t-this.ledgerAt<1800,g=this.ctx;
    // a lectern: a post, a sloped top and the open ledger the sweeper writes the books into
    this.box(129,156,0,10,8,2,{t:"#5e3c20",l:"#4a2f19",r:"#3b2514"});this.box(132,158,2,4,4,15,{t:"#8a5a33",l:"#6e4626",r:"#55361d"});
    this.quad([[126,154,15],[142,154,15],[142,165,20],[126,165,20]],"#6e4626");
    if(fl){g.save();g.shadowColor="#fff";g.shadowBlur=12*this.dpr}
    this.quad([[127,155,15.6],[134,155,16.4],[134,164,20.9],[127,164,20.1]],fl?"#ffffff":P.paper);this.quad([[134,155,16.4],[141,155,15.6],[141,164,20.1],[134,164,20.9]],fl?"#f4fff9":"#dbe9e2");
    if(fl)g.restore();g.strokeStyle="rgba(60,90,80,.6)";g.lineWidth=.4;for(let i=1;i<4;i++){g.beginPath();g.moveTo(...iso(128,155+i*2,15.9+i*1.1));g.lineTo(...iso(133,155+i*2,16.6+i*1.1));g.stroke()}
    this.label("BOOKS",133,176,0,P.mu,4.8);
  }
  drawVault(t){
    this.box(160,150,0,102,22,18,{t:"#1e322b",l:"#13211c",r:"#0d1714"},P.c);
    this.plane("Y",162,172,15,g=>{g.font=`800 5px ${FONT}`;g.fillStyle=P.c;g.globalAlpha=.85;g.fillText("VAULT · OPEN POSITIONS",1,5)});
    const extra=(this.d&&this.d.positions||[]).length-8;if(extra>0)this.label("+"+extra+" MORE",268,150,22,P.mu,5);
    for(const j of this.jars)if(!j.hidden)this.drawJar(j,t);
  }
  drawJar(j,t){
    // a glass jar per open position: fill = value vs cost, red ring = stop-loss, green ring = take-profit
    const g=this.ctx,p=j.pos||{},ratio=p.value!=null&&p.cost?p.value/p.cost:p.ch!=null?1+p.ch:1,x=this.jarX(j),y=161,z=18,r=4.2,h=13;
    const [sx,sy]=iso(x,y,z),rx=r*1.25,ry=r*1.25*K,lv=Math.max(.06,Math.min(1,ratio/1.4))*h,col=ratio>=1?"#16c47a":"#d0284a";
    g.fillStyle="rgba(0,0,0,.35)";g.beginPath();g.ellipse(sx,sy,rx+1,ry+.6,0,0,7);g.fill();
    g.fillStyle=col;g.beginPath();g.moveTo(sx-rx,sy-lv);g.lineTo(sx-rx,sy);g.ellipse(sx,sy,rx,ry,0,Math.PI,0,true);g.lineTo(sx+rx,sy-lv);g.closePath();g.fill();
    g.fillStyle=shade(col,.3);g.beginPath();g.ellipse(sx,sy-lv,rx,ry,0,0,7);g.fill();
    g.fillStyle="rgba(190,255,230,.12)";g.strokeStyle="rgba(190,255,230,.45)";g.lineWidth=.5;g.beginPath();g.moveTo(sx-rx,sy-h);g.lineTo(sx-rx,sy);g.ellipse(sx,sy,rx,ry,0,Math.PI,0,true);g.lineTo(sx+rx,sy-h);g.fill();g.stroke();
    const ring=(f,c)=>{const yy=sy-Math.max(.5,Math.min(1,f/1.4))*h;g.strokeStyle=c;g.lineWidth=.7;g.beginPath();g.ellipse(sx,yy,rx,ry,0,0,Math.PI);g.stroke()};
    if(j.sl!=null)ring(1-j.sl,P.r);if(j.tp!=null)ring(1+j.tp,P.g);
    g.fillStyle=BOOKCOL[j.book]||P.g;g.beginPath();g.ellipse(sx,sy-h,rx,ry,0,0,7);g.fill();
    g.fillStyle="rgba(255,255,255,.25)";g.fillRect(sx-rx+1,sy-h+2,1,h-4);
    this.plane("Y",x-5,172,8,q=>{q.font=`800 4.4px ${FONT}`;q.fillStyle=P.tx;q.fillText(coin3(j.mint),0,4)});
    if(j.retry){g.strokeStyle=P.a;g.lineWidth=.9;g.beginPath();g.arc(sx,sy-h-6,2.6,(this.motion?t/300:0),(this.motion?t/300:0)+4.6);g.stroke()}
  }
  drawFloorCard(it){
    this.quad([[it.x-4,it.y-3,.3],[it.x+4,it.y-3,.3],[it.x+4,it.y+3,.3],[it.x-4,it.y+3,.3]],P.paper);
    const g=this.ctx;g.strokeStyle=P.r;g.lineWidth=.9;g.beginPath();g.moveTo(...iso(it.x-3,it.y-2,.4));g.lineTo(...iso(it.x+3,it.y+2,.4));g.moveTo(...iso(it.x+3,it.y-2,.4));g.lineTo(...iso(it.x-3,it.y+2,.4));g.stroke();
  }
  // ---- robots (on wheels; the chief hovers) -------------------------------------------------------------------------
  bodyPath(s,g){
    g.beginPath();
    if(s==="blob")g.ellipse(0,-18.5,9.5,10.5,0,0,7);
    else if(s==="dome"){this.rr(-8.5,-17,17,11,4,g);g.moveTo(7.5,-21);g.arc(0,-21,7.5,0,7)}
    else if(s==="hex")this.rr(-9.5,-28.5,19,21,5.5,g);
    else if(s==="square")this.rr(-9.5,-27.5,19,20.5,3.5,g);
    else if(s==="tri"){g.moveTo(0,-31);g.quadraticCurveTo(1.5,-30,10,-9.5);g.quadraticCurveTo(10.5,-7,8,-7);g.lineTo(-8,-7);g.quadraticCurveTo(-10.5,-7,-10,-9.5);g.quadraticCurveTo(-1.5,-30,0,-31);g.closePath()}
    else{g.moveTo(-9,-9);g.lineTo(-9,-20);g.arc(0,-20,9,Math.PI,0);g.lineTo(9,-9);
      for(let i=0;i<6;i++){const x0=9-i*3;g.quadraticCurveTo(x0-1.5,-5.5,x0-3,-9)}g.closePath()}
  }
  drawBot(b,t){
    const g=this.ctx,d=b.d,[sx,sy]=iso(b.x,b.y,0),mv=b.path.length>0&&this.motion,sleep=b.pose==="sleep"||b.k==="ct",hover=d.shape==="ghost";
    const VIS={blob:-20,dome:-21,hex:-21,square:-20,tri:-15,ghost:-20}[d.shape];
    const bs=this.bs||1;g.save();g.translate(sx,sy);g.scale(bs,bs);
    g.fillStyle="rgba(0,0,0,.5)";g.beginPath();g.ellipse(0,0,11.5,4.6,0,0,7);g.fill();
    if(mv){const dir=b.sdx>=0?-1:1;g.strokeStyle=d.col;g.globalAlpha=.5;g.lineWidth=.9;
      for(let i=0;i<3;i++){const yy=-7-i*6.5,o=(t/40+i*7)%6;g.beginPath();g.moveTo(dir*(12+o),yy);g.lineTo(dir*(21+o+i*3),yy);g.stroke()}g.globalAlpha=1;
      g.fillStyle="rgba(200,255,230,.18)";for(let i=0;i<3;i++){const a=((t/120)+i)%3;g.beginPath();g.arc(dir*(9+a*4),-1-a,1.6-a*.4,0,7);g.fill()}}
    if(!hover){
      g.fillStyle="#1d2725";this.rr(-9.5,-8.5,19,6.5,2.8,g);g.fill();g.fillStyle="rgba(255,255,255,.08)";g.fillRect(-8,-8,16,1);
      for(const wx of[-6.2,6.2]){g.fillStyle="#070a09";g.beginPath();g.ellipse(wx,-2.7,3.6,3.6,0,0,7);g.fill();g.strokeStyle="#2c3834";g.lineWidth=.9;g.stroke();
        g.fillStyle="#6d7f78";g.beginPath();g.arc(wx,-2.7,1.1,0,7);g.fill();const a=b.odo/3.6;g.strokeStyle="rgba(140,160,152,.55)";g.lineWidth=.5;g.beginPath();
        for(const o of[0,2.094,4.189]){g.moveTo(wx+Math.cos(a+o)*1.1,-2.7+Math.sin(a+o)*1.1);g.lineTo(wx+Math.cos(a+o)*2.9,-2.7+Math.sin(a+o)*2.9)}g.stroke()}
    }else{const rg=g.createRadialGradient(0,-2,1,0,-2,11);rg.addColorStop(0,"rgba(255,210,63,.55)");rg.addColorStop(1,"rgba(255,210,63,0)");g.fillStyle=rg;g.beginPath();g.ellipse(0,-2,11,4.4,0,0,7);g.fill();g.translate(0,-3)}
    g.rotate(mv?Math.max(-1,Math.min(1,b.sdx))*.14:0);
    this.bodyPath(d.shape,g);const lg=g.createLinearGradient(-11,-31,11,-6);lg.addColorStop(0,shade(d.col,.5));lg.addColorStop(.42,d.col);lg.addColorStop(1,shade(d.col,-.5));
    g.fillStyle=lg;g.fill();g.strokeStyle=shade(d.col,-.62);g.lineWidth=.8;g.stroke();
    g.fillStyle="rgba(255,255,255,.4)";g.beginPath();g.ellipse(-4.2,VIS-6.5,2.6,1.4,-.5,0,7);g.fill();
    if(b.k==="chart"){
      // its face is a screen with the last real SOL closes
      g.fillStyle=P.scr;this.rr(-7,VIS-4.5,14,9,1.6,g);g.fill();g.strokeStyle="rgba(0,255,156,.5)";g.lineWidth=.5;g.stroke();
      const cl=((((this.d||{}).trend||{}).closes)||[]).slice(-14).map(x=>x[1]);
      if(cl.length>2){const mn=Math.min(...cl),mx=Math.max(...cl)||1;g.strokeStyle=P.g;g.lineWidth=.6;g.beginPath();cl.forEach((v,i)=>{const x=-5.5+i*11/(cl.length-1),y=VIS+3-(v-mn)/((mx-mn)||1)*6;i?g.lineTo(x,y):g.moveTo(x,y)});g.stroke()}
    }else{
      const vw=d.shape==="tri"?10:13;g.fillStyle="#04110d";this.rr(-vw/2,VIS-3.6,vw,7.2,3.6,g);g.fill();g.strokeStyle="rgba(255,255,255,.16)";g.lineWidth=.5;g.stroke();
      const lx=mv?Math.sign(b.sdx)*1.3:0,ex=d.shape==="tri"?2:2.7;
      if(sleep){g.strokeStyle=d.eye;g.lineWidth=.8;g.beginPath();g.moveTo(-ex-1.2,VIS+.5);g.lineTo(-ex+1.2,VIS+.5);g.moveTo(ex-1.2,VIS+.5);g.lineTo(ex+1.2,VIS+.5);g.stroke()}
      else{g.save();g.shadowColor=d.eye;g.shadowBlur=4*this.dpr;g.fillStyle=d.eye;this.rr(-ex-1.1+lx,VIS-1.6,2.2,3.2,1,g);g.fill();this.rr(ex-1.1+lx,VIS-1.6,2.2,3.2,1,g);g.fill();g.restore()}
    }
    const k=b.k;
    if(k==="scout"){g.strokeStyle="#1d5f99";g.lineWidth=1;g.beginPath();g.moveTo(0,-29);g.lineTo(0,-35);g.stroke();g.fillStyle="#9fd4ff";g.beginPath();g.ellipse(0,-35.5,3.4,1.3,0,0,7);g.fill();
      const lit=t-this.antennaAt<1500;g.save();if(lit){g.shadowColor=P.c;g.shadowBlur=8*this.dpr}g.fillStyle=lit?P.c:"#7fc6ff";g.beginPath();g.arc(0,-37.5,1.4,0,7);g.fill();g.restore()}
    else if(k==="guard"){g.fillStyle="#2b1b08";this.rr(-9.5,-31,19,4.5,2,g);g.fill();g.fillStyle="#1a1005";g.fillRect(-10.5,-27.2,21,1.2);g.fillStyle=P.gold;g.beginPath();g.arc(5,-12,1.5,0,7);g.fill()}
    else if(k==="trader"){g.strokeStyle="#0f3d24";g.lineWidth=1.4;g.beginPath();g.arc(0,-19,10.8,Math.PI*1.08,Math.PI*1.92);g.stroke();g.fillStyle="#0f3d24";this.rr(-11.6,-23,2.6,6,1,g);g.fill();this.rr(9,-23,2.6,6,1,g);g.fill();
      g.lineWidth=.8;g.beginPath();g.moveTo(10,-17);g.quadraticCurveTo(8,-12,4,-12.5);g.stroke()}
    else if(k==="vet"){const o=b.pose==="inspect"&&this.motion?Math.sin(t/160)*1.5:0;
      if(b.pose==="inspect"){g.fillStyle="rgba(127,246,255,.18)";g.beginPath();g.moveTo(8+o,-21);g.lineTo(22,-12);g.lineTo(18,-6);g.closePath();g.fill()}
      g.strokeStyle="#cfe3ff";g.lineWidth=1;g.fillStyle="rgba(160,220,255,.35)";g.beginPath();g.arc(8+o,-21,3.3,0,7);g.fill();g.stroke();g.strokeStyle="#8a5a33";g.beginPath();g.moveTo(10+o,-18.5);g.lineTo(12+o,-15);g.stroke()}
    else if(k==="chief"){g.fillStyle=P.gold;g.beginPath();g.moveTo(-5,-28);g.lineTo(-5.5,-33);g.lineTo(-2.5,-30.5);g.lineTo(0,-34.5);g.lineTo(2.5,-30.5);g.lineTo(5.5,-33);g.lineTo(5,-28);g.closePath();g.fill()}
    else if(k==="sweeper"){g.save();g.translate(8.5,-20);if(b.pose==="sweep"&&this.motion)g.rotate(Math.sin(t/85)*.55);else g.rotate(-.18);
      g.strokeStyle=P.wood;g.lineWidth=1.2;g.beginPath();g.moveTo(0,0);g.lineTo(3,17);g.stroke();g.fillStyle="#e6c35c";g.beginPath();g.moveTo(.5,15);g.lineTo(6,15);g.lineTo(7.5,20);g.lineTo(-1.5,20);g.closePath();g.fill();g.restore();
      if(b.pose==="sweep"&&this.motion){g.fillStyle="rgba(230,195,92,.45)";for(let i=0;i<4;i++){const a=((t/90)+i*1.7)%4;g.beginPath();g.arc(13+a*3,-1-a*1.4,1.4-a*.25,0,7);g.fill()}}}
    else if(k==="ct"){g.fillStyle="#7fdcff";g.beginPath();g.moveTo(-3,-12);g.quadraticCurveTo(-1.5,-15,0,-12.5);g.quadraticCurveTo(1.5,-15,3,-12);g.quadraticCurveTo(0,-10,-3,-12);g.fill()}
    if(sleep){const z=this.motion?(t/900)%3:1;g.font=`800 ${5+z}px ${FONT}`;g.fillStyle="rgba(200,255,230,.6)";g.fillText("z",8+z*2,-30-z*4)}
    if(b.carry){const cx=-5,cy=-50;if(b.carry.type==="card"){g.fillStyle=P.paper;this.rr(cx,cy,10,12,1.2,g);g.fill();g.fillStyle=b.carry.bad?P.r:BOOKCOL[b.carry.book]||P.g;g.fillRect(cx,cy,10,3);
        g.fillStyle="#8fa39b";g.fillRect(cx+2,cy+5,6,.8);g.fillRect(cx+2,cy+7.5,4.5,.8);if(b.carry.bad){g.strokeStyle=P.r;g.lineWidth=1;g.beginPath();g.moveTo(cx+2,cy+4);g.lineTo(cx+8,cy+11);g.moveTo(cx+8,cy+4);g.lineTo(cx+2,cy+11);g.stroke()}}
      else{g.fillStyle="rgba(190,255,230,.25)";this.rr(-4,-47,8,10,2,g);g.fill();g.fillStyle=b.carry.up?"#16c47a":"#d0284a";g.fillRect(-3.2,-42,6.4,4.4);g.fillStyle=BOOKCOL[b.carry.book]||P.g;g.fillRect(-4,-48,8,2)}}
    g.restore();
    g.font=`700 4.4px ${FONT}`;g.textAlign="center";g.fillStyle=d.col;g.globalAlpha=.8;g.fillText(d.name,sx,sy+8*bs);g.globalAlpha=1;g.textAlign="left";
  }
  drawFx(t){
    const g=this.ctx;this.fx=this.fx.filter(f=>t-f.t0<f.dur);
    for(const f of this.fx){const a=(t-f.t0)/f.dur;
      if(f.type==="float"){const [x,y]=iso(f.x,f.y,(f.z||0)+a*16);g.save();g.globalAlpha=1-a*a;g.shadowColor=f.col;g.shadowBlur=6*this.dpr;g.font=`800 8px ${FONT}`;g.textAlign="center";g.fillStyle=f.col;g.fillText(f.text,x,y);g.restore()}
      else if(f.type==="stamp"&&Math.floor(a*6)%2===0){const [x,y]=iso(f.x,f.y,10);g.strokeStyle=P.r;g.lineWidth=1.6;g.beginPath();g.arc(x,y,6,0,7);g.moveTo(x-4,y-4);g.lineTo(x+4,y+4);g.moveTo(x+4,y-4);g.lineTo(x-4,y+4);g.stroke()}
      else if(f.type==="ring"){const [x,y]=iso(f.x,f.y,f.z||0);g.strokeStyle=f.col;g.globalAlpha=1-a;g.lineWidth=1.2;g.beginPath();g.ellipse(x,y,4+a*16,(4+a*16)*K,0,0,7);g.stroke();g.globalAlpha=1}}
  }
}
window.ZRoom={create:(root,o)=>new Room(root,o),plain};
})();
