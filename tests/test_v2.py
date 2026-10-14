"""Trend Rider v2 tests on synthetic prices (no network): indicators, rules, fills, sizing, the 3-coin gate and report,
and a day-by-day replay of the live paper runner that must make the same trades as the backtest.
Run: python3 tests/test_v2.py"""
import sys,os,random,math,time,tempfile,json,re,html,types
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);os.chdir(R)
os.environ.setdefault("HELIUS_API_KEY","x")
import yaml,strategy_v2 as S,backtest_v2 as B
res={}
D=86400;T0=1622505600   # 1 Jun 2021 00:00 UTC
def synth(days,seed,px=40.0):
    random.seed(seed);out=[];drift=0
    for k in range(days):
        if k%60==0:drift=random.choice([0.006,0.003,0.0,-0.003,-0.005])
        o=px*math.exp(random.gauss(0,0.004));c=o*math.exp(drift+random.gauss(0,0.035))
        h=max(o,c)*math.exp(abs(random.gauss(0,0.02)));l=min(o,c)*math.exp(-abs(random.gauss(0,0.02)))
        out.append((T0+k*D,o,h,l,c,1.0));px=c
    return out
# ---- indicators
c=[float(x) for x in range(1,61)];h=[x+1 for x in c];l=[x-1 for x in c]
sm=S.sma(c,20);res["SMA20 exact"]=sm[18] is None and abs(sm[19]-10.5)<1e-9 and abs(sm[59]-50.5)<1e-9
at=S.atr_wilder(h,l,c,14)
tr=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,15)]
res["ATR14 Wilder seed = mean of first 14 TRs"]=at[13] is None and abs(at[14]-sum(tr)/14)<1e-9
x=at[14];x=(x*13+max(h[15]-l[15],abs(h[15]-c[14]),abs(l[15]-c[14])))/14;res["ATR14 Wilder smoothing"]=abs(at[15]-x)<1e-9
# ---- breakout excludes today: a close EQUAL to the previous 20-day high is not a breakout
rows=[(T0+i*D,100+i*0.5,101+i*0.5,99+i*0.5,100+i*0.5,1) for i in range(80)]
d=S.Daily(rows);i=79
res["steady uptrend gives a signal"]=bool(d.signal(i))
rows2=rows[:79]+[(T0+79*D,139,140,138,rows[78][4],1)];d2=S.Daily(rows2)
res["close equal to the 20-day high is not a breakout"]=d2.signal(79) is None
sg=d.signal(i);res["stop = close - 3 x ATR14"]=abs(sg["stop"]-(d.c[i]-3*d.atr[i]))<1e-9
# ---- sizing
s1=S.size_usd(30,0.10,0.0015);s2=S.size_usd(30,0.20,0.0015)
res["size: $30, 10% stop -> about $2.65"]=abs(s1-30*0.01/(0.10+0.003+0.01))<1e-9 and 2.6<s1<2.7
res["size: wider stop -> smaller position"]=s2<s1 and 1.3<s2<1.5
res["size: capped at 50% of the book"]=abs(S.size_usd(30,0.001,0)-15)<1e-9
res["size: capped at the live limit"]=S.size_usd(5000,0.1,0.0015,cap_usd=20)==20
res["too-small trade refused"]=not S.worth_it(0.5,30,0.0015,0.01)[0] and S.worth_it(2.6,30,0.0015,0.01)[0]
res["costs eating the risk budget refused"]=not S.worth_it(2.6,30,0.0015,0.05)[0]
# ---- backtest mechanics on a hand-made path
base=[(T0+i*D,100+i*0.5,100.6+i*0.5,99.4+i*0.5,100+i*0.5,1) for i in range(70)]
r=B.run(base,cost=0.0)
t1=r["trades"][0] if r["trades"] else None
sig_day=next(k for k in range(70) if S.Daily(base).signal(k))
res["entry fills at the NEXT day's open"]=t1 and t1["t_in"]==base[sig_day+1][0] and abs(t1["entry"]-base[sig_day+1][1])<1e-9
gap=base+[(T0+70*D,80,81,79,80,1)]+[(T0+(71+k)*D,80,80.5,79.5,80,1) for k in range(5)]
r=B.run(gap,cost=0.0);t=r["trades"][0]
res["gap below the stop fills at the open (not the stop)"]=t["why"]=="stop" and abs(t["exit"]-80)<1e-9
dip=base+[(T0+70*D,135,135.5,100,134.8,1)]+[(T0+(71+k)*D,135,135.5,134.5,135,1) for k in range(3)]
r=B.run(dip,cost=0.0);t=r["trades"][0];dd=S.Daily(dip)
res["intraday touch fills AT the stop"]=t["why"]=="stop" and t["exit"]<135 and t["exit"]>100
slow=base+[(T0+(70+k)*D,134-k*1.2,134.3-k*1.2,133.5-k*1.2,133.8-k*1.2,1) for k in range(40)]
r=B.run(slow,cost=0.0);w=[x["why"] for x in r["trades"]]
res["exits happen (trend exit or trailing stop)"]=bool(w) and set(w)<={"trend_exit","stop","end"}
# trend exit: wide daily ranges keep the 3-ATR stop far away, so the close under SMA50 comes first; fills at the next open
wide=[(T0+i*D,100+i*0.5,105+i*0.5,95+i*0.5,100+i*0.5,1) for i in range(70)]+[(T0+(70+k)*D,134.5-k*0.3,139.5-k*0.3,129.5-k*0.3,134.5-k*0.3,1) for k in range(60)]
r=B.run(wide,cost=0.0);sd=S.Daily(wide);te=[x for x in r["trades"] if x["why"]=="trend_exit"]
k=next((k for k in range(len(wide)) if te and wide[k][0]==te[0]["t_out"]),None)
res["trend exit fills at the next open"]=bool(te) and sd.trend_exit(k-1) and not sd.trend_exit(k-2) and abs(te[0]["exit"]-wide[k][1])<1e-9
chase=base[:sig_day+1]+[(base[sig_day+1][0],base[sig_day][4]*1.08,base[sig_day][4]*1.09,base[sig_day][4]*1.07,base[sig_day][4]*1.08,1)]
r=B.run(chase,cost=0.0);res["no chasing: open 8% above the signal close is skipped"]=not any(x["t_in"]==chase[-1][0] for x in r["trades"])
r0=B.run(synth(500,3),cost=0.0015);r2=B.run(synth(500,3),cost=0.003)
res["doubling costs lowers the result"]=r2["end"]<=r0["end"]
# ---- the 3-coin gate and the report
data={"SOL":synth(1960,1),"BTC":synth(1960,2,px=30000.0),"ETH":synth(1960,3,px=2500.0)}
bt=B.full(data,cost=0.0015,seen_from=T0+1400*D)
res["gate has 7 fixed checks"]=len(bt["checks"])==7 and isinstance(bt["passed"],bool)
res["SOL is judged on its own"]=any(c["name"].startswith("SOL alone") for c in bt["checks"])
res["held-back overlap disclosed"]=any("overlaps" in n for n in bt["notes"])
res["benchmarks: holding each coin"]=all(bt["coins"][k]["hold"]["ret"] is not None for k in B.COINS)
res["missing coin -> clear error"]="missing" in B.full({"SOL":data["SOL"]}).get("error","")
import trend
rep=trend.backtest_report(dict(bt,cost_source="assumed (no measurements yet)",holdout_views=1));body=html.unescape(re.search(r"<pre>(.*?)</pre>",rep,re.S).group(1))
print(body)
res["report lines fit the phone (32 cols)"]=all(len(l)<=32 for l in body.split("\n"))
res["v1 result is not shown as v2"]="no v2 result" in trend.backtest_report(dict(version="trend-rider v1",checks=[],passed=False))
print("synthetic gate:",bt["passed"],[(c["name"],c["ok"]) for c in bt["checks"]])
# ---- live paper runner replay == backtest
import paper,chain,candles
from db import DB
SOL=synth(1000,7,px=150.0);cfg=yaml.safe_load(open("config.yaml"));tb_cfg=cfg["books"]["trend"]
tb_cfg.update(exec_latency_s=0,max_drawdown_pct=0.9,weekly_loss_limit_pct=0.9,daily_loss_limit_pct=0.9)
db=DB(tempfile.mktemp(suffix=".db"));SENT=[]
class N:
    token=""
    def send(s,t,buttons=None):SENT.append(t)
