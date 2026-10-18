"""Tests for the faster candidates (no network): indicators, no look-ahead in the daily regime and 4h bars, fills,
exits, the gate and the Telegram report, plus a speed check on 5 years of hourly data.  Run: python3 tests/test_fast.py"""
import sys,os,random,math,time,re,html
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);os.chdir(R)
import strategy_fast as F,backtest_fast as B,fastlab
res={};H=3600;D=86400;T0=1622505600
def synth(hours,seed,px=40.0):
    random.seed(seed);out=[];drift=0
    for k in range(hours):
        if k%(24*45)==0:drift=random.choice([0.0004,0.0002,0.0,-0.0002,-0.0004])
        o=px;c=o*math.exp(drift+random.gauss(0,0.008));h=max(o,c)*math.exp(abs(random.gauss(0,0.003)));l=min(o,c)*math.exp(-abs(random.gauss(0,0.003)))
        out.append((T0+k*H,o,h,l,c,1.0));px=c
    return out
# ---- RSI(2), Wilder
c=[10,11,10.5,10,9,9.5,8]
r=F.rsi_wilder(c,2)
g=(1+0)/2;l=(0+0.5)/2;exp2=100-100/(1+g/l)
g3=(g*1+0)/2;l3=(l*1+0.5)/2;exp3=100-100/(1+g3/l3)
res["RSI2 seed = mean of the first 2 changes"]=r[1] is None and abs(r[2]-exp2)<1e-9
res["RSI2 Wilder smoothing"]=abs(r[3]-exp3)<1e-9
res["RSI is 100 when nothing fell"]=F.rsi_wilder([1,2,3,4],2)[3]==100.0
# ---- daily regime uses completed days only (no look-ahead)
rows=synth(24*80,1);ind=F.Hourly(rows)
i=len(rows)-5;day=rows[i][0]//D
rows2=rows[:]
for k in range(len(rows2)):
    if rows2[k][0]//D==day:t,o,h,l,c_,v=rows2[k];rows2[k]=(t,o,h*3,l,c_*3,v)        # today's prices explode
