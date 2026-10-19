"""New Telegram commands (cmds.py) and the daily operations (ops.py) on a made-up database, no network.
Checks every command answers on a phone-width message, /pause and /resume, the code-confirmed /risk clear and
/risk reset, the morning report (once a day), the monthly checks (once a month), the hourly digest, the stalled-thread
alarm, and that main.py really routes the commands and starts the background parts. Run: python3 tests/test_ops.py"""
import sys,os,time,json,tempfile,re,html
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);os.chdir(R)
T=tempfile.mkdtemp();os.environ.update(HELIUS_API_KEY="x",COPYBOT_MEMORY=os.path.join(T,"memory"),TELEGRAM_BOT_TOKEN="",TELEGRAM_ALERTS_CHAT_ID="",COPYBOT_DB=os.path.join(T,"bot.db"))
import yaml,ops,cmds,risk,ledger,notify
from db import DB
from tgfmt import plain,W
cfg=yaml.safe_load(open("config.yaml"));db=DB(os.environ["COPYBOT_DB"]);res={};S=[]
class N:
    token=""
    def send(s,t,buttons=None,tier="instant",digest=None):S.append((tier,plain(t)))
    def flush_digest(s,every_s=3600,force=False):S.append(("flush",""));return 0
def txt(t):return html.unescape(re.sub(r"<[^>]+>","",t))
def fits(t):
    body=txt(t).split("\n")[1:]
    return all(len(l)<=W for l in body if l and not l.startswith("root@"))
now=time.time();M1="7VertkgF9KLhxxJXHX6uaWuoYZTP9LdGj2bWmVXVpump"
for b,e in(("copy",51.0),("consensus",25.0),("trend",30.0),("narrative",50.4),("perps",101.2),("perps-grok",87.0)):
    db.kv_set(b+":cash",e*0.8);db.kv_set(b+":last_equity",e);db.kv_set(b+":day_start",{"day":risk.day(),"eq":e-0.5});db.kv_set(b+":limits",{"start":e})
    db.ins("b_equity",ts=now-90000,book=b,equity=e*0.98,cash=e)
x=dict(v=1,src="1.5x ATR15m",entry_px=0.002,stop_pct=0.1,stop0=0.0018,stop_px=0.0018,tp1_pct=0.1,tp1_px=0.0022,tp1_portion=0.5,tp2_px=0.0026,trail_pct=0.1,hi=0.002,tp1_done=False)
db.ins("b_positions",book="narrative",trader="narrative:AI cats",mint=M1,tokens=2500,cost=5.0,opened=now-3600,status="open",open_sig="n1",meta=json.dumps({"x":x,"narrative":"ds:ai"}))
db.ins("b_positions",book="perps",trader="perps:long",mint="XRP",tokens=40,cost=12.0,opened=now-7200,status="open",open_sig="p1",
       meta=json.dumps(dict(side=1,entry=1.29,lev=3,notional=51.6,liq=0.9,stop=1.25,stop0=1.25,tp1=1.33,tp2=1.37,tp1_done=False,funding=0.01)))
db.ins("b_positions",book="copy",trader="whale",mint="CL",tokens=0,cost=0,opened=now-9000,status="closed",closed=now-600,realized=1.2,meta="{}")
db.ins("b_trades",ts=now-600,book="copy",trader="whale",mint="CL",side="sell",usd=6,tokens=1,price=1,pnl=1.2,sig="s",note="")
ledger.record(db,book="copy",asset="CL…coin",dir="long",entry=1,exit=1.2,size=5,stop=0.9,target=1.1,trigger="take_profit",market="SOL $186",result="win",pnl=1.2,fees=0.2,notes="final target",source="whale")
# ---- the commands -------------------------------------------------------------------------------------------
P=cmds.positions_text(db,cfg,prices={M1:0.0024},mids={"XRP":1.31})
res["/positions: spot + perps with stop, targets, liquidation, live P&L"]="narrative" in txt(P) and "LONG XRP 3x" in txt(P) and "+20.0%" in txt(P) and "liq" in txt(P) and fits(P)
B=cmds.balance_text(db,cfg);res["/balance: every book, saved pot, today, state"]=all(n in txt(B) for n in("copy","Narrative Rider","perps-grok (8-19x)","all paper")) and fits(B)
L=cmds.handle("/lasttrade",db,"/lasttrade",cfg);res["/lasttrade: every ledger field"]=all(w in txt(L) for w in("entry","exit","stop","target","trigger","fees")) and fits(L)
res["/ledger answers"]="take_profit" in txt(cmds.handle("/ledger",db,"/ledger",cfg))
res["/narratives before the first cycle says so"]="not run yet" in txt(cmds.handle("/narratives",db,"/narratives",cfg))
res["/perps lists the open perps position"]="LONG XRP" in txt(cmds.handle("/perps",db,"/perps",cfg))
D=cmds.handle("/reportdaily",db,"/reportdaily",cfg)
res["/reportdaily: 24h change, closed trades, win rate, fees, green days, alerts"]=all(w in txt(D) for w in("24h","win rate","fees","green d","alerts")) and "+$1.20" in txt(D) and fits(D)
bq=ops.bar(db,"copy")
res["copy/consensus bar: trades counted, PF with fees x3, shown in the daily report"]=bq["n"]==1 and abs(bq["pf"]-99.0)<1e-9 and not bq["ok"] and "1/30 trades" in txt(D)
for i in range(30):ledger.record(db,book="consensus",asset="X",dir="long",entry=1,exit=1.1 if i%2 else 0.95,size=5,stop=0.9,target=1.1,trigger="t",market="",result="",pnl=0.5 if i%2 else -0.25,fees=0.05,notes="",source="")
bc=ops.bar(db,"consensus")
res["bar: 30 trades, PF at 3x fees = (0.4*15)/(0.35*15) = 1.14 -> not passed"]=bc["n"]==30 and abs(bc["pf"]-0.4/0.35)<1e-9 and not bc["ok"]
res["/gaps answers with no gaps"]="no data gaps" in txt(cmds.handle("/gaps",db,"/gaps",cfg))
cmds.handle("/pause",db,"/pause",cfg)
import paper
class H:
    def rpc(s,*a):raise RuntimeError("no network")
