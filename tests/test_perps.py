"""Perps books (paper) + perps backtest on made-up data (no network): indicators, long/short signals on closed candles,
crowding, rule zero, leverage vs liquidation, order-book fills, TP/SL installed before entry, partial TP + breakeven,
funding, liquidation, correlation cap, drawdown switch, the hourly runner, the backtest bar. Run: python3 tests/test_perps.py"""
import sys,os,time,json,tempfile,math,random
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);os.chdir(R)
T=tempfile.mkdtemp();os.environ.update(COPYBOT_MEMORY=os.path.join(T,"memory"))
import yaml,perps as PZ,backtest_perps as BP,risk,ledger,evidence as EV
from db import DB
from tgfmt import plain
res={}
H=3600
# ---- indicators -------------------------------------------------------------------------------------------
e=PZ.ema([float(x) for x in range(1,31)],10);res["EMA seeded with the SMA, then smoothed"]=e[8] is None and abs(e[9]-5.5)<1e-9 and abs(e[10]-(11*2/11+5.5*9/11))<1e-9
res["correlation: same series 1, opposite -1"]=abs(PZ.corr([1,2,3,1,5,2,6,3]*4,[1,2,3,1,5,2,6,3]*4)-1)<1e-9 and abs(PZ.corr([1,2,3,1,5,2,6,3]*4,[-1,-2,-3,-1,-5,-2,-6,-3]*4)+1)<1e-9
def trend(n,step,t0=1790000000,px=1.0,noise=0.002,seed=1,last=None):
    random.seed(seed);rows=[];p=px
    for i in range(n):
        o=p;c=o*(1+step+random.gauss(0,noise));h=max(o,c)*(1+abs(random.gauss(0,noise/2)));l=min(o,c)*(1-abs(random.gauss(0,noise/2)))
        rows.append((t0+i*H,o,h,l,c,100.0));p=c
    if last:o=rows[-1][4];c=o*(1+last);rows.append((t0+n*H,o,max(o,c)*1.001,min(o,c)*0.999,c,300.0))
    return rows
P=dict(PZ.DEF);NAR=dict(ok=True,sentiment=0.3,ratio=6,why="XRP talk 6x")
up=trend(200,0.002,noise=0.001,last=0.012);s=PZ.signal(up,P,0.00001,0.02,NAR)
res["long: green candle closes above the 20-candle high in an uptrend"]=s["direction"]==1 and s["stop"]<s["entry_ref"]<s["tp1"]<s["tp2"] and abs((s["entry_ref"]-s["stop"])-P["atr_mult"]*s["features"]["atr"])<1e-12
dn=[(t,1/o,1/l,1/h,1/c,v) for t,o,h,l,c,v in up];s2=PZ.signal(dn,P,-0.00001,0.02,dict(NAR,sentiment=-0.3))
res["short: the mirror image"]=s2["direction"]==-1 and s2["stop"]>s2["entry_ref"]>s2["tp1"]>s2["tp2"]
flat=trend(200,0.0,noise=0.0005,last=0.004);res["chop: no trade"]=PZ.signal(flat,P,0,0,NAR)["direction"] is None and "chop" in PZ.signal(flat,P,0,0,NAR)["why"]
res["crowded longs (high funding): no long"]="crowded" in PZ.signal(up,P,0.0005,0,NAR)["why"]
res["rule zero: no narrative, no trade"]="no narrative" in PZ.signal(up,P,0,0,dict(ok=False,why="XRP: no CT/news talk"))["why"]
res["narrative leaning the other way blocks it"]="other way" in PZ.signal(up,P,0,0,dict(NAR,sentiment=-0.6))["why"]
part=up[:-1]+[(up[-1][0],up[-1][1],up[-1][2],up[-1][3],up[-2][4]*0.999,1)];res["the signal candle must close in the trade direction"]=PZ.signal(part,P,0,0,NAR)["direction"] is None
# ---- leverage vs liquidation ---------------------------------------------------------------------------------
G=PZ.BOOK_DEF["perps-grok"]
l1,_=PZ.choose_leverage(0.005,0.01,20,G,P);l2,_=PZ.choose_leverage(0.003,0.004,20,G,P);l3,w3=PZ.choose_leverage(0.005,0.01,5,G,P);l4,w4=PZ.choose_leverage(0.08,0.05,20,G,P)
res["grok leverage: 0.12/target, 8x-19x, inversely scaled"]=l1==12 and l2==19
res["grok: refused when the coin's max is under 8x"]=l3 is None and "only 5x" in w3
res["grok: refused when even 8x puts liquidation inside the stop"]=l4 is None and "inside the stop" in w4
res["liquidation distance uses Hyperliquid's maintenance margin"]=abs(PZ.liq_dist(10,20)-(0.1-0.025))<1e-12
bk=dict(bids=[(0.999,100),(0.998,1000)],asks=[(1.001,100),(1.002,1000)])
res["order-book fill: walks the levels"]=abs(PZ.walk(bk,1,200.2)-200.2/(100+100.1/1.002))<1e-12 and PZ.walk(bk,1,1e9) is None
# ---- paper books -------------------------------------------------------------------------------------------
class FH:
    def __init__(s):s.px={"XRP":1.0,"DOGE":0.1,"LINK":12.0};s.cand={}
    def meta(s):return {"XRP":dict(max_lev=20,sz_dec=0,delisted=False),"DOGE":dict(max_lev=10,sz_dec=0,delisted=False),"LINK":dict(max_lev=10,sz_dec=1,delisted=False)}
    def ctxs(s):return {k:dict(funding=0.0001,oi=1e6,mark=v,vol=1e8) for k,v in s.px.items()}
    def mids(s):return dict(s.px)
    def book(s,coin):p=s.px[coin];return dict(bids=[(p*0.9995,1e9)],asks=[(p*1.0005,1e9)])
    def candles(s,coin,interval="1h",n=300):return s.cand.get(coin)
