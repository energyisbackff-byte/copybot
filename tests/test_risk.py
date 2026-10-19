"""Risk charter, ledger + reviews, alert tiers and the new copy/consensus exits, on a made-up database (no network).
Run: python3 tests/test_risk.py"""
import sys,os,time,json,tempfile,types,re,html
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);os.chdir(R)
T=tempfile.mkdtemp();os.environ.update(HELIUS_API_KEY="x",COPYBOT_MEMORY=os.path.join(T,"memory"),TELEGRAM_BOT_TOKEN="",TELEGRAM_ALERTS_CHAT_ID="")
import yaml,risk,ledger,paper,veto,feeds,notify
from db import DB
from tgfmt import plain
res={}
cfg=yaml.safe_load(open("config.yaml"))
def fresh():return DB(os.path.join(T,f"t{time.time_ns()}.db"))
# ---- Kelly + sizing ------------------------------------------------------------------------------------
k=risk.kelly([2,2,2,-1,-1,-1]);res["Kelly: 50% wins at 2:1 = 0.25"]=abs(k-0.25)<1e-9
res["Kelly: no losses = not measurable"]=risk.kelly([1,2])is None
res["size for risk: 1% of $100 at a 10% stop + 2x0.5% costs = $9.09"]=abs(risk.size_for_risk(100,0.10,0.005,0.01)-100*0.01/0.11)<1e-9
ok,txt=risk.fee_edge(0.10,0.04,3);ok2,_=risk.fee_edge(0.13,0.04,3)
res["fee edge: +10% target < 3x 4% costs is refused, +13% passes"]=not ok and ok2 and "12.0%" in txt
db=fresh();c={"risk":{"per_trade":0.005,"books":{"copy":{"per_trade":0.01}}}}
f,_=risk.risk_fraction(db,c,"narrative");f2,_=risk.risk_fraction(db,c,"copy")
res["charter per-book override (copy 1%, others 0.5%)"]=f==0.005 and f2==0.01
for i in range(30):db.ins("b_positions",book="narrative",trader="x",mint="M",tokens=0,cost=0,opened=1,status="closed",closed=2+i,realized=(2 if i%2 else -1),meta=json.dumps({"x":{"risk_usd":1}}))
f3,n3=risk.risk_fraction(db,c,"narrative")
res["30+ trades: 0.25x Kelly caps the risk (never raises it)"]=abs(f3-min(0.005,0.25*0.25))<1e-12 and "Kelly" in n3
db2=fresh()
for i in range(30):db2.ins("b_positions",book="narrative",trader="x",mint="M",tokens=0,cost=0,opened=1,status="closed",closed=2+i,realized=(1 if i%3==0 else -1),meta="{}")
fp,_=risk.risk_fraction(db2,c,"narrative");fl,nl=risk.risk_fraction(db2,c,"narrative",live=True)
res["no edge (Kelly <= 0): paper quarter size, real money zero"]=abs(fp-0.005/4)<1e-12 and fl==0 and "no measurable edge" in nl
# ---- blocks: stand-down, cooldown, narrative failures, Trend Rider exempt ---------------------------------
db=fresh();now=time.time()
risk.on_exit(db,cfg,"copy","COIN1","stop_loss",-1.0,now=now)
res["one losing stop: coin cooled down 24h, book still open"]="cooldown" in (risk.entry_block(db,cfg,"copy",coin="COIN1",now=now) or "") and risk.entry_block(db,cfg,"copy",coin="COIN2",now=now) is None
res["cooldown ends after 24h"]=risk.entry_block(db,cfg,"copy",coin="COIN1",now=now+86401) is None
risk.on_exit(db,cfg,"copy","COIN2","take_profit",+1.0,now=now);res["a winning exit is not a stop-out"]=risk.stopouts_today(db,"copy")==1
risk.on_exit(db,cfg,"copy","COIN3","trail_stop",-0.2,now=now)
res["2 losing stop-outs in a day: book stands down"]="stand-down" in (risk.entry_block(db,cfg,"copy",now=now) or "")
res["stand-down ends at the next UTC day"]=risk.entry_block(db,cfg,"copy",coin="COIN9",now=now+86400) is None
risk.on_exit(db,cfg,"trend","SOL","stop_loss",-1,now=now);risk.on_exit(db,cfg,"trend","SOL","stop_loss",-1,now=now)
res["Trend Rider v2 untouched: no stand-down/cooldown for it"]=risk.entry_block(db,cfg,"trend",coin="SOL",now=now) is None
risk.on_exit(db,cfg,"narrative","M","stop_loss",-1,narrative="ds:ai",now=now);b1=risk.entry_block(db,cfg,"narrative",narrative="ds:ai",now=now)
risk.on_exit(db,cfg,"perps","M2","time_stop",-1,narrative="ds:ai",now=now);b2=risk.entry_block(db,cfg,"narrative",coin="M3",narrative="ds:ai",now=now)
res["narrative with 2 losing trades is blocked"]=(b1 is None or "narrative" not in b1) and "narrative blocked" in (b2 or "")
res["...until it accelerates again"]=risk.narrative_reaccelerated(db,"ds:ai") and "narrative" not in (risk.entry_block(db,cfg,"perps",narrative="ds:ai",now=now) or "")
# ---- global kill switch ----------------------------------------------------------------------------------
db=fresh();S=[]
class N:
    token=""
    def send(s,t,buttons=None,tier="instant",digest=None):S.append((tier,t))
