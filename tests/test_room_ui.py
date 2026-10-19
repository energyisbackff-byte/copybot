"""Robot room in a real browser (skipped when Playwright/Chromium are not installed, e.g. on the server): the job panel
shows every step of a buy check with the bot's own numbers, the CA checks one line at a time, a refusal stops at the
right desk, the CT scan and the perps hourly jobs list their real results, and the idle panel lists real due jobs.
Run: python3 tests/test_room_ui.py"""
import os,sys,json,shutil,subprocess,tempfile
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PW=os.environ.get("PLAYWRIGHT_NODE","/opt/npm-tools/node_modules/playwright")
node=shutil.which("node")
if not node or not os.path.exists(PW):print("(playwright not installed: browser test of the room skipped)");sys.exit(0)
js=r"""
const {chromium}=require(process.argv[2]);const fs=require('fs');
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:390,height:900}});const errs=[];p.on('pageerror',e=>errs.push(e.message));
await p.setContent('<div id="r"></div><script>'+fs.readFileSync(process.argv[3],'utf8')+'</script>');
const out=await p.evaluate(()=>{
 const now=Date.now()/1000,M="AgNTs9kQe7vYx1p6mZr4HcWfLb2Tq8UoDiJn5aKpump";
 const base={now,online:true,last_poll:now-10,heartbeat_age:5,loop_age:10,ws:{connected:true,notifications:1},sol:{price:186,ch24:1.2},books:[{book:"copy",equity:51,pnl:-1,open:0,state:"ok"}],
  positions:[],pending_sells:[],events:[],today:{proposals:0,filled:0,refused:0,by_station:{},vet_checked:0,vet_reasons:{}},marks:{last:now-100,every:300},
  selection:{at:now-3600,next_at:now+20*3600,picked:["whale"],checked:3,qualified:1},trend:{state:null,closes:[],ohlc:[]},live:{armed:false,safety_ok:1,safety_total:2},
  ct:{connected:true,alive:now-20,ts:now-60,x:false,metas:6,trending:20,top:[]},perps:{alive:now-5,ts:now-900,all:[],signals:[],positions:[]},risk:{},
  cfg:{exits:{enabled:true,sl:.15,tp:.3,max_hold:24},top10:.3,roundtrip:.06,max_age:120,min_liq:50000,max_impact:.03,exit_check_s:30,poll_s:120,poll_down_s:25,narr_cycle_s:300,a_plus:85}};
 const room=ZRoom.create(document.getElementById("r"),{manual:true,autoReplay:false,rounds:false});
 const run=(ms)=>{for(let t=0;t<ms;t+=50)room.advance(50,true)};
 // the last job, as the panel lists it (title, rows, each row's state, text and sub-checks)
 const job=()=>{const J=room.J;return J?J.title+"\n"+J.rows.map(r=>`${r.st} ${r.l}: ${r.x}`+r.sub.map(s=>"\n  "+(s[1]>0?"✓":s[1]<0?"✗":"·")+" "+s[0]).join("")).join("\n"):""};
 const panel=()=>room.root.querySelector(".rm-job").innerText;
 room.update(JSON.parse(JSON.stringify(base)));run(30000);const idle=panel();
 const d=JSON.parse(JSON.stringify(base));d.now=now+1;
 d.events=[{id:"e1",kind:"buy",ts:now,book:"copy",source:"whale",mint:M,decision:"refused",reason:"safety veto: top 10 holders own 41%",size:5,station:"vet",
   features:{liq:182000,mint_auth:null,freeze_auth:null,top10:0.41},fill:{detect_ms:1800}}];
 room.update(d);run(9000);const mid=panel();run(30000);const bad=job();
 const d2=JSON.parse(JSON.stringify(d));d2.now=now+60;d2.events=d.events.concat([{id:"e2",kind:"buy",ts:now+50,book:"narrative",source:"narrative:AI agents",mint:M,decision:"filled",reason:"passed every check",size:5,station:"vault",
   features:{liq:182000,mint_auth:null,freeze_auth:null,top10:0.18,roundtrip:0.031,stop_pct:0.094,tp1_pct:0.094,costs_rt:0.025,stop_src:"1.5x ATR15m"},fill:{total_ms:1500,impact:0.0034,usd:5}}]);
 room.update(d2);run(60000);const good=job();
 const d3=JSON.parse(JSON.stringify(d2));d3.now=now+120;d3.ct=Object.assign({},d3.ct,{ts:now+110,x:true,x_read:96,x_left:204,top:[{narrative:"ds:ai",name:"AI agents",state:"accelerating",score:88,grade:"A+",ticker:"AGNT",mint:M,gates:[]}]});
 room.update(d3);run(30000);const ct=job();
 const d4=JSON.parse(JSON.stringify(d3));d4.now=now+180;d4.perps=Object.assign({},d4.perps,{ts:now+170,all:[{coin:"ATOM",direction:null,why:"chop: averages only 0.18% apart"},{coin:"XRP",direction:1,why:"long: closed above the 20-candle high in a 1.1% trend"}]});
 room.update(d4);run(20000);const pz=job();
 return {idle,mid,bad,good,ct,pz};
});
console.log(JSON.stringify(Object.assign(out,{errs})));await b.close()})();
"""
T=tempfile.mkdtemp();open(os.path.join(T,"t.js"),"w").write(js)
r=subprocess.run([node,os.path.join(T,"t.js"),PW,os.path.join(R,"dashboard","room.js")],capture_output=True,text=True,timeout=180)
try:o=json.loads(r.stdout.strip().splitlines()[-1])
except Exception:print(r.stdout[-2000:],r.stderr[-2000:]);sys.exit(1)
res={}
res["no script errors in the browser"]=not o["errs"]
res["idle: the panel lists the real jobs that are due next"]="next real jobs" in o["idle"] and "POLL" in o["idle"] and "CT SCAN" in o["idle"] and "PERPS" in o["idle"] and "TRADERS" in o["idle"]
res["buy check in progress: rows in the gate order"]=all(w in o["mid"] for w in("BUY CHECK","TRADER","RULES","STORM","CA CHECK","QUOTE","EXIT TEST"))
res["refused at the CA desk: liquidity ok, top-10 41% fails, later steps not reached"]="liquidity $182k" in o["bad"] and "top-10 holders own 41%" in o["bad"] and "skip QUOTE" in o["bad"] and "not bought" in o["bad"]
res["A+ narrative buy: every CA line, the exit plan and the fee edge, bought"]=all(w in o["good"] for w in("A+ narrative","supply locked","no freeze authority","top-10 holders own 18%","EXIT PLAN","stop -9.4%","fee edge","bought $5.00"))
res["CT scan job: X reads, market themes, scores, A+ handed over"]=all(w in o["ct"] for w in("CT SCAN","96 posts read","6 trending themes","88 A+","queued for the Narrative Rider"))
res["perps hourly job: one row per coin with its verdict"]="PERPS HOURLY" in o["pz"] and "ATOM" in o["pz"] and "chop" in o["pz"] and "LONG setup" in o["pz"]
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v]
if bad:print(json.dumps(o,indent=1)[:3000])
print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