S=[]
class N:
    token=""
    def send(s,t,buttons=None,tier="instant",digest=None):S.append((tier,t))
cfg=yaml.safe_load(open("config.yaml"));cfg.setdefault("perps",{})["exec_latency_s"]=0
db=DB(os.path.join(T,"p.db"));fh=FH()
ch=PZ.PerpBook(db,cfg,N(),"perps");gk=PZ.PerpBook(db,cfg,N(),"perps-grok")
sg=dict(direction=1,entry_ref=1.0,stop=0.985,tp1=1.015,tp2=1.03,stop_dist=0.015,score=85,features={},t=1,why="long: test")
pid=ch.open("XRP",sg,fh,{"narrative":"major:XRP","name":"XRP news/CT"},{},fh.px)
m=json.loads(db.q("select meta,cost from b_positions where id=?",(pid,))[0][0]) if pid else {}
res["charter: 0.25% of the book at risk at the stop"]=bool(pid) and abs(m["notional"]*(abs(m["entry"]-m["stop"])/m["entry"]+2*0.00045)-100*0.0025*((abs(m["entry"]-m["stop"])/m["entry"]+2*0.00045)/(0.015+0.00045+0.0002)))<0.02
res["TP/SL set before entry and reported as installed"]=bool(pid) and m["stop0"]==0.985 and m["tp1"]==1.015 and "orders installed" in plain(S[-1][1]) and "intended prices" in plain(S[-1][1])
c0=ch.cash;eq0=ch.equity(fh.px)
res["margin and the opening fee leave the cash; equity only loses the fee"]=bool(pid) and abs(c0-(100-m["notional"]-m["notional"]*0.00045))<1e-9 and abs(eq0-(100-m["notional"]*0.00045+m["side"]*(1.0-m["entry"])*m["notional"]/m["entry"]))<1e-9
time.sleep(0.01);x=json.loads(db.q("select meta from b_positions where id=?",(pid,))[0][0]);x["last_fund"]=time.time()-2*H;db.x("update b_positions set meta=? where id=?",(json.dumps(x),pid))
ch.check(fh.mids(),fh.ctxs());x=json.loads(db.q("select meta from b_positions where id=?",(pid,))[0][0])
res["funding accrued at the hourly rate (longs pay positive funding)"]=abs(x["funding"]-m["notional"]*0.0001*2)<1e-6
fh.px["XRP"]=1.016;ch.check(fh.mids(),fh.ctxs());x=json.loads(db.q("select meta,tokens from b_positions where id=?",(pid,))[0][0])
res["TP1: half closed, stop to breakeven (fees included)"]=x["tp1_done"] and x["stop"]>x["entry"] and db.q("select count(*) from ledger where trigger='take_profit_1'")[0][0]==1
fh.px["XRP"]=1.031;ch.check(fh.mids(),fh.ctxs())
st_=db.q("select status,realized from b_positions where id=?",(pid,))[0]
tr=sum(r[0] for r in db.q("select pnl from b_trades where book='perps' and side='sell'"))
res["TP2 closes the rest; realized P&L = price move - fees - funding"]=st_[0]=="closed" and abs(st_[1]-tr)<1e-9 and tr>0 and abs((ch.cash)-(100+tr))<1e-6
# short + stop
fh.px["DOGE"]=0.1;sg2=dict(direction=-1,entry_ref=0.1,stop=0.102,tp1=0.098,tp2=0.096,stop_dist=0.02,score=70,features={},t=2,why="short: test")
p2=ch.open("DOGE",sg2,fh,{},{},fh.px);fh.px["DOGE"]=0.1025;ch.check(fh.mids(),fh.ctxs())
r2=db.q("select status,realized from b_positions where id=?",(p2,))[0]
res["short stopped out when the price rises: a loss, coin cooled down"]=r2[0]=="closed" and r2[1]<0 and "cooldown" in (risk.entry_block(db,cfg,"perps",coin="DOGE") or "")
# grok: half the book at high leverage, liquidation simulated
fh.px["XRP"]=1.0;sg3=dict(direction=1,entry_ref=1.0,stop=0.995,tp1=1.01,tp2=1.02,stop_dist=0.005,score=90,features={},t=3,why="long: grok test")
p3=gk.open("XRP",sg3,fh,{},{},fh.px);m3=json.loads(db.q("select meta,cost from b_positions where id=?",(p3,))[0][0])
mg=db.q("select cost from b_positions where id=?",(p3,))[0][0]
res["grok: A+ signal commits half the book as margin at 8-19x"]=abs(mg-50)<1e-9 and 8<=m3["lev"]<=19 and abs(m3["notional"]-50*m3["lev"])<1e-9
fh.px["XRP"]=m3["liq"]*0.99;gk.check(fh.mids(),fh.ctxs());r3=db.q("select status,realized from b_positions where id=?",(p3,))[0]
res["grok: a gap through the liquidation price loses the margin (critical alert)"]=r3[0]=="closed" and abs(r3[1]+50+m3["notional"]*0.00045)<1e-6 and S[-1][0]=="critical" and "LIQUIDATED" in plain(S[-1][1])
gk.mark(fh.mids());res["grok drawdown switch (60%) not tripped by one liquidation of 50%"]=not gk.k("shutdown")
# correlation cap
db2=DB(os.path.join(T,"p2.db"));c2=yaml.safe_load(open("config.yaml"));c2.setdefault("perps",{}).update(exec_latency_s=0,max_same_direction=1)
b2=PZ.PerpBook(db2,c2,N(),"perps");fh.px.update(XRP=1.0,LINK=12.0)
series=[random.gauss(0,0.01) for _ in range(100)];RS={"XRP":series,"LINK":[x*1.1 for x in series]}
b2.open("XRP",dict(sg,t=9),fh,{},RS,fh.px);ok2=b2.open("LINK",dict(sg,entry_ref=12.0,stop=11.8,tp1=12.2,tp2=12.4,t=9),fh,{},RS,fh.px)
res["correlation cap: a coin moving with an open one (same side) counts as the same bet"]=ok2 is None and "correlation cap" in db2.q("select reason from b_signals where mint='LINK'")[0][0]
# drawdown switch on the charter book
db3=DB(os.path.join(T,"p3.db"));b3=PZ.PerpBook(db3,cfg,N(),"perps");b3.ks("cash",94.0);b3.ks("peak",100.0);b3.mark({})
res["charter drawdown switch: -5% from the peak stops new entries"]=bool(b3.k("shutdown")) and "drawdown" in (b3.blocked() or "")
# hourly runner: signals for every coin, rule zero refusals logged, both books trade the same signal
db4=DB(os.path.join(T,"p4.db"));fh4=FH();fh4.px={"XRP":up[-1][4],"DOGE":0.1,"LINK":12.0}
fh4.cand={"XRP":up,"DOGE":trend(200,0.0,noise=0.0005,last=0.004),"LINK":trend(200,0.002,px=12,noise=0.001,last=0.012,seed=3)}
class FE:
    def major(s,c,now=None):return dict(NAR,ok=c=="XRP") if c=="XRP" else dict(ok=False,why=f"{c}: no CT/news talk in the last hour")