pp=paper.Paper(db,H(),cfg,N(),"copy")
res["/pause blocks new entries in every book"]="paused" in (pp.blocked() or "") and ops.book_state(db,"perps")=="PAUSED"
r=cmds.handle("/resume",db,"/resume",cfg);res["/resume allows them again"]=pp.blocked() is None and "resumed" in txt(r)
db.kv_set("risk:global_kill",{"ts":now,"eq":1,"peak":2})
r1=cmds.handle("/risk",db,"/risk clear",cfg);code=re.search(r"/risk clear (\d{6})",txt(r1)).group(1)
bad=cmds.handle("/risk",db,"/risk clear 000000",cfg);ok=cmds.handle("/risk",db,f"/risk clear {code}",cfg)
res["/risk clear needs the 6-digit code"]="wrong" in txt(bad) and "OFF" in txt(ok) and not db.kv_get("risk:global_kill")
db.kv_set("perps-grok:shutdown",True);r2=cmds.handle("/risk",db,"/risk reset perps-grok",cfg);c2=re.search(r"perps-grok (\d{6})",txt(r2)).group(1)
cmds.handle("/risk",db,f"/risk reset perps-grok {c2}",cfg)
res["/risk reset BOOK restarts a book after its drawdown stop"]=not db.kv_get("perps-grok:shutdown") and db.kv_get("perps-grok:peak")==87.0
res["/risk shows the charter"]="0.25% of the book" in txt(cmds.handle("/risk",db,"/risk",cfg))
res["unknown commands fall through to main.py"]=cmds.handle("/status",db,"/status",cfg) is None and cmds.handle("/backtest",db,"/backtest",cfg) is None
# ---- daily operations -----------------------------------------------------------------------------------------
S.clear();g=time.gmtime(now);t9=now//86400*86400+9*3600     # 09:00 UTC today: after the morning hour
db.kv_set("ops:morning",None);db.kv_set("ops:monthly",time.strftime("%Y-%m",time.gmtime(t9)))
ops.tick(db,cfg,N(),{},now=t9);ops.tick(db,cfg,N(),{},now=t9+30)
mr=[t for k,t in S if "daily report" in t]
res["morning report: sent once a day (not again 30 s later)"]=len(mr)==1 and ("flush","") in S
S.clear();db.kv_set("ops:monthly",None);ran=[]
ops.tick(db,cfg,N(),{},now=t9,backtests=lambda:ran.append(1));ops.tick(db,cfg,N(),{},now=t9+60,backtests=lambda:ran.append(1))
res["1st-of-month checks: panic-test reminder + walk-forward re-check, once a month"]=sum(1 for k,t in S if "monthly checks" in t)==1 and ran==[1] and any("/panic" in t for k,t in S)
S.clear();db.kv_set("perps:alive",t9-1200);ops.stalls(db,cfg,N(),t9,ops.conf(cfg));ops.stalls(db,cfg,N(),t9+5,ops.conf(cfg))
crit=[t for k,t in S if k=="critical"];db.kv_set("perps:alive",t9+10);ops.stalls(db,cfg,N(),t9+11,ops.conf(cfg))
res["stalled thread: one critical alert, then 'recovered'"]=len(crit)==1 and "perps books" in crit[0] and any("recovered" in t for k,t in S)
db.kv_set("copy:last_equity",30.0);S.clear();ops.tick(db,cfg,N(),{},now=t9+120)
res["global kill trips from the daily tick (research book left out)"]=bool(db.kv_get("risk:global_kill")) and "perps-grok" not in ops.KILL_BOOKS
# ---- the real Notifier: digest tier queues, flush groups ------------------------------------------------------
nt=notify.Notifier(db,False);db.kv_set("notify:digest",[])
for i in range(3):nt.send("x",tier="digest",digest="copy: skipped COIN (liquidity)")
q=db.kv_get("notify:digest");db.kv_set("notify:digest",[[q[0][0]-4000,q[0][1]]]+q[1:])
sent=[];nt.send=lambda t,buttons=None,tier="instant",digest=None:sent.append(plain(t))
res["digest: routine lines queued, then one grouped message an hour"]=nt.flush_digest(3600)==3 and "3x copy: skipped" in sent[0]
# ---- main.py routes the new commands and starts the background parts ----------------------------------------
src=open("main.py").read()
res["main.py: cmds.handle first, ops.tick at every exit check"]="_cmds.handle(cmd,cdb,text,cfg,n)" in src and "_ops.tick(db,cfg,n," in src
res["main.py: narrative engine, Narrative Rider book and perps threads start"]=all(x_ in src for x_ in("_rider.engine_thread","_rider.book_thread","_perps.runner_thread"))
import tgfmt;names=[c for c,_ in tgfmt.COMMANDS]
res["Telegram menu lists every new command"]=all(c in names for c in("positions","balance","lasttrade","ledger","narratives","perps","reportdaily","pause","resume","risk","gaps"))
res["Telegram menu: names valid, at most 100"]=len(names)<=100 and all(re.fullmatch(r"[a-z0-9_]{1,32}",c) for c in names)
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