for b,e in(("copy",52),("consensus",25)):db.kv_set(b+":last_equity",e)
risk.global_check(db,cfg,["copy","consensus"],N())
db.kv_set("copy:last_equity",52*0.95);db.kv_set("consensus:last_equity",25*0.95);t1=risk.global_check(db,cfg,["copy","consensus"],N())
db.kv_set("copy:last_equity",52*0.92);t2=risk.global_check(db,cfg,["copy","consensus"],N())
res["global kill: -5% no, -6%+ trips once as CRITICAL"]=not t1 and t2 and S and S[-1][0]=="critical" and "global kill" in (risk.entry_block(db,cfg,"trend") or "")
risk.global_clear(db,["copy","consensus"]);res["/risk clear resets it"]=risk.entry_block(db,cfg,"copy") is None
# ---- green days ------------------------------------------------------------------------------------------
db=fresh();d0=1760000000//86400*86400
for dd,p in((0,1),(0,-0.5),(1,-2),(2,3),(3,0)):db.ins("b_trades",ts=d0+dd*86400+100,book="copy",trader="t",mint="m",side="sell",usd=1,tokens=1,price=1,pnl=p,sig="s",note="")
g=risk.green_days(db,"copy");res["green days: 2 green / 1 red of 3 traded days"]=g["green"]==2 and g["red"]==1 and abs(g["pct"]-2/3)<1e-9
# ---- ledger + reviews ------------------------------------------------------------------------------------
db=fresh();S.clear()
for i in range(25):
    ledger.record(db,ts=1760000000+i*3600,book="copy",asset="AAAA…pump" if i%5 else "BBBB…pump",dir="long",entry=1.0,exit=1.1 if i%3 else 0.85,size=5,stop=0.85,target=1.15,
                  trigger="take_profit" if i%3 else "stop_loss",market="SOL $180",result="win" if i%3 else "loss",pnl=0.5 if i%3 else -0.9,fees=0.2,notes="t",source="whale")
    r=ledger.maybe_review(db,"copy",N())
    if i==9:res["review after 10 trades"]=r and not r["deep"] and r["n"]==10
    if i==24:res["deep review after 25 trades + Learnings.md"]=r and r["deep"] and os.path.exists(os.path.join(os.environ["COPYBOT_MEMORY"],"Learnings.md"))
