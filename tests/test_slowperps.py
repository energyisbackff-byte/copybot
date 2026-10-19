"""Slow Perps (slowperps.py) on made-up candles, no network: indicators, 4-hour candles from 1-hour ones, long and
short signals, next-open entries, the chase filter, gap stops, the trailing stop, the exit back across the 50 average,
no look-ahead, conservative funding, the full evidence bar, phone-width reports, the /backtest command and the
one-time run after the update. Backtest only: no book, nothing real. Run: python3 tests/test_slowperps.py"""
import sys,os,math,random,tempfile,re,html,json,time
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);os.chdir(R)
T=tempfile.mkdtemp();os.environ.update(HELIUS_API_KEY="x",COPYBOT_MEMORY=os.path.join(T,"memory"),TELEGRAM_BOT_TOKEN="",TELEGRAM_ALERTS_CHAT_ID="",COPYBOT_DB=os.path.join(T,"bot.db"))
import slowperps as SP,evidence as EV
from tgfmt import W,plain
res={};DAY=SP.DAY;H=SP.H
def txt(t):return html.unescape(re.sub(r"<[^>]+>","",t))
def fits(t):
    body=txt(t).split("\n")[1:]
    return all(len(l)<=W for l in body if l and not l.startswith("root@"))
def series(closes,t0=1_600_000_000//DAY*DAY,tf=DAY,wick=0.01):
    rows=[];prev=closes[0]
    for i,c in enumerate(closes):
        o=prev;rows.append((t0+i*tf,o,max(o,c)*(1+wick),min(o,c)*(1-wick),c,1000.0));prev=c
    return rows
# ---- indicators -----------------------------------------------------------------------------------------------
res["sma: plain average of the last n closes"]=SP.sma([1,2,3,4,5],3)==[None,None,2.0,3.0,4.0]
flat=[(i,10,11,9,10,1) for i in range(30)];a=SP.atr(flat,14)
res["atr: Wilder ATR of a steady 2-wide range is 2"]=a[13] is None and abs(a[14]-2)<1e-9 and abs(a[-1]-2)<1e-9
hr=[(i*H,10+i,10.5+i,9.5+i,10.2+i,1.0) for i in range(8)]+[(8*H,1,1,1,1,1),(9*H,1,1,1,1,1)]
r4=SP.resample(hr,SP.H4)
res["4-hour candles: open of first hour, high/low over 4 hours, last close, summed volume"]=r4[0]==(0,10,13.5,9.5,13.2,4.0) and r4[1][0]==4*H and len(r4)==2
res["4-hour candles: a bucket with under 3 of 4 hours is dropped"]=all(x[0]!=8*H for x in r4)
# ---- signals and fills ------------------------------------------------------------------------------------------
P=dict(SP.DEF)
up=[100*1.004**i for i in range(200)];X=SP.prep(series(up),P)
res["signal: steady uptrend -> long"]=SP.signal(X,120,P)==1
dn=[100*0.996**i for i in range(200)];Xd=SP.prep(series(dn),P)
res["signal: steady downtrend -> short"]=SP.signal(Xd,120,P)==-1
res["signal: not before 51 candles of history"]=all(SP.signal(X,i,P)==0 for i in range(50))
ru=SP.run(series(up),P,DAY);rd=SP.run(series(dn),P,DAY)
res["uptrend: makes money with longs only"]=ru["end"]>ru["start"] and ru["trades"] and all(x["d"]==1 for x in ru["trades"])
res["downtrend: makes money with shorts only"]=rd["end"]>rd["start"] and rd["trades"] and all(x["d"]==-1 for x in rd["trades"])
rows=series(up);first=next(i for i in range(len(rows)) if SP.signal(X,i,P))
res["entry at the NEXT candle's open after the signal"]=ru["trades"][0]["t_in"]==rows[first+1][0]
# chase filter: the open after the signal jumps 10% -> skipped
ch=series(up);c=list(ch[first+1]);c[1]=ch[first][4]*1.10;c[2]=max(c[2],c[1]);ch[first+1]=tuple(c)
rc=SP.run(ch,P,DAY)
res["chase filter: an open 10% past the signal close is skipped"]=not rc["trades"] or rc["trades"][0]["t_in"]!=ch[first+1][0]
# gap through the stop fills at the open (pessimistic), trailing stop only tightens
g=up[:first+5]+[up[first+4]*0.80]*3+up[first+4:first+60];gr=series(g,wick=0.001);gr[first+5]=(gr[first+5][0],up[first+4]*0.80,up[first+4]*0.80,up[first+4]*0.79,up[first+4]*0.80,1)
rg=SP.run(gr,P,DAY);t0=rg["trades"][0]
res["gap through the stop: filled at that open, not at the stop"]=t0["why"]=="stop (gap)" and t0["t_out"]==gr[first+5][0]
lk=up[:first+40]+[up[first+39]*(0.97**k) for k in range(1,60)];rl=SP.run(series(lk),P,DAY)
res["trend turns: the long is closed (stop or back across the 50 average)"]=rl["trades"][0]["why"] in("stop","stop (gap)","back across the 50 average") and rl["trades"][0]["pnl"]>0
# no look-ahead: changing the future never changes a trade already closed
rng=random.Random(3);rw=[100.0]
for _ in range(1499):rw.append(rw[-1]*math.exp(rng.gauss(0.0005,0.03)))
full_=SP.run(series(rw),P,DAY);m=900;part=SP.run(series(rw[:m]),P,DAY);cut=series(rw)[m-1][0]
A=[(x["t_in"],x["t_out"],round(x["pnl"],9)) for x in full_["trades"] if x["t_out"]<cut];B=[(x["t_in"],x["t_out"],round(x["pnl"],9)) for x in part["trades"] if x["t_out"]<cut]
res["no look-ahead: trades closed before day 900 are identical with or without later data"]=A==B and len(A)>3
# conservative funding: a short on a flat market never earns funding
fl=dn[:130]+[dn[129]]*40;rf=SP.run(series(fl,wick=0.0005),P,DAY,fund_short=0.0)
rf2=SP.run(series(fl,wick=0.0005),P,DAY,fund_short=-0.001)
res["shorts are never paid funding (the default never assumes income)"]=SP.run.__defaults__[3]==0.0 and rf2["end"]>rf["end"]
s3=SP.stress(P,DAY,series(rw));b1=SP.run(series(rw),P,DAY)
res["stress run (fees x3, funding x3 longs, shorts pay) is worse than the base run"]=s3["end"]<b1["end"]
res["risk: ~1% of the book per trade, never above 3x leverage"]=all(x["lev"]<=3.0001 for x in b1["trades"]) and min(x["ret"] for x in b1["trades"])>-0.05
# ---- the whole evidence bar on made-up markets ----------------------------------------------------------------
def market(seed,n=1900,drift=0.0,vol=0.035,t0=1_622_505_600):
    r=random.Random(seed);c=[100.0]
    for _ in range(n-1):c.append(c[-1]*math.exp(r.gauss(drift,vol)))
    return series(c,t0)
data={k:market(i) for i,k in enumerate(SP.COINS)}
bt=SP.full(data,DAY)
names=[c["name"] for c in bt["checks"]]
res["full: the 6 checks of the evidence bar are all judged"]=names==["trades","Sharpe, held-back","PF, fees x3","Monte Carlo 95% drawdown","neighbour settings pass","walk-forward windows green"]
res["full: random-walk coins do NOT pass (no edge in noise)"]=bt["passed"] is False and bt["trades"]>=30
res["full: 6 neighbour settings tried, each with fees x3"]=len(bt["plateau"])==6 and set(bt["plateau"])=={"atr_mult=2.5","atr_mult=3.5","breakout=15","breakout=30","sma_slow=40","sma_slow=60"}
res["full: long and short counted separately"]=bt["longs"]+bt["shorts"]==bt["trades"] and bt["longs"]>0 and bt["shorts"]>0
rep=SP.report(bt);pl=SP.plain(bt)
res["report fits the phone (32 columns), says NOT passed in plain words"]=fits(rep) and "NOT passed" in txt(rep) and "in plain words" in txt(rep) and all(len(l)<=W for l in pl)
res["plain words: lists what failed, says nothing changes"]=any(l.startswith("- ") for l in pl) and "nothing changes in the bot" in pl
tr={k:market(i,drift=0.004,vol=0.02) for i,k in enumerate(SP.COINS)};bt2=SP.full(tr,DAY)
res["full: a strongly trending made-up market makes money (the rules do follow trends)"]=bt2["pf"] is not None and bt2["pf"]>1.3 and bt2["ret_basket"]>0
pl2=SP.plain(dict(bt2,passed=True,checks=[dict(c,ok=True) for c in bt2["checks"]]))
res["plain words when it passes: paper book only if you say yes, real money stays off"]="PASSED" in pl2[0] and any("if you say yes" in l for l in pl2)
res["too little history -> a clear error, report still fits"]=SP.full({"BTC":market(1,n=100)},DAY).get("error") and fits(SP.report(SP.full({},DAY)))
h1={k:[(t0_,o,h,l,c,v) for (t0_,o,h,l,c,v) in market(i,n=9000,vol=0.007)] for i,k in enumerate(list(SP.COINS)[:2])}
h1={k:[(1_622_505_600+j*H,)+r[1:] for j,r in enumerate(v)] for k,v in h1.items()}
b4=SP.full({k:SP.resample(v,SP.H4) for k,v in h1.items()},SP.H4)
res["4-hour pipeline: hourly -> 4-hour -> evidence bar"]=not b4.get("error") and b4["tf"]==SP.H4 and len(b4["checks"])==6 and fits(SP.report(b4))
sm=SP.summary({"daily":bt,"4-hour":b4})
res["summary: both timeframes, rules fixed, everything paper"]=fits(sm) and "daily: NOT passed" in txt(sm) and "never tuned" in txt(sm) and "paper" in txt(sm)
# ---- frozen rules, backtest only ------------------------------------------------------------------------------
res["rules are the pre-registered ones"]=SP.DEF==dict(sma_fast=20,sma_slow=50,breakout=20,atr_n=14,atr_mult=3.0,max_chase=0.05,risk_pct=0.01,max_lev=3.0) and SP.COST==dict(fee=0.00045,slip=0.0002,fund_h=0.0000125)
src=open("slowperps.py").read()
res["backtest only: no real-money module, no order path"]=not re.search(r"import[^\n]*\b(live|perps_live|spot_live|signer|perpsguard)\b",src) and "place_order" not in src
import yaml;cfg=yaml.safe_load(open("config.yaml"))
res["no book was added (risk numbers and books unchanged)"]="slowperps" not in (cfg.get("books") or {}) and "slowperps" not in ((cfg.get("risk") or {}).get("books") or {})
# ---- /backtest slowperps and the one-time run after the update -----------------------------------------------
import cmds
from db import DB
db=DB(os.environ["COPYBOT_DB"]);S=[]
class N:
    token=""
    def send(s,t,buttons=None,tier="instant",digest=None):S.append(t)
r=cmds.handle("/backtest",db,"/backtest slowperps last",cfg,N())
res["/backtest slowperps last before any run -> 'not run yet'"]="not run yet" in txt(r)
calls=[];SP.run_async=lambda *a,**k:calls.append(a) or True
r=cmds.handle("/backtest",db,"/backtest slowperps",cfg,N())
res["/backtest slowperps starts the background run"]=calls and "slow perps test started" in txt(r) and fits(r)
db.kv_set(SP.KEY,{"daily":bt,"4-hour":b4});S.clear();r=cmds.handle("/backtest",db,"/backtest slowperps last",cfg,N())
res["/backtest slowperps last: both reports + the plain verdict"]=len(S)==2 and "verdict" in txt(r)
db.kv_set(SP.KEY,None);calls.clear()
a1=SP.auto_once(db,"x.db",N(),"v1");a2=SP.auto_once(db,"x.db",N(),"v1")
res["after the update: runs once by itself, not again on the next restart"]=a1 and not a2 and len(calls)==1
db.kv_set(SP.KEY,{"daily":bt});res["no automatic run when a result already exists"]=not SP.auto_once(db,"x.db",N(),"v2")
m_=open("main.py").read()
res["main.py: one-time run at boot + monthly re-check"]="_sp.auto_once(db,DBP,n,version())" in m_ and "_sp.run_async(DBP,n,cfg)" in m_
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
