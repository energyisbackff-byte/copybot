"""Bot room test: /api/room on a made-up database (no network, no bot running). Checks that every refusal reason lands at
the right desk in the real gate order, fills and sells carry their details, nothing secret leaves the database,
the token is required, and room.js is served. Run: python3 tests/test_room.py"""
import sys,os,json,time,tempfile,shutil,threading,importlib.util,urllib.request,urllib.error,subprocess,math
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);os.chdir(R)
from db import DB
T=tempfile.mkdtemp();APP=os.path.join(T,"app");os.makedirs(os.path.join(APP,"dashboard"))
for f in("server.py","index.html","room.js"):shutil.copy(os.path.join(R,"dashboard",f),os.path.join(APP,"dashboard",f))
shutil.copy(os.path.join(R,"config.yaml"),APP);open(os.path.join(APP,"dashboard","token.txt"),"w").write("tok-test-123")
DBP=os.path.join(T,"bot.db");HB=os.path.join(T,"heartbeat");open(HB,"w").write("1")
os.environ.update(COPYBOT_DB=DBP,COPYBOT_HEARTBEAT=HB,DASH_PORT="0")
db=DB(DBP);now=time.time();res={}
if now%86400<4000:now=now//86400*86400+4000      # keep every made-up event inside "today" (UTC) whenever the test runs
os.utime(HB,(now-5,now-5))
M1="7VertkgF9KLhxxJXHX6uaWuoYZTP9LdGj2bWmVXVpump";M2="nxPqZ4F7ux8Vph5NFu25rEP6Kx9Gr1U6Fmj36Rjpump";SOL="So11111111111111111111111111111111111111112"
REASONS=[("panic stop active (/panic)","guard"),("book paused (/book)","guard"),("daily loss limit hit","guard"),("drawdown switch tripped","guard"),
  ("stale signal: seen 141s after the trade","guard"),("already holding (add-on ignored)","guard"),("max open positions","guard"),
  ("size $0.00 below minimum (coin exposure cap)","guard"),("market storm: SOL -5.3% in 1h","window"),("liquidity $12,300 < $50,000","vet"),
  ("safety veto: creator can still mint","vet"),("safety veto: top 10 holders own 41%","vet"),("safety veto: check failed (Timeout)","vet"),
  ("FILL FAILED: no Jupiter route/quote (HTTP 400)","trader"),("FILL FAILED: price impact 4.1% > 3%","trader"),
  ("sell check: buy then sell loses 9.8%","exit"),("sell check: no sell route","exit")]
t=now-3000
for i,(why,_) in enumerate(REASONS):
    db.ins("b_signals",ts=t+i,book="copy",source="whale",mint=M2,side="buy",decision="refused",reason=why,size_usd=5.0,
           features=json.dumps({"liq":61000.0,"top10":float("nan")} if i==15 else {"liq":61000.0}))
# a filled buy with its fill record, an open position, then a stop-loss sell of an earlier position
db.ins("b_fills",ts=now-600,book="copy",sig="b1",trader="whale",mint=M1,side="buy",status="filled",detect_ms=1800,total_ms=3100,impact=0.0034,slip_bps=228,our_px=0.0019,size_usd=5.0,fee_usd=0.31)
db.ins("b_signals",ts=now-600,book="copy",source="whale",mint=M1,side="buy",decision="filled",reason="passed every check",size_usd=5.0,
       features=json.dumps({"liq":182000.0,"program":"token","mint_auth":None,"freeze_auth":None,"top10":0.18,"roundtrip":0.031}))