LIVE={"px":SOL[0][4]}
def q(inm,outm,raw,bps=300,detail=False):
    px=LIVE["px"]
    if inm==chain.USDC:return (int(raw/1e6/px*1e9),0.0005,None)
    return (int(raw/1e9*px*1e6),0.0005,None)
paper.jup_quote=q;paper.decimals=lambda h,m:9;paper.jup_price=lambda m:(LIVE["px"],5e8);paper.jup_prices=lambda ms:{m:LIVE["px"] for m in ms}
paper.sol_usd=lambda:LIVE["px"];trend.jup_price=lambda m:(LIVE["px"],5e8)
import veto;veto.check=lambda *a,**k:(True,"ok",{})
CLOCK={"t":SOL[0][0]};_t=time
fake=types.SimpleNamespace(time=lambda:CLOCK["t"],sleep=lambda x:None,strftime=_t.strftime,localtime=_t.localtime,gmtime=_t.gmtime)
paper.time=fake;trend.time=fake
import datetime as _dt
class _DT(_dt.datetime):
    @classmethod
    def utcnow(cls):return _dt.datetime.utcfromtimestamp(CLOCK["t"])
paper.dt=types.SimpleNamespace(datetime=_DT)
candles.sync=lambda d,sym="SOL-USD",tf=D,need_days=150,now=None:candles.last_ts(d,sym,tf)
tb=paper.Paper(db,None,cfg,N(),"trend");run=trend.TrendRunner(db,tb,N(),cfg)
warm=80;candles.store(db,"SOL-USD",D,SOL[:warm])
stops=[]
for k in range(warm,len(SOL)):
    o,h,l,c=SOL[k][1:5];day=SOL[k][0]
    for frac,px in ((300,o),(6*3600,l),(12*3600,h),(D-60,c)):    # pessimistic order: low before high
        if frac==300:candles.store(db,"SOL-USD",D,[SOL[k-1]])        # yesterday's candle is now closed
        CLOCK["t"]=day+frac;LIVE["px"]=px;run.tick(now=CLOCK["t"])
        p=run.paper_pos()
        if p and p["meta"].get("v")==2:stops.append((p["id"],p["meta"]["stop"]))
    if k%7==0:tb.mark()