c4=yaml.safe_load(open("config.yaml"));c4.setdefault("perps",{}).update(exec_latency_s=0,coins=["XRP","DOGE","LINK"])
r4=PZ.PerpsRunner(db4,c4,N(),FE());r4.H=fh4
out=r4.hourly(time.time())
opened={b:[m for (m,) in db4.q("select mint from b_positions where book=? and status='open'",(b,))] for b in("perps","perps-grok")}
res["hourly: XRP (narrative) opens in both books; LINK refused for no narrative"]=opened["perps"]==["XRP"] and "XRP" in opened["perps-grok"] and \
    "no narrative" in (db4.q("select reason from b_signals where mint='LINK' order by id desc limit 1") or [("",)])[0][0]
sgk=db4.kv_get("perps:signals");res["the last hour's verdict per coin is stored for /perps and the dashboard"]=sgk and {x["coin"] for x in sgk["all"]}=={"XRP","DOGE","LINK"}
txt=PZ.text(db4);res["/perps fits the phone"]=all(len(x)<=32 for x in plain(txt).split("\n")[1:])
# ---- backtest ------------------------------------------------------------------------------------------------
def walkpx(n,seed,px):
    random.seed(seed);rows=[];p=px;drift=0
    for i in range(n):
        if i%400==0:drift=random.choice([0.0012,0.0008,0.0,-0.0008,-0.0012])
        o=p;c=o*math.exp(drift+random.gauss(0,0.006));h=max(o,c)*math.exp(abs(random.gauss(0,0.003)));l=min(o,c)*math.exp(-abs(random.gauss(0,0.003)))
        rows.append((1650000000+i*H,o,h,l,c,100+random.random()*50));p=c
    return rows
