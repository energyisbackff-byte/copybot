/* Zetrax bot room: a live, animated picture of what the bot is doing (served at /room.js, data from /api/room).
   Rule: nothing moves without a reason. Every walk, bubble and number comes from the bot's own records:
   - a buy proposal walks the real gate order of paper.Paper.on_buy: rules -> storm -> contract check (CA) -> quote ->
     exit test -> vault. Where the bot refused it, the coin stops at that desk and the sweeper takes it to the bin.
   - a sale leaves the vault for the reason the bot recorded (stop-loss, take-profit, time limit, trader sold...).
   - duties follow real timers: bookkeeping when a new equity mark appears (every 5 min), the exit check every 30 s
     while positions are open, the trader board when the bot re-selects, the radar turns only while the wallet stream
     is connected, the heart monitor beats when the bot's heartbeat file is touched.
   - the CT desk sleeps because that feed is not built yet. When the bot is offline, the whole room sleeps.
   Read-only: the room never sends anything to the bot. */
(function(){"use strict";
const W=320,H=180,AISLE=126,WSOL="So11111111111111111111111111111111111111112";
const P={wall:"#07120e",wall2:"#0b1a14",trim:"#14382a",floor:"#040a07",tile:"#0a1610",desk:"#15251d",top:"#20382b",edge:"#3a6c51",leg:"#0d1a14",
  scr:"#01170e",frame:"#1a2f27",g:"#00ff9c",gd:"#00a866",c:"#00e5ff",a:"#ffb000",r:"#ff3b5c",mu:"#5f8f7b",tx:"#c8ffe6",wood:"#7a5230",
  gold:"#ffd23f",paper:"#e9fff4",ink:"#06140e"};
const BOOKCOL={copy:P.g,consensus:P.c,trend:P.a,"trend-live":P.r};
const DEF={
  chief:{name:"CHIEF",role:"runs the room, real-money lock",col:"#f1f1f1",shape:"ghost",home:[297,76]},
  scout:{name:"SCOUT",role:"finds traders, watches their wallets",col:"#3fa9ff",shape:"blob",home:[50,152]},
  guard:{name:"GUARD",role:"rules, limits, stop-loss",col:"#ff9f1a",shape:"hex",home:[74,114]},
  vet:{name:"VET",role:"checks each coin's contract (CA)",col:"#d9e4ff",shape:"blob",home:[166,84]},
  trader:{name:"TRADER",role:"quotes, buys and sells",col:"#2bdc7a",shape:"blob",home:[266,114]},
  chart:{name:"CHART",role:"Trend Rider: SOL daily",col:"#00d5ef",shape:"square",home:[106,70]},
  sweeper:{name:"SWEEPER",role:"housekeeping: bin, books, retries",col:"#ff4fb8",shape:"tri",home:[69,166]},
  ct:{name:"CT",role:"reads Crypto Twitter",col:"#ff3b5c",shape:"square",home:[26,76]}};
const ORDER=["chief","scout","guard","vet","trader","chart","sweeper","ct"];
const NEAR={guard:[90,116],vet:[182,86],trader:[250,116],exit:[301,110],shredder:[105,140],ledger:[138,145],board:[238,54],rules:[58,116]};
const DESKS=[{k:"ct",x:8,y:54,w:36,h:12,lbl:"CT"},{k:"chart",x:86,y:48,w:40,h:12,lbl:"TREND"},{k:"vet",x:146,y:62,w:40,h:12,lbl:"CA CHECK"},
  {k:"chief",x:280,y:52,w:34,h:12,lbl:"CHIEF"},{k:"guard",x:54,y:92,w:40,h:12,lbl:"RULES"},{k:"trader",x:246,y:92,w:40,h:12,lbl:"TRADE"}];
const SH={
  blob:["...XXXXX...","..XXXXXXX..",".XHXXXXXXX.",".HXXXXXXXX.","XXXXXXXXXXX","XXXXXXXXXXX","XXXXXXXXXXX","XXXXXXXXXXD",".XXXXXXXXD.","..XXXXXXD..","...DDDDD..."],
  hex:["..XXXXXXX..",".XXXXXXXXX.","XHXXXXXXXXX","XHXXXXXXXXX","XXXXXXXXXXX","XXXXXXXXXXX","XXXXXXXXXXX","XXXXXXXXXXD","XXXXXXXXXXD",".XXXXXXXXD.","..DDDDDDD.."],
  square:["XXXXXXXXXXX","XHHXXXXXXXX","XHXXXXXXXXX","XXXXXXXXXXX","XXXXXXXXXXX","XXXXXXXXXXX","XXXXXXXXXXX","XXXXXXXXXXD","XXXXXXXXXXD","XXXXXXXXXDD",".DDDDDDDDD."],
  tri:[".....X.....","....XXX....","....HXX....","...XHXXX...","...XXXXX...","..XXXXXXX..","..XXXXXXX..",".XXXXXXXXX.",".XXXXXXXXXD","XXXXXXXXXXD","DDDDDDDDDDD"],
  ghost:["...XXXXX...","..XXXXXXX..",".XHXXXXXXX.",".HXXXXXXXX.",".XXXXXXXXX.",".XXXXXXXXX.",".XXXXXXXXX.",".XXXXXXXXX.",".XXXXXXXXD.",".XXXXXXXXD.",".X.XX.XX.X."]};
const EYE={blob:4,hex:4,square:4,tri:6,ghost:4};
// 3x5 pixel font, rows top to bottom
const FONT={"0":"111101101101111","1":"010110010010111","2":"111001111100111","3":"111001111001111","4":"101101111001001","5":"111100111001111",
  "6":"111100111101111","7":"111001001010010","8":"111101111101111","9":"111101111001111","A":"010101111101101","B":"110101110101110",
  "C":"011100100100011","D":"110101101101110","E":"111100110100111","F":"111100110100100","G":"011100101101011","H":"101101111101101",
  "I":"111010010010111","J":"001001001101010","K":"101101110101101","L":"100100100100111","M":"10001/11011/10101/10001/10001","N":"1001/1101/1011/1001/1001",
  "O":"010101101101010","P":"110101110100100","Q":"010101101110011","R":"110101110101101","S":"011100010001110","T":"111010010010010",
  "U":"101101101101111","V":"101101101101010","W":"10001/10001/10101/11011/10001","X":"101101010101101","Y":"101101010010010","Z":"111001010100111",
  "$":"011110010011110","%":"101001010100101","+":"000010111010000","-":"000000111000000",".":"000000000000010",":":"000010000010000",
  "/":"001001010100100","!":"010010010000010","?":"110001010000010"," ":"000000000000000","#":"101111101111101","(":"010100100100010",
  ")":"010001001001010","@":"010101111100011","=":"000111000111000"};
const GL={};for(const k in FONT){const v=FONT[k],rows=v.includes("/")?v.split("/"):[0,3,6,9,12].map(i=>v.slice(i,i+3));GL[k]={w:rows[0].length,rows}}
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
.rm-stage{position:relative;margin:0 auto;background:#000;border:1px solid rgba(0,255,156,.2);overflow:hidden;max-width:100%}
.rm-stage canvas{position:absolute;inset:0;width:100%;height:100%;image-rendering:pixelated;image-rendering:crisp-edges}
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

class Bot{
  constructor(k){this.k=k;this.d=DEF[k];this.x=this.d.home[0];this.y=this.d.home[1];this.path=[];this.carry=null;this.pose=null;this.face=0;this.bub=null}
  goto(x,y,instant){
    if(instant){this.x=x;this.y=y;this.path=[];return}
    // short hops go straight; longer walks use the aisle so nobody walks through a desk
    const pts=Math.abs(x-this.x)<28&&Math.abs(y-this.y)<28?[[x,this.y],[x,y]]:[[this.x,AISLE],[x,AISLE],[x,y]];
    this.path=[];let cx=this.x,cy=this.y;
    for(const p of pts)if(Math.abs(p[0]-cx)>.5||Math.abs(p[1]-cy)>.5){this.path.push(p);cx=p[0];cy=p[1]}
  }
  step(dt,sp){
    let left=72*sp*dt;
    while(left>0&&this.path.length){const [tx,ty]=this.path[0],dx=tx-this.x,dy=ty-this.y,dd=Math.hypot(dx,dy);if(Math.abs(dx)>.3)this.face=dx>0?1:-1;
      if(dd<=left){this.x=tx;this.y=ty;this.path.shift();left-=dd}else{this.x+=dx/dd*left;this.y+=dy/dd*left;left=0}}
    if(!this.path.length)this.face=0;
  }
}

class Room{
  constructor(root,o){
    this.root=root;this.o=Object.assign({duties:true,rounds:true,autoReplay:true,manual:false,preview:""},o||{});
    this.rm=!!(window.matchMedia&&matchMedia("(prefers-reduced-motion: reduce)").matches);
    this.motion=this.o.motion!==false&&!this.rm;this.t=0;this.d=null;this.recvAt=0;this.visible=true;this.onTick=null;
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
  resize(){
    const cw=Math.max(200,Math.min(this.root.clientWidth||320,980)),dpr=window.devicePixelRatio||1;let k=Math.floor(cw*dpr/W),css;
    if(k>=2)css=W*k/dpr;else{k=Math.max(1,Math.ceil(cw*dpr/W));css=cw}
    this.k=k;this.cv.width=W*k;this.cv.height=H*k;this.stage.style.width=css+"px";this.stage.style.height=css*H/W+"px";this.stageW=css;
    for(const b in this.bubEl)this.bubEl[b]._t=null;
    this.draw();
  }
  setMotion(on){this.motion=!!on&&!this.rm}
  // ---- clock ------------------------------------------------------------------------------------------------
  snow(){return this.d?this.d.now+(this.t-this.recvAt)/1000:Date.now()/1000}
  get awake(){return !this.d||this.d.online!==false}
  cfg(){return Object.assign({top10:.3,roundtrip:.06,max_age:120,min_liq:50000,max_impact:.03,exit_check_s:30,exits:{}},(this.d&&this.d.cfg)||{})}
  busy(){return !!this.cur||this.queue.length>0}
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
      const last=(d.events||[]).slice(-1)[0];
      if(last&&this.o.autoReplay&&d.now-last.ts<900)this.enqueueEvent(last,{tag:"LATEST · "+dur(d.now-last.ts)+" ago"});
      this.renderCards();this.banner();return;
    }
    const fresh=(d.events||[]).filter(e=>!this.seen.has(e.id));fresh.forEach(e=>this.seen.add(e.id));
    if(fresh.length>6){const old=fresh.splice(0,fresh.length-2);this.queue.push({build:()=>this.catchUpScript(old)})}
    for(const e of fresh)this.enqueueEvent(e,{tag:e.sample?"SAMPLE":""});
    const pv=prev;
    if(d.selection&&d.selection.at&&pv.selection&&d.selection.at!==pv.selection.at)this.queue.push({build:()=>this.selectScript()});
    if(this.o.duties&&d.marks&&d.marks.last&&!d.marks.quiet&&pv.marks&&pv.marks.last&&d.marks.last!==pv.marks.last)this.queue.push({build:()=>this.markScript()});
    const ts=(d.trend||{}).state,pts=(pv.trend||{}).state;if(ts&&ts.t&&pts&&pts.t&&ts.t!==pts.t)this.queue.push({build:()=>this.dailyScript()});
    for(const p of d.pending_sells||[]){const q=(pv.pending_sells||[]).find(x=>x.book===p.book&&x.mint===p.mint);if(q&&p.tries>q.tries)this.queue.push({build:()=>this.retryScript(p)})}
    if(Math.floor(d.now/86400)!==Math.floor(pv.now/86400))this.queue.push({build:()=>this.newDayScript()});
    const n1=(d.ws||{}).notifications,n0=(pv.ws||{}).notifications;if(n1!=null&&n0!=null&&n1>n0)this.wsEvents(n1-n0);
    if(d.last_poll&&pv.last_poll&&d.last_poll!==pv.last_poll)this.pollPulse();
    const h1=d.heartbeat_age!=null?d.now-d.heartbeat_age:null,h0=pv.heartbeat_age!=null?pv.now-pv.heartbeat_age:null;
    if(h1&&h0&&h1>h0+1)this.beats.push(this.t-(d.now-h1)*1000);
    this.beats=this.beats.filter(b=>this.t-b<40000);
    this.renderCards();this.banner();
  }
  replay(){const e=((this.d||{}).events||[]).slice(-1)[0];if(e)this.enqueueEvent(e,{tag:"REPLAY · "+dur(this.snow()-e.ts)+" ago"});return !!e}
  enqueueEvent(e,o){
    const b=e.kind==="buy"?()=>this.buyScript(e):e.kind==="sell"?()=>this.sellScript(e):e.kind==="sell_failed"?()=>this.failScript(e):null;
    if(b)this.queue.push({build:b,tag:(o&&o.tag)||(e.sample?"SAMPLE":""),ev:e});
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
    else if(s.t==="say"){this.showSay(s.b,s.text,s.o);s.left=s.ms}
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
    if(this.o.duties&&this.o.rounds&&this.jars.some(j=>j.pos)&&this.t-this.lastRound>c.exit_check_s*1000){this.lastRound=this.t;this.queue.push({build:()=>this.roundScript()});return}
    if(this.t-this.lastIdle>6500){
      this.lastIdle=this.t;const L=this.statusLines();
      if(L.length){const [b,txt,sh]=L[this.idleI++%L.length];this.idleSay(b,txt,4600,sh)}
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
      "paper only: no real money moves"+(lv.safety_total?` · real-money safety checks ${lv.safety_ok}/${lv.safety_total}`:""),lv.armed?`REAL $ ${lv.mode==="trial"?"TRIAL":"ON"}`:"paper only"]);
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
  jarX(j){return 172+j.slot*16}
  jarStand(j){return [this.jarX(j)+4,136]}
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
      doit(()=>{jar.hidden=false;R.bots.trader.carry=null;R.float("+"+k.split("…")[0],R.jarX(jar)+4,142,P.g)}),
      say("trader",`bought ${fm(size)} of ${k}`+(f.slip_bps!=null?` · paid ${Math.abs(f.slip_bps/100).toFixed(1)}% ${f.slip_bps>=0?"more":"less"} than the trader`:"")+
        (f.total_ms!=null?` · ${(f.total_ms/1000).toFixed(1)}s after their trade`:"")+this.exitPlan(e),4200,{good:true,s:`bought ${fm(size)} of ${k} ✓`}),
      walk("trader",home("trader")),doit(()=>this.addRecent(e,`BOUGHT ${k} ${fm(size)}`+(trend?"":` · @${e.source}`),"g")));
    return S;
  }
  refuseTail(e,holder,deskBot){
    const R=this,k=coin(e.mint);let item=null;
    return [doit(()=>{const b=R.bots[holder];item={x:Math.round(b.x)+(b.x<300?8:-8),y:Math.round(b.y)+1,bad:true};R.items.push(item);b.carry=null;R.stamp(item.x,item.y-6)}),
      par(walk(holder,DEF[holder].home),seq({t:"walk",b:"sweeper",get to(){return [item.x-6,item.y]}},doit(()=>{R.bots.sweeper.pose="sweep"}),wait(1100),
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
      doit(()=>{R.float(fsm(e.pnl),266,96,good?P.g:P.r);this.addRecent(e,`SOLD ${k} · ${EXITNAME[ex]||ex} · ${fsm(e.pnl)}`,good?"g":"r")}));
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
  float(text,x,y,col){this.fx.push({type:"float",text,x,y,col,t0:this.t,dur:2200})}
  // ---- cards ----------------------------------------------------------------------------------------------------
  renderCards(){for(const k of ORDER)this.updateCard(k)}
  updateCard(k){
    const el=this.cardEls&&this.cardEls[k];if(!el||!this.d)return;const [v,m,mc]=this.cardVal(k);
    el.v.textContent=v;el.m.style.width=Math.max(0,Math.min(1,m||0))*100+"%";el.m.style.background=mc||DEF[k].col;el.q.textContent=this.quotes[k]||"";
  }
  cardVal(k){
    const d=this.d,t=d.today||{},lv=d.live||{},ws=d.ws||{},sel=d.selection||{};
    if(k==="chief")return [lv.armed?`REAL $ ${lv.mode==="trial"?"TRIAL":"ON"}`:"PAPER · real $ OFF",lv.safety_total?lv.safety_ok/lv.safety_total:0,lv.armed?P.a:P.g];
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
    const sw=this.stageW||W,sc=sw/W;
    for(const k of ORDER){
      const b=this.bots[k],el=this.bubEl[k];
      if(!b.bub){if(el.classList.contains("on"))el.classList.remove("on");continue}
      const txt=sw<520&&b.bub.short?b.bub.short:b.bub.text,key=txt+"|"+b.bub.tag+"|"+b.bub.tone;
      if(el._t!==key){el._t=key;el.innerHTML=`<b>${DEF[k].name}</b>${b.bub.tag==="SAMPLE"?`<span class="rm-tag">SAMPLE</span> `:""}${esc(txt)}`;
        el.className="rm-bub"+(b.bub.tone?" "+b.bub.tone:"");el._w=el.offsetWidth;el._h=el.offsetHeight}
      const cx=b.x*sc;let top=(b.y-16)*sc-el._h-7,below=false;if(top<2){top=(b.y+2)*sc+7;below=true}
      const left=Math.max(2,Math.min(sw-el._w-2,cx-el._w/2));
      el.style.transform=`translate(${Math.round(left)}px,${Math.round(top)}px)`;el.style.setProperty("--tail",Math.round(cx-left)+"px");
      el.classList.toggle("below",below);el.classList.add("on");
    }
  }
  // ---- drawing ----------------------------------------------------------------------------------------------------
  px(x,y,w,h,c){const g=this.ctx;g.fillStyle=c;g.fillRect(x,y,w,h)}
  text(s,x,y,c,al){
    const gs=[...String(s).toUpperCase()].map(ch=>GL[ch]||GL["?"]),w=gs.reduce((a,g)=>a+g.w+1,0)-1;
    if(al==="c")x-=Math.floor(w/2);else if(al==="r")x-=w;x=Math.round(x);y=Math.round(y);
    const g=this.ctx;g.fillStyle=c;
    for(const gl of gs){for(let r=0;r<5;r++)for(let q=0;q<gl.w;q++)if(gl.rows[r][q]==="1")g.fillRect(x+q,y+r,1,1);x+=gl.w+1}
  }
  outline(x,y,w,h,c){this.px(x,y,w,1,c);this.px(x,y+h-1,w,1,c);this.px(x,y,1,h,c);this.px(x+w-1,y,1,h,c)}
  draw(){
    const g=this.ctx;if(!g)return;const t=this.t;g.setTransform(this.k,0,0,this.k,0,0);g.imageSmoothingEnabled=false;
    this.drawWall(t);this.drawFloor();
    const L=[];
    for(const dk of DESKS)L.push([dk.y+dk.h,()=>this.drawDesk(dk,t)]);
    L.push([174,()=>this.drawRadar(t)],[160,()=>this.drawDock()],[170,()=>this.drawShredder(t)],[170,()=>this.drawLedger(t)],[177,()=>this.drawVault(t)],[112,()=>this.drawDoor()]);
    for(const it of this.items)L.push([it.y,()=>this.drawCard(it.x-3,it.y-6,it)]);
    for(const k of ORDER){const b=this.bots[k];L.push([b.y+.1,()=>this.drawBot(b,t)])}
    L.sort((a,b)=>a[0]-b[0]);for(const x of L)x[1]();
    this.drawFx(t);
    if(!this.awake){g.fillStyle="rgba(0,0,0,.5)";g.fillRect(0,0,W,H)}
    else if(this.d&&this.d.panic&&Math.floor(t/600)%2){g.fillStyle="rgba(255,30,60,.12)";g.fillRect(0,0,W,H)}
  }
  drawWall(t){
    this.px(0,0,W,46,P.wall);for(let x=8;x<W;x+=16)this.px(x,0,1,44,P.wall2);this.px(0,44,W,2,P.trim);
    this.drawWindow(t);this.drawScreen();this.drawHeart(t);this.drawClock();this.drawBoard(t);this.drawSafe();
  }
  drawFloor(){this.px(0,46,W,H-46,P.floor);for(let y=58;y<H;y+=12)this.px(0,y,W,1,P.tile);for(let x=12;x<W;x+=24)this.px(x,46,1,H-46,P.tile)}
  drawWindow(t){
    // SOL weather: the 24h move from Jupiter; a storm only while the bot's storm pause is really on
    const d=this.d||{},ch=(d.sol||{}).ch24,storm=!!(d.storm_until&&this.snow()<d.storm_until),mv=this.motion;
    this.px(5,5,40,30,P.frame);
    const sky=storm?"#160e2e":ch==null?"#0a1630":ch<-2?"#14233a":ch<0?"#1a3044":ch<2?"#1d4568":"#25639a";this.px(7,7,36,26,sky);
    if(storm||(ch!=null&&ch<-2)){const n=storm?12:7;for(let i=0;i<n;i++){const rx=7+((i*11+(mv?Math.floor(t/(storm?28:55)):0))%36),ry=7+((i*7+(mv?Math.floor(t/(storm?16:32)):0))%24);this.px(rx,ry,1,2,storm?"#8a8ff0":"#5f7fb0")}}
    else if(ch!=null&&ch<0){this.px(11,12,12,3,"#5d6f80");this.px(13,10,7,2,"#5d6f80");this.px(25,18,13,3,"#4f6170")}
    else if(ch!=null&&ch>=2){this.px(31,10,7,7,P.gold);this.px(30,12,9,3,P.gold);this.px(33,9,3,9,P.gold)}
    else if(ch!=null){this.px(29,10,6,6,P.gold);this.px(9,19,12,3,"#7c93a8")}
    else{this.px(12,10,1,1,"#fff");this.px(30,14,1,1,"#fff");this.px(20,24,1,1,"#cfe")}
    if(storm&&mv&&t%2600<110)this.px(7,7,36,26,"rgba(255,255,255,.55)");
    this.px(24,7,1,26,P.frame);this.px(7,19,36,1,P.frame);
    if(t-this.windowAt<1600)this.outline(4,4,42,32,P.a);
    this.text(storm?"STORM":ch==null?"SOL --":"SOL "+(ch>=0?"+":"")+ch.toFixed(1)+"%",25,37,storm||(ch!=null&&ch<0)?P.r:P.g,"c");
  }
  drawScreen(){
    // Trend Rider's real daily SOL closes, 20-day high, 50-day average and the open position's stop
    this.px(51,2,110,41,P.frame);this.px(53,4,106,37,P.scr);
    const d=this.d||{},T=d.trend||{},st=T.state||{},cl=(T.closes||[]).map(x=>x[1]).filter(v=>v>0);
    this.text("SOL 1D",56,6,P.mu);if(T.sample)this.text("SAMPLE",157,6,P.a,"r");else if(st.price)this.text("$"+Math.round(st.price),157,6,P.g,"r");
    if(cl.length<5){this.text("WAITING FOR CANDLES",106,22,P.mu,"c");return}
    const tp=(d.positions||[]).find(p=>p.book.startsWith("trend")&&p.stop);
    const vals=cl.concat([st.high20,st.sma50,tp&&tp.stop].filter(v=>v>0));let mn=Math.min(...vals),mx=Math.max(...vals);const pad=(mx-mn)*.08||1;mn-=pad;mx+=pad;
    const X0=56,X1=156,Y0=13,Y1=38,X=i=>X0+Math.round(i*(X1-X0)/(cl.length-1)),Y=v=>Math.round(Y1-(v-mn)/(mx-mn)*(Y1-Y0));
    if(st.high20){const y=Y(st.high20);for(let x=X0;x<X1;x+=3)this.px(x,y,2,1,"#008a99")}
    if(st.sma50){const y=Y(st.sma50);for(let x=X0;x<X1;x+=4)this.px(x,y,1,1,"#b07a00")}
    if(tp){const y=Y(tp.stop);for(let x=X0;x<X1;x+=3)this.px(x,y,2,1,"#b3203e")}
    let py=Y(cl[0]);
    for(let i=0;i<cl.length;i++){const x=X(i),y=Y(cl[i]),nx=i<cl.length-1?X(i+1):x+1,lo=Math.min(py,y),hi=Math.max(py,y);this.px(x,lo,1,hi-lo+1,P.g);this.px(x,y,Math.max(1,nx-x),1,P.g);py=y}
    const ly=Y(cl[cl.length-1]);this.px(X1-1,ly-1,3,3,"#ffffff");
  }
  drawHeart(t){
    // a spike for each time the bot touched its heartbeat file (every ~30 s while it runs)
    this.px(165,5,34,20,P.frame);this.px(167,7,30,16,P.scr);const ok=this.awake,b=18;
    this.px(167,b,30,1,ok?"#0b5a37":"#7a1024");
    if(ok)for(const tb of this.beats){const x=Math.round(196-(t-tb)/1000);if(x<169||x>196)continue;this.px(x-2,b-1,1,1,P.g);this.px(x-1,b-6,1,6,P.g);this.px(x,b,1,3,P.g);this.px(x+1,b-2,1,2,P.g)}
    this.text(ok?"PULSE":"NO PULSE",168,9,ok?P.mu:P.r);
  }
  drawClock(){this.px(165,27,34,12,P.frame);this.px(167,29,30,8,P.scr);this.text(this.d?hhmm(this.snow()):"--:--",182,30,P.g,"c")}
  drawBoard(t){
    // the traders the bot picked; it lights up when the bot re-selects
    this.px(205,3,66,40,"#4a3220");this.px(207,5,62,36,"#6b4a2b");this.text("TRADERS",238,7,"#f0d8b0","c");
    const pk=((this.d||{}).selection||{}).picked||[],fl=t-this.boardAt<2600;
    if(!pk.length)this.text("NONE",238,22,"#f0d8b0","c");
    pk.slice(0,3).forEach((h,i)=>{const x=210+i*20,y=14;this.px(x,y,17,23,fl?"#ffffff":P.paper);this.px(x,y,17,4,"#3fa9ff");this.px(x+8,y-1,2,2,P.r);
      this.text(String(h).replace(/[^a-z0-9]/gi,"").slice(0,3)||"?",x+9,y+8,P.ink,"c");this.px(x+3,y+16,11,1,"#9aa9b0");this.px(x+3,y+19,8,1,"#9aa9b0")});
  }
  drawSafe(){
    // the real-money wallet: locked unless /live arm or /live trial is on
    const lv=(this.d||{}).live||{},on=!!lv.armed;
    this.px(277,4,38,38,"#2a3640");this.px(279,6,34,34,"#1d2730");
    if(on){this.px(279,6,12,34,"#0e1418");this.px(281,8,10,30,shade(P.a,-.6))}
    this.px(301,13,7,7,"#55636e");this.px(303,15,3,3,"#9aa7b2");
    const lc=on?P.a:P.g;this.px(283,15,9,7,lc);this.px(284,11,1,4,lc);this.px(290,11,1,4,on?"#1d2730":lc);this.px(284,10,7,1,lc);this.px(287,17,2,2,"#1d2730");
    this.text("REAL $",296,27,P.mu,"c");this.text(on?(lv.mode==="trial"?"TRIAL":"ON"):"OFF",296,33,on?P.a:P.g,"c");
  }
  drawDoor(){
    this.px(302,76,18,7,"#06301d");this.text("EXIT",311,77,P.g,"c");this.px(311,84,9,30,P.frame);
    this.px(313,86,7,28,this.door==="open"?"#2d6b4c":this.door==="jam"?"#4a0f1c":"#10241b");
    if(this.door==="open")this.px(313,86,2,28,"#9fffd0");
    if(this.door==="jam")for(let i=0;i<5;i++){this.px(314+i,95+i,1,1,P.r);this.px(318-i,95+i,1,1,P.r)}
  }
  drawDesk(dk,t){
    const {k,x,y,w,h}=dk,d=this.d||{};
    if(k==="vet"||k==="guard"||k==="trader"||k==="chief"){this.px(x+12,y-11,16,11,P.frame);this.px(x+13,y-10,14,8,P.scr);this.px(x+19,y-1,2,1,P.frame)}
    this.px(x,y,w,4,P.top);this.px(x,y,w,1,P.edge);this.px(x,y+4,w,h-4,P.desk);this.px(x+1,y+h,2,2,P.leg);this.px(x+w-3,y+h,2,2,P.leg);
    this.text(dk.lbl,x+w/2,y+6,P.mu,"c");
    if(k==="ct"){this.px(x+11,y-2,14,3,"#2a2f2c");this.px(x+12,y-1,12,1,"#3a403c");this.px(x+30,y+1,2,1,P.r)}
    if(k==="chart"){this.px(x+10,y+1,20,2,"#2f4a3c")}
    if(k==="vet"){const L=this.vetRows;for(let i=0;i<4;i++){const v=L&&(!this.motion||t-this.vetAt>350+i*550)?L[i]:0;this.px(x+14,y-9+i*2,1,1,v>0?P.g:v<0?P.r:"#0b3d28");this.px(x+16,y-9+i*2,9,1,v>0?"#0b6b40":v<0?"#6b0b22":"#0b2a1d")}}
    if(k==="guard"){const B=(d.books||[]).filter(b=>!b.live);B.slice(0,3).forEach((b,i)=>{const u=b.day_pnl!=null&&b.daily_limit&&b.start?Math.max(0,-b.day_pnl/(b.start*b.daily_limit)):0;
      this.px(x+14,y-9+i*3,12,2,"#0b2a1d");this.px(x+14,y-9+i*3,Math.max(1,Math.round(12*Math.min(1,u))),2,u>.8?P.r:u>.5?P.a:BOOKCOL[b.book]||P.g)})}
    if(k==="trader"){const tt=d.today||{};this.text(String(tt.filled||0),x+17,y-9,P.g,"c");this.text(String(tt.sold||0),x+23,y-9,P.c,"c")}
    if(k==="chief"){(d.books||[]).slice(0,4).forEach((b,i)=>this.px(x+15+i*3,y-7,2,2,b.state==="ok"?(BOOKCOL[b.book]||P.g):b.state==="off"?"#203028":P.r));
      this.px(x+28,y+1,4,2,d.panic?P.r:"#5a1020")}
  }
  drawRadar(t){
    // turns only while the live wallet stream is connected; a dot for each real wallet transaction
    const on=!!((this.d||{}).ws||{}).connected&&this.awake,cx=22,cy=146,mv=this.motion;
    this.px(14,163,16,6,P.frame);this.px(20,155,4,9,"#24402f");
    for(let yy=-11;yy<=11;yy++){const w=Math.round(Math.sqrt(121-yy*yy));this.px(cx-w,cy+yy,2*w,1,"#0b2419")}
    for(let a=0;a<360;a+=7){const r=a*Math.PI/180;this.px(Math.round(cx+Math.cos(r)*11),Math.round(cy+Math.sin(r)*11),1,1,"#1f5a3e");if(a%14===0)this.px(Math.round(cx+Math.cos(r)*6),Math.round(cy+Math.sin(r)*6),1,1,"#174a33")}
    if(on){const a=mv?t/1000*Math.PI*.5:-Math.PI/4;for(let r=1;r<11;r++)this.px(Math.round(cx+Math.cos(a)*r),Math.round(cy+Math.sin(a)*r),1,1,P.g)}
    this.blips=this.blips.filter(b=>t-b.t0<5000);
    for(const b of this.blips){const al=1-(t-b.t0)/5000;this.px(Math.round(cx+Math.cos(b.a)*b.r),Math.round(cy+Math.sin(b.a)*b.r),2,2,`rgba(0,229,255,${al.toFixed(2)})`)}
    if(t-this.ringAt<1400){const r=Math.round(2+(t-this.ringAt)/140);for(let a=0;a<360;a+=12){const q=a*Math.PI/180;this.px(Math.round(cx+Math.cos(q)*r),Math.round(cy+Math.sin(q)*r),1,1,P.c)}}
    this.px(36,136,2,2,on?P.g:P.r);this.text("WALLETS",22,171,P.mu,"c");
  }
  drawDock(){const home=Math.hypot(this.bots.sweeper.x-DEF.sweeper.home[0],this.bots.sweeper.y-DEF.sweeper.home[1])<1;
    this.px(60,163,18,5,"#1b3a2c");this.px(61,164,16,3,"#0f241b");this.px(76,161,2,1,home?P.g:P.mu)}
  drawShredder(t){
    this.px(96,146,18,22,"#24302b");this.px(97,147,16,20,"#1a2420");this.px(99,145,12,2,"#05080a");
    this.text("BIN",105,151,P.mu,"c");this.text(String(this.binCount),105,158,this.binCount?P.r:P.mu,"c");
    const a=t-this.shredAt;if(a<1300)for(let i=0;i<6;i++){const sy=147+((a/55+i*4)%16);this.px(99+i*2,Math.round(sy),1,2,P.paper)}
  }
  drawLedger(t){
    const fl=t-this.ledgerAt<1800;this.px(135,157,6,10,P.wood);this.px(130,166,16,3,P.wood);
    this.px(128,148,20,9,fl?"#ffffff":P.paper);this.px(137,148,1,9,"#9aa9b0");for(let i=0;i<3;i++){this.px(130,150+i*2,5,1,"#7a8a84");this.px(140,150+i*2,6,1,"#7a8a84")}
    this.text("BOOKS",138,172,P.mu,"c");
  }
  drawVault(t){
    this.px(164,138,144,40,"#0d1d16");this.px(164,138,144,1,P.edge);this.px(166,157,140,2,P.wood);this.text("VAULT",168,169,P.mu);
    const extra=(this.d&&this.d.positions||[]).length-8;if(extra>0)this.text("+"+extra+" MORE",302,169,P.mu,"r");
    for(const j of this.jars)if(!j.hidden)this.drawJar(j,this.jarX(j));
  }
  drawJar(j,x){
    const p=j.pos||{},ratio=p.value!=null&&p.cost?p.value/p.cost:p.ch!=null?1+p.ch:1;
    this.px(x,145,9,12,"#2c4a3d");this.px(x+1,146,7,10,"#081510");
    const lvl=Math.max(1,Math.min(10,Math.round(ratio/1.4*10)));this.px(x+1,156-lvl,7,lvl,ratio>=1?"#119a5c":"#a3203a");
    if(j.sl!=null){const y=156-Math.round((1-j.sl)/1.4*10);this.px(x+1,y,7,1,P.r)}
    if(j.tp!=null){const y=156-Math.max(1,Math.round((1+j.tp)/1.4*10));this.px(x+1,y,7,1,P.g)}
    this.px(x,143,9,2,BOOKCOL[j.book]||P.g);this.text(coin3(j.mint),x+4,161,P.tx,"c");
    if(j.retry){this.px(x+6,139,3,1,P.a);this.px(x+7,140,1,2,P.a)}
  }
  drawCard(x,y,it){this.px(x,y,7,8,P.paper);this.px(x,y,7,2,it.bad?P.r:BOOKCOL[it.book]||P.g);this.px(x+1,y+3,5,1,"#9aa9b0");this.px(x+1,y+5,4,1,"#9aa9b0");
    if(it.bad)for(let i=0;i<5;i++){this.px(x+1+i,y+2+i,1,1,P.r);this.px(x+5-i,y+2+i,1,1,P.r)}}
  drawBot(b,t){
    const d=b.d,x=Math.round(b.x)-5,y0=Math.round(b.y)-13,col=d.col,dk=shade(col,-.45),hi=shade(col,.45),sh=SH[d.shape];
    const walking=b.path.length>0&&this.motion,ph=walking?Math.floor(t/130)%2:0,bob=walking&&ph?-1:0,y=y0+bob;
    this.px(x+1,y0+12,9,2,"rgba(0,0,0,.45)");
    if(d.shape!=="ghost"){this.px(x+3,y0+11-(walking&&ph?1:0),2,2,dk);this.px(x+6,y0+11-(walking&&!ph?1:0),2,2,dk)}
    for(let r=0;r<11;r++){const row=d.shape==="ghost"&&r===10&&walking&&ph?"..X.XX.XX.X":sh[r];for(let c=0;c<11;c++){const ch=row[c];if(ch===".")continue;this.px(x+c,y+r,1,1,ch==="X"?col:ch==="H"?hi:dk)}}
    const ey=y+EYE[d.shape],sleep=b.pose==="sleep"||b.k==="ct";
    if(sleep){this.px(x+3,ey+1,2,1,P.ink);this.px(x+6,ey+1,2,1,P.ink)}
    else{this.px(x+3,ey,2,2,"#fff");this.px(x+6,ey,2,2,"#fff");const o=b.face>0?1:0;this.px(x+3+o,ey+1,1,1,P.ink);this.px(x+6+o,ey+1,1,1,P.ink)}
    const k=b.k;
    if(k==="scout"){this.px(x+5,y-3,1,3,"#1d5f99");this.px(x+5,y-4,1,1,t-this.antennaAt<1500?P.c:"#7fc6ff")}
    else if(k==="guard"){this.px(x+2,y-1,7,2,"#3a2a10");this.px(x+1,y+1,9,1,"#2a1d0a");this.px(x+5,y+7,1,1,P.gold)}
    else if(k==="trader"){this.px(x+1,y-1,9,1,"#0f3d24");this.px(x,y+3,1,3,"#0f3d24");this.px(x+10,y+3,1,3,"#0f3d24");this.px(x+9,y+7,2,1,"#0f3d24")}
    else if(k==="chart"){this.px(x+2,y+3,8,1,"#003b44");this.px(x+5,y+4,1,1,"#003b44")}
    else if(k==="vet"){this.px(x+2,y+3,8,1,"#5a6a8a");if(b.pose==="inspect"){const o=this.motion?Math.floor(t/380)%2:0;this.px(x+9,y+1+o,3,3,"#9fb2d8");this.px(x+10,y+2+o,1,1,"#e8f2ff");this.px(x+11,y+4+o,1,3,P.wood)}}
    else if(k==="chief"){this.px(x+3,y-2,5,2,P.gold);this.px(x+3,y-3,1,1,P.gold);this.px(x+5,y-3,1,1,P.gold);this.px(x+7,y-3,1,1,P.gold)}
    else if(k==="sweeper"){if(b.pose==="sweep"){const o=this.motion&&Math.floor(t/120)%2?2:0;this.px(x+9,y+2,1,9,P.wood);this.px(x+6+o,y+10,5,2,"#e6c35c")}else{this.px(x+11,y-2,1,12,P.wood);this.px(x+10,y+9,3,3,"#e6c35c")}}
    else if(k==="ct"){this.px(x+4,y+7,3,1,"#7fdcff");this.px(x+5,y+6,1,1,"#7fdcff");const z=this.motion?Math.floor(t/900)%3:1;this.px(x+11,y-1-z*2,3,1,P.mu);this.px(x+12,y-z*2,1,1,P.mu);this.px(x+11,y+1-z*2,3,1,P.mu)}
    if(b.pose==="sleep"&&k!=="ct"){this.px(x+11,y-2,3,1,P.mu);this.px(x+12,y-1,1,1,P.mu);this.px(x+11,y,3,1,P.mu)}
    if(b.carry){const cx=x+2,cy=y-10;if(b.carry.type==="card")this.drawCard(cx,cy,b.carry);else{this.px(cx,cy,7,8,"#2c4a3d");this.px(cx+1,cy+3,5,4,b.carry.up?"#119a5c":"#a3203a");this.px(cx,cy-1,7,2,BOOKCOL[b.carry.book]||P.g)}}
  }
  drawFx(t){
    this.fx=this.fx.filter(f=>t-f.t0<f.dur);
    for(const f of this.fx){const a=(t-f.t0)/f.dur;
      if(f.type==="float")this.text(f.text,f.x,Math.round(f.y-a*12),f.col,"c");
      else if(f.type==="stamp"&&Math.floor(a*6)%2===0)for(let i=0;i<9;i++){this.px(f.x-4+i,f.y-4+i,1,1,P.r);this.px(f.x+4-i,f.y-4+i,1,1,P.r)}}
  }
}
window.ZRoom={create:(root,o)=>new Room(root,o),plain};
})();