T=db.q("select status,realized,meta,opened from b_positions where book='trend' order by id")
why=dict(db.q("select reason,count(*) from b_fills where book='trend' and side='sell' and status='filled' group by 1"))
bt1=B.run(SOL,cost=0.0015,equity=30.0,start_ts=SOL[warm][0],end_ts=SOL[-1][0])
bw={}
for x in bt1["trades"]:bw[x["why"]]=bw.get(x["why"],0)+1
print("replay paper:",len(T),"trades",why,"| backtest:",len(bt1["trades"]),bw)
ins_live=[int(x[3]//D*D) for x in T];ins_bt=[int(x["t_in"]) for x in bt1["trades"]]
res["replay enters on the same days as the backtest"]=ins_live==ins_bt[:len(ins_live)] and len(ins_live)>=len(ins_bt)-1 and len(ins_live)>=3
res["replay exit reasons match"]=why.get("stop_loss",0)==bw.get("stop",0) and why.get("trend_break",0)==bw.get("trend_exit",0)
mono=all(b>=a-1e-9 for (p1,a),(p2,b) in zip(stops,stops[1:]) if p1==p2);res["trailing stop never moves down"]=mono
bad=[l for t in SENT for pre in re.findall(r"<pre>(.*?)</pre>",t,re.S) for l in html.unescape(pre).split("\n") if len(l)>32]
res["telegram lines fit the phone"]=not bad
if bad:print("long:",bad[:4])
rows=run.status_rows();print("\n".join(str(r) for r in rows))
res["/trend fits the phone"]=all(len(x)<=32 for r in rows for x in str(r).split("\n"))
print("refusals:",db.q("select reason,count(*) from b_signals where book='trend' and decision='refused' group by 1"))
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