db.ins("b_positions",book="copy",trader="whale",mint=M1,tokens=2719.2,cost=5.31,opened=now-600,status="open",open_sig="b1",meta="{}")
db.ins("b_positions",book="copy",trader="whale",mint=M2,tokens=0,cost=0,opened=now-4000,status="closed",closed=now-300,realized=-0.94,open_sig="b0")
db.ins("b_trades",ts=now-300,book="copy",trader="whale",mint=M2,side="sell",usd=4.37,tokens=2719,price=0.0016,pnl=-0.94,sig="stop_loss:copy:2:1",note="stop-loss -16.4% · impact 0.00%")
db.ins("b_fills",ts=now-300,book="copy",sig="stop_loss:copy:2:1",trader="whale",mint=M2,side="sell",status="filled",reason="stop_loss",fill_ts=now-300)
db.ins("b_fills",ts=now-200,book="copy",sig="x",trader="whale",mint=M1,side="sell",status="failed",reason="take_profit: no route (HTTP 500)",fill_ts=now-200)
# Trend Rider: open paper position with its stop, daily candles, state
db.ins("b_positions",book="trend",trader="trend-rider",mint=SOL,tokens=0.1,cost=18.0,opened=now-86400,status="open",open_sig="t1",meta=json.dumps({"v":2,"stop":170.0,"entry":180.0,"hi":185.0}))
for d in range(70):db.c.execute("insert into candles values(?,?,?,?,?,?,?,?)",("SOL-USD",86400,int(now//86400-70+d)*86400,1,1,1,150+d,1))
db.c.commit()
db.kv_set("trend:state",dict(t=now-3600,ready=True,price=187.4,close=186.9,sma20=180.2,sma50=171.0,high20=190.3,uptrend=True,signal=False))
# selection, equity marks, books, stream
for i,(h,ok) in enumerate([("whale",1),("shark",0),("fish",0)]):db.c.execute("insert into decisions values(?,?,?,?,?,?)",(now-7200,h,"W"+str(i),ok,"PASS: 40 trades" if ok else "profit factor 0.9 < 1.2","{}"))
db.c.commit();db.kv_set("selected",[{"handle":"whale","wallet":"W0","source":"fomo","reason":"PASS"}]);db.kv_set("selected_at",now-7200)
for b,eq in(("copy",51.06),("consensus",25.0),("trend",30.2)):
    db.kv_set(b+":cash",eq);db.kv_set(b+":last_equity",eq);db.kv_set(b+":day_start",{"day":time.strftime("%Y-%m-%d",time.gmtime(now)),"eq":52.0 if b=="copy" else eq})
    db.kv_set(b+":limits",{"start":52 if b=="copy" else eq,"daily":0.15});db.ins("b_equity",ts=now-120,book=b,equity=eq,cash=eq)
db.kv_set("copy:pending_sells",{"1":{"first":now-200,"tries":2,"next":now+60,"reason":"take_profit","note":"take-profit +31%"}})
db.kv_set("ws",{"connected":True,"notifications":41,"reconnects":0,"last_msg":now-20});db.kv_set("last_poll",now-30)
# secrets that must never appear in the room data
SECRETS=["111222","987654321","333444","appr-secret-id"]
db.kv_set("live:arm_code",{"code":"111222","exp":now+60,"mode":"arm"});db.kv_set("tg_chat_id","987654321");db.kv_set("live:withdraw_code",{"code":"333444"})
db.kv_set("live:approval",{"id":"appr-secret-id","status":"pending","size":9.5,"exp":now+300});db.kv_set("boot_id","bootA")
db.kv_set("live:armed",{"ts":now,"boot":"bootA","until":now+86400,"mode":"trial"})
db.kv_set("live:readiness",{"ts":now,"safety":[[True,"signer running"],[False,"home not set"]],"strategy":[],"tier2":[],"extra":[]})
# ---- load the dashboard server from the temp copy, without network ----
spec=importlib.util.spec_from_file_location("dash_server",os.path.join(APP,"dashboard","server.py"));S=importlib.util.module_from_spec(spec);spec.loader.exec_module(S)
import types;S.time=types.SimpleNamespace(time=lambda:now,strftime=time.strftime,gmtime=time.gmtime)
S.prices=lambda ms:{M1:0.0021,SOL:186.0}
S.sol_now=lambda:(186.0,1.3)
st=S.room_state();body=S.room_json().decode();j=json.loads(body)
ev={e["id"]:e for e in j["events"]}
buys=[e for e in j["events"] if e["kind"]=="buy"]
res["every refusal reason lands at the right desk"]=all(S.room_station(w)==s for w,s in REASONS) and \
    all(e["station"]==dict(REASONS).get(e["reason"],"vault") for e in buys)
bad=[(w,S.room_station(w),s) for w,s in REASONS if S.room_station(w)!=s]
if bad:print("wrong desks:",bad)
res["trend-only reasons go to the chart desk"]=S.room_station("late: more than 6h after the daily close","trend-rider")=="chart" and \
    S.room_station("costs $0.031 > 25% of the $0.30 risk","trend-rider")=="chart" and S.room_station("price $190.10 moved past the entry rules","trend-rider")=="chart"
f=[e for e in buys if e["decision"]=="filled"][0]
res["filled buy carries its fill: latency, impact, slip"]=f["station"]=="vault" and f["fill"]["detect_ms"]==1800 and f["fill"]["impact"]==0.0034 and \
    f["fill"]["slip_bps"]==228 and f["features"]["top10"]==0.18 and f["features"]["roundtrip"]==0.031
s=[e for e in j["events"] if e["kind"]=="sell"][0]
res["sell carries why, P&L, % and hold time"]=s["exit"]=="stop_loss" and s["pnl"]==-0.94 and abs(s["pct"]-(-0.94/5.31))<1e-9 and abs(s["held"]-3700)<2
res["failed sale shows up for the retry animation"]=any(e["kind"]=="sell_failed" for e in j["events"]) and j["pending_sells"][0]["tries"]==2 and j["pending_sells"][0]["mint"]==M1
res["events are in time order with unique ids"]=[e["ts"] for e in j["events"]]==sorted(e["ts"] for e in j["events"]) and len(ev)==len(j["events"])
td=j["today"]
res["today's counts per desk"]=td["proposals"]==18 and td["filled"]==1 and td["refused"]==17 and td["by_station"]["guard"]==8 and td["by_station"]["vet"]==4 \
    and td["vet_checked"]==1+4+2+2 and td["sold"]==1
P={p["book"]:p for p in j["positions"]}
res["open positions priced, with stop/target"]=abs(P["copy"]["ch"]-(2719.2*0.0021/5.31-1))<1e-9 and P["copy"]["sl"]==0.15 and P["copy"]["tp"]==0.30 and \
    P["trend"]["stop"]==170.0 and P["trend"]["entry"]==180.0 and P["trend"]["sl"] is None
res["trader board: picks and counts"]=j["selection"]["picked"]==["whale"] and j["selection"]["checked"]==3 and j["selection"]["qualified"]==1
res["Trend Rider: state and 60 daily closes"]=j["trend"]["state"]["high20"]==190.3 and len(j["trend"]["closes"])==60
cb={b["book"]:b for b in j["books"]}
res["books: today's P&L vs the daily limit"]=abs(cb["copy"]["day_pnl"]-(51.06-52.0))<1e-9 and cb["copy"]["daily_limit"]==0.15
res["real money: armed state, safety count, approval without its id"]=j["live"]["armed"] and j["live"]["mode"]=="trial" and j["live"]["safety_ok"]==1 and \
    j["live"]["safety_total"]==2 and j["live"]["approval"]=={"size":9.5,"exp":j["live"]["approval"]["exp"]}
res["no secrets in the room data (codes, chat id, approval id)"]=not any(x in body for x in SECRETS)
res["NaN inside stored features becomes null (valid JSON)"]="NaN" not in body and ev[[k for k,e in ev.items() if e.get("features",{}).get("liq")==61000.0 and "top10" in e.get("features",{})][0]]["features"]["top10"] is None
res["CT honestly marked as not built"]=j["ct"]["connected"] is False
res["bot online from heartbeat + last poll"]=j["online"] is True
db.kv_set("last_poll",now-1000);db.kv_set("selected_at",now-30*3600);S._cache.clear();x=S.room_state()
res["busy main loop (re-selecting traders) is not 'offline'"]=x["online"] is True and abs(x["loop_age"]-1000)<1 and x["selection_due"] is True
db.kv_set("last_poll",now-30);db.kv_set("selected_at",now-7200)
db.kv_set("boot_id","bootB");S._cache.clear()
res["a restart (new boot id) means real money is off"]=S.room_state()["live"]["armed"] is False
# ---- HTTP: token required, room.js served, CSP present ----
srv=S.ThreadingHTTPServer(("127.0.0.1",0),S.H);port=srv.server_address[1];threading.Thread(target=srv.serve_forever,daemon=True).start()
def get(path,tok=None):
    r=urllib.request.Request(f"http://127.0.0.1:{port}{path}",headers={"x-token":tok} if tok else {})
    try:x=urllib.request.urlopen(r,timeout=10);return x.status,x.headers,x.read()
    except urllib.error.HTTPError as e:return e.code,e.headers,e.read()
c1,_,_=get("/api/room");c2,h2,b2=get("/api/room","tok-test-123");c3,h3,b3=get("/room.js");c4,_,_=get("/api/room","wrong")
res["/api/room needs the token"]=c1==403 and c4==403 and c2==200 and json.loads(b2)["mode"]=="paper"
res["/room.js served as JavaScript with the CSP"]=c3==200 and "javascript" in h3["content-type"] and b"ZRoom" in b3 and "script-src 'self'" in h3["content-security-policy"]
c5,_,b5=get("/");res["page loads room.js and has the room window"]=c5==200 and b'<script src="room.js"></script>' in b5 and b'id="roomWin"' in b5
srv.shutdown()
# ---- an old database (before the books version) gives a clear message, not a crash ----
old=os.path.join(T,"old.db");import sqlite3;sqlite3.connect(old).execute("create table kv(k text primary key,v text)").connection.commit()
S.DB=old;S._cache.clear()
try:S.room_state();res["old database -> clear message"]=False
except RuntimeError as e:res["old database -> clear message"]="send /update" in str(e)
# ---- room.js: plain-words reasons (only if node is installed) ----
node=shutil.which("node")
if node:
    js=f"""globalThis.window=globalThis;require({json.dumps(os.path.join(R,'dashboard','room.js'))});const p=window.ZRoom.plain;
    console.log(JSON.stringify([p("liquidity $12,300 < $50,000"),p("safety veto: creator can still mint"),p("sell check: buy then sell loses 9.8%"),
    p("stale signal: seen 141s after the trade"),p("FILL FAILED: price impact 4.1% > 3%"),p("market storm: SOL -5.3% in 1h"),p("something new")]))"""
    out=json.loads(subprocess.run([node,"-e",js],capture_output=True,text=True,timeout=30).stdout)
    res["room.js explains refusals in plain words"]=out==["liquidity only $12,300 (need $50,000)","the creator can still mint more coins",
        "buying then selling right away loses 9.8%","too late: we saw the trade 141s after it happened","our buy would move the price 4.1% (max 3%)",
        "SOL fell 5.3% within an hour, so buying is paused","something new"]
    if not res["room.js explains refusals in plain words"]:print(out)
else:print("(node not installed: room.js wording test skipped)")
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");shutil.rmtree(T,ignore_errors=True);sys.exit(1 if bad else 0)