ind2=F.Hourly(rows2)
res["regime ignores today's prices"]=ind.regime(i)==ind2.regime(i)
up=[(T0+k*H,100+k*0.01,100.2+k*0.01,99.8+k*0.01,100+k*0.01,1) for k in range(24*60)]
iu=F.Hourly(up);res["regime ON in a steady uptrend once 50 days are complete"]=iu.regime(len(up)-1) and iu.regime(24*50) and not iu.regime(24*49+23)
# ---- 4h bars: built from complete blocks only, close = last hour's close
k4=iu.bar4;i4=next(i for i in sorted(k4) if i>100)
res["4h bar closes on the hour ending at 04/08/... UTC"]=(up[i4][0]+H)%(4*H)==0 and iu.c4[k4[i4]]==up[i4][4]
part=up[:-2];ip=F.Hourly(part);res["an unfinished 4h bar is never used"]=(len(part)-1) not in ip.bar4
# ---- fills and exits on a hand-made path (Dip Buyer)
base=[(T0+k*H,100+k*0.01,100.3+k*0.01,99.7+k*0.01,100+k*0.01,1) for k in range(24*60)]
n=len(base);px=base[-1][4]
dip=[(T0+(n)*H,px,px+0.1,px-3,px-3,1),(T0+(n+1)*H,px-2.9,px-2.5,px-3.2,px-2.8,1),(T0+(n+2)*H,px-2.8,px+1,px-2.9,px+0.9,1)]+[(T0+(n+3+k)*H,px+1,px+1.2,px+0.8,px+1,1) for k in range(5)]
rows=base+dip;ind=F.Hourly(rows);s=F.DipBuyer(ind)
res["sharp hourly drop in an uptrend gives a Dip signal"]=bool(s.signal(n))
r=B.run(ind,s,cost=0.0,start_ts=T0+50*D);t=[x for x in r["trades"] if x["t_in"]>=T0+n*H]
res["entry at the NEXT hour's open"]=bool(t) and t[0]["t_in"]==rows[n+1][0] and abs(t[0]["entry"]-rows[n+1][1])<1e-9
res["bounce above the 5h average exits at the next open"]=bool(t) and t[0]["why"]=="bounce" and abs(t[0]["exit"]-rows[n+3][1])<1e-9
gap=base+[(T0+n*H,px,px+0.1,px-3,px-3,1),(T0+(n+1)*H,px-2.9,px-2.5,px-3.2,px-3.1,1),(T0+(n+2)*H,px-20,px-19,px-21,px-20,1)]+[(T0+(n+3+k)*H,px-20,px-19.8,px-20.2,px-20,1) for k in range(3)]
ig=F.Hourly(gap);r=B.run(ig,F.DipBuyer(ig),cost=0.0,start_ts=T0+50*D);t=[x for x in r["trades"] if x["t_in"]>=T0+n*H]
res["gap below the stop fills at the open"]=bool(t) and t[0]["why"]=="stop" and abs(t[0]["exit"]-(px-20))<1e-9
flat=base+[(T0+n*H,px,px+0.1,px-3,px-3,1)]+[(T0+(n+1+k)*H,px-3,px-2.98,px-3.3,px-3.2-0.001*k,1) for k in range(60)]
iff=F.Hourly(flat);r=B.run(iff,F.DipBuyer(iff),cost=0.0,start_ts=T0+50*D);t=[x for x in r["trades"] if x["t_in"]>=T0+n*H]
res["no bounce within 48h -> time exit"]=bool(t) and t[0]["why"] in("time","regime") and t[0]["hours"]<=49
# ---- trailing stop of Fast Trend never moves down
rows=synth(24*400,5);ind=F.Hourly(rows);ft=F.FastTrend(ind);pos=dict(hi=0,stop=0,t=0);stops=[]
for i in range(len(rows)):
    if ft.on_close(i,pos) is None:stops.append(pos["stop"])
res["Fast Trend trailing stop never moves down"]=all(b>=a for a,b in zip(stops,stops[1:]))
# ---- the gate and the report
data={"SOL":synth(24*420,11),"BTC":synth(24*420,12,30000.0),"ETH":synth(24*420,13,2500.0)}
bt=B.full(data,cost=0.0015,seen_from=T0);bt["cost_source"]="assumed 0.15% (measured p90 0.054% is lower)";bt["holdout_views"]=1
res["two candidates, 10 fixed checks each"]=len(bt["candidates"])==2 and all(len(c["checks"])==10 for c in bt["candidates"])
res["SOL must pass training AND held-back"]=all(any(x["name"]=="SOL held-back" for x in c["checks"]) and any(x["name"]=="SOL training years" for x in c["checks"]) for c in bt["candidates"])
res["missing coin -> clear error"]="missing" in B.full({"SOL":data["SOL"]}).get("error","")
msgs=fastlab.report(bt);bodies=[html.unescape(re.search(r"<pre>(.*?)</pre>",m,re.S).group(1)) for m in msgs]
print(bodies[0]);print(bodies[-1])
res["3 messages: A, B, summary"]=len(msgs)==3
long=[l for b in bodies for l in b.split("\n") if len(l)>32];res["every line fits the phone (32 cols)"]=not long
if long:print("long:",long)
for c in bt["candidates"]:print(c["name"],"SOL trades/month %.1f"%c["coins"]["SOL"]["all"]["per_month"],"passed",c["passed"])
# ---- speed: 5 years of hourly candles x 3 coins must finish quickly on a small server
big={k:synth(24*365*5+24*140,20+i) for i,k in enumerate(B.COINS)};t0=time.time();bb=B.full(big)
dt=time.time()-t0;print(f"5-year run: {dt:.1f}s");res["5 years x 3 coins x 2 strategies in under 2 minutes"]=dt<120 and not bb.get("error")
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