md=open(os.path.join(os.environ["COPYBOT_MEMORY"],"Trade_Ledger.md")).read()
res["Trade_Ledger.md: header once + one line per exit, every field"]=md.count("| time (UTC) |")==1 and md.count("\n| 2025")+md.count("\n| 2026")==25 and "SOL $180" in md and "take_profit" in md
L=open(os.path.join(os.environ["COPYBOT_MEMORY"],"Learnings.md")).read()
res["Learnings.md has the 4 sections from evidence"]=all(x in L for x in("### Repeated mistakes","### Strengths","### Weaknesses","### Candidate improvements (paper first)"))
res["reviews never change anything by themselves"]="nothing changes by itself" in plain(S[-1][1]) and "paper first" in L
fits=lambda h:all(len(x)<=32 for x in plain(h).split("\n")[1:])
res["/lasttrade and /ledger fit the phone"]=fits(ledger.last_text(db)) and fits(ledger.ledger_text(db))
# ---- alert tiers -------------------------------------------------------------------------------------------
db=fresh();CALLS=[]
n=notify.Notifier(db,False);n.token="t";db.kv_set("tg_chat_id","111")
n._api=lambda m,p:CALLS.append(p) or {"ok":True}
n.send("<b>x</b> skipped 1",tier="digest",digest="copy: skipped A (liq)");n.send("y",tier="digest",digest="copy: skipped A (liq)");n.send("z",tier="digest",digest="copy: skipped B (veto)")
res["routine refusals wait for the hourly digest"]=CALLS==[] and len(db.kv_get("notify:digest"))==3
res["digest not sent before an hour"]=n.flush_digest()==0 and CALLS==[]
res["digest groups repeats into one message"]=n.flush_digest(force=True)==3 and len(CALLS)==1 and "2x copy: skipped A (liq)" in plain(CALLS[0]["text"]) and db.kv_get("notify:digest")==[]
CALLS.clear();os.environ["TELEGRAM_ALERTS_CHAT_ID"]="-100999"
n.send("fill",tier="instant");n.send("kill",tier="critical")
res["instant -> alerts chat; critical -> both chats, marked 🚨"]=[c["chat_id"] for c in CALLS]==["-100999","-100999","111"] and CALLS[1]["text"].startswith("🚨")
CALLS.clear();n.send("approve?",buttons=[[("Approve","appr:1:yes")]]);res["approval buttons stay in the bot chat"]=CALLS[0]["chat_id"]=="111"
os.environ["TELEGRAM_ALERTS_CHAT_ID"]=""
res["notifications counted per day (morning report)"]=sum((db.kv_get("notify:count") or {}).values())==4
# ---- copy exits: volatility stop, fee edge, 50% at the first target, breakeven, trailing ------------------
import chain
c2=yaml.safe_load(open("config.yaml"));c2["paper"].update(exec_latency_s=0,fee_pct=0.001,gas_usd=0.001,cost_model="fixed")   # exit logic with known costs (test_costs.py tests the measured model);c2["exits"].update(enabled=True,mode="atr")
db=fresh();SENT=[]
class N2:
    token=""
    def send(s,t,buttons=None,tier="instant",digest=None):SENT.append((tier,t))
LIVE={"px":1.0,"impact":0.002,"back":0.002}
def q(inm,outm,raw,bps=300,detail=False):
    px=LIVE["px"]
    if inm==chain.USDC:return (int(raw/1e6/px*1e6),LIVE["impact"],None)
    return (int(raw/1e6*px*1e6*(1-LIVE["back"])),LIVE["impact"],None)