data={"SOL":walkpx(9000,1,100),"DOGE":walkpx(9000,2,0.1),"LINK":walkpx(9000,3,10)}
b1=BP.full(data,{},"charter");b2_=BP.full(data,{},"grok")
res["backtest: both directions traded, fees x3 and Sharpe reported"]=b1["coins"]["SOL"]["all"]["longs"]>0 and b1["coins"]["SOL"]["all"]["shorts"]>0 and b1["pf_x3"] is not None and "sharpe_oos" in b1
res["backtest: the evidence bar (trades, Sharpe, fees x3, Monte Carlo, plateau, walk-forward)"]=[c["name"] for c in b1["checks"]]==["trades","Sharpe, held-back","PF, fees x3","Monte Carlo 95% drawdown","neighbour settings pass","walk-forward windows green"]
res["backtest: fees x3 are worse than fees x1"]=(b1["pf_x3"] or 0)<=(b1["pf"] or 0)
res["grok backtest counts liquidations and uses 8-19x"]=b2_["mode"]=="grok" and "liquidations" in b2_
res["backtest reports say what cannot be tested (narrative filter)"]="narrative filter cannot be tested" in " ".join(plain(BP.report(b1)).split()) and all(len(x)<=32 for x in plain(BP.report(b1)).split("\n")[1:])
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