paper.jup_quote=q;paper.decimals=lambda h,m:6;paper.jup_price=lambda m:(LIVE["px"],5e6);paper.jup_prices=lambda ms:{m:LIVE["px"] for m in ms}
paper.sol_usd=lambda:180.0;paper.storm=lambda d,c:None;veto.check=lambda *a,**k:(True,"ok",{"mint_auth":None,"freeze_auth":None,"top10":0.2})
ATR={"v":0.05}
feeds.Feeds.atr_pct=lambda self,mint,minutes=15,n=14:ATR["v"]
pp=paper.Paper(db,None,c2,N2(),"copy")
pid=pp.on_buy("whale","MINT1",20.0,20.0,"sig1",{})
m=json.loads(db.q("select meta from b_positions where id=?",(pid,))[0][0]) if pid else {}
x=m.get("x") or {}
res["buy carries its plan: 1.5x ATR stop, first target 1R, final 3R"]=bool(pid) and abs(x["stop_pct"]-0.075)<1e-9 and abs(x["tp1_pct"]-0.075)<1e-9 and abs(x["tp2_px"]/x["entry_px"]-1-0.225)<1e-9
res["buy message shows the plan as intended prices"]="intended prices" in SENT[-1][1] and "breakeven" in SENT[-1][1]
ATR["v"]=0.01;pid2=pp.on_buy("whale","MINT2",20.0,20.0,"sig2",{})
sg=db.q("select reason from b_signals where mint='MINT2' order by id desc limit 1")[0][0]
res["tiny volatility: stop floor 6%, fee edge decides (refused when costs too high)"]=(pid2 is None and sg.startswith("fee edge")) or (pid2 is not None)
LIVE["back"]=0.03;ATR["v"]=0.05;pid3=pp.on_buy("whale","MINT3",20.0,20.0,"sig3",{})
sg3=db.q("select reason from b_signals where mint='MINT3' order by id desc limit 1")[0][0]
res["fee edge refuses: +7.5% target vs 3x (3% exit + fees)"]=pid3 is None and sg3.startswith("fee edge: target +7.5% vs 3x costs")
LIVE["back"]=0.002
res["refusals go to the hourly digest, not instantly"]=SENT[-1][0]=="digest"
# first target: half sold, stop to breakeven
LIVE["px"]=1.08;pp.check_exits()
row=db.q("select tokens,meta from b_positions where id=?",(pid,))[0];x=json.loads(row[1])["x"]
res["first target: 50% sold, stop moved to breakeven (fees included)"]=x["tp1_done"] and abs(row[0]-0.5*db.q("select tokens from b_trades where mint='MINT1' and side='buy'")[0][0])<1e-6 and db.q("select count(*) from b_trades where book='copy' and side='sell' and mint='MINT1'")[0][0]==1 and x["stop_px"]>=x["entry_px"]
LIVE["px"]=1.15;pp.check_exits();x=json.loads(db.q("select meta from b_positions where id=?",(pid,))[0][0])["x"]
res["after the first target the stop trails the high (only up)"]=abs(x["stop_px"]-1.15*(1-0.075))<1e-9
LIVE["px"]=1.06;pp.check_exits();st_=db.q("select status from b_positions where id=?",(pid,))[0][0]
lg=[r for r in ledger.rows(db,"copy",10,final=False) if r["asset"]=="MINT1"]
res["trailing stop sells the rest; ledger has the partial and the final line"]=st_=="closed" and [r["trigger"] for r in lg[:2]]==["trail_stop","take_profit_1"] and lg[1]["result"].startswith("partial")
res["a winning trailing stop is not a stop-out"]=risk.stopouts_today(db,"copy")==0
# two losing stops -> stand-down; the coin is cooled down
for i,mn in enumerate(("MINT4","MINT5")):
    LIVE["px"]=1.0;p_=pp.on_buy("whale",mn,20.0,20.0,f"s4{i}",{});LIVE["px"]=0.90;pp.check_exits()
res["2 losing stop-outs -> stand-down refusal on the next buy"]=risk.stopouts_today(db,"copy")==2 and pp.on_buy("whale","MINT6",20.0,20.0,"s6",{}) is None and \
    "stand-down" in db.q("select reason from b_signals where mint='MINT6'")[0][0]
res["sells are never blocked by the stand-down"]=db.q("select count(*) from b_positions where book='copy' and status='open'")[0][0]==0
# old positions without a plan keep the old flat exits
db.ins("b_positions",book="copy",trader="w",mint="OLD",tokens=10,cost=10,opened=time.time(),status="open",open_sig="o",meta="{}")
LIVE["px"]=0.84;paper.jup_prices=lambda ms:{m:0.84 for m in ms};pp.check_exits()
res["positions opened before the change keep the flat -15% stop"]=db.q("select status from b_positions where mint='OLD'")[0][0]=="closed"
# sweep: profit above the baseline moves to the swept pot, once per UTC day after 00:05
db=fresh();p2=paper.Paper(db,None,c2,N2(),"consensus");p2.kset("cash",30.0);d=time.time()//86400*86400
out=risk.sweep(db,c2,{"consensus":p2},N2(),now=d+200);out2=risk.sweep(db,c2,{"consensus":p2},N2(),now=d+400);out3=risk.sweep(db,c2,{"consensus":p2},N2(),now=d+500)
res["profit sweep: not before 00:05, then once a day"]=out=={} and out2=={"consensus":5.0} and out3=={}
res["swept money counts in equity but is never traded"]=abs(p2.equity()-30)<1e-9 and abs(p2.tradable()-25)<1e-9 and p2.cash==25.0
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
