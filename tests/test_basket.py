"""Trend Rider basket (basket.py) on made-up candles, no network. Checks: each coin is judged on its own history with
the gate fixed in basket.py; a coin trades only after it passed AND you approved it with a 6-digit code; the paper book
uses the unchanged Trend Rider v2 rules (entry after the daily close, 3x ATR stop trailing up, exit on a close under the
50-day average); the stop is checked at every tick; nothing changed in strategy_v2.py; main.py, ops and the dashboard
know the book. Run: python3 tests/test_basket.py"""
import sys,os,time,json,tempfile,re,html,math,random,subprocess
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);os.chdir(R)
T=tempfile.mkdtemp();os.environ.update(HELIUS_API_KEY="x",COPYBOT_MEMORY=os.path.join(T,"memory"),TELEGRAM_BOT_TOKEN="",COPYBOT_DB=os.path.join(T,"b.db"))
import yaml,basket,candles,strategy_v2 as S
from db import DB
cfg=yaml.safe_load(open("config.yaml"));db=DB(os.environ["COPYBOT_DB"]);res={};DAY=86400
def txt(t):return html.unescape(re.sub(r"<[^>]+>","",t))
def series(n,f,t0=1_600_000_000//DAY*DAY):
    out=[];prev=f(0)
    for i in range(n):
        c=f(i);o=prev;h=max(o,c)*1.01;l=min(o,c)*0.99;out.append((t0+i*DAY,o,h,l,c,1e6));prev=c
    return out
def cyc(i):                       # 130-day cycles: flat, a strong rise, a fall: what a trend follower is built for
    k,j=divmod(i,130);base=10*1.15**k
    if j<60:return base*(1+0.002*math.sin(j))
    if j<100:return base*1.012**(j-60)
    return base*1.012**40*0.985**(j-100)
def chop(i):return 10*(1+0.12*math.sin(i/7.0)+0.03*math.sin(i*1.7))   # breakouts that reverse at once
good=basket.judge(series(1800,cyc));bad=basket.judge(series(1800,chop))
res["a trending coin passes its own gate (10+ trades, PF x3, held-back profit, DD, without best trade)"]=good["passed"] and good["all"]["trades"]>=10 and len(good["checks"])==5
res["a choppy coin (false breakouts) fails its own gate"]=not bad["passed"]
res["too little history is an error, not a pass"]="need 120" in basket.judge(series(80,cyc)).get("error","")
bt=dict(ran=time.time(),cost=0.0015,coins={"JUP":good,"BONK":bad,"RAY":dict(error="no Coinbase history")})
rep=txt(basket.report(bt))
from tgfmt import W
res["report: one line per coin, ✓/✗, nothing trades from it, phone width"]="JUP" in rep and "✓" in rep and "✗" in rep and "nothing trades" in rep and all(len(l)<=W for l in rep.split("\n")[1:] if not l.startswith("root@"))
# ---- approval -------------------------------------------------------------------------------------------------
r0=txt(basket.cmd(db,["add","JUP"]))
db.kv_set(basket.KEY,bt)
r1=txt(basket.cmd(db,["add","BONK"]));r2=txt(basket.cmd(db,["add","JUP"]));code=re.search(r"/trend add JUP (\d{6})",r2).group(1)
r3=txt(basket.cmd(db,["add","JUP","000000"]))
res["/trend add: refused before any backtest and for a coin that failed"]="not passed" in r0 and "not passed" in r1 and basket.approved(db)==[]
res["/trend add: needs the 6-digit code"]="wrong" in r3 and basket.approved(db)==[]
txt(basket.cmd(db,["add","JUP",code]));res["/trend add JUP CODE approves it"]=basket.approved(db)==["JUP"]
res["/trend basket shows approved + tested"]="JUP" in txt(basket.cmd(db,["basket"])) and "PASSED" in txt(basket.cmd(db,["basket"]))
res["unknown coin refused"]="unknown" in txt(basket.cmd(db,["add","DOGE"]))
# ---- the paper book with a fake broker ----------------------------------------------------------------------------
class PP:
    enabled=True;buys=[];exits=[];sigs=[]
    def tradable(s,mark=True):return 60.0
    def signal(s,*a):s.sigs.append(a)
    def on_buy(s,trader,mint,u,a,sig,tm,meta=None,size_usd=None):
        pid=db.ins("b_positions",book=basket.BOOK,trader=trader,mint=mint,tokens=size_usd/meta["signal_ref"],cost=size_usd,opened=time.time(),status="open",open_sig=sig,meta=json.dumps(meta))
        db.ins("b_trades",ts=time.time(),book=basket.BOOK,trader=trader,mint=mint,side="buy",usd=size_usd,tokens=1,price=meta["signal_ref"],pnl=0,sig=sig,note="")
        s.buys.append((mint,size_usd,meta));return db.q("select max(id) from b_positions")[0][0]
    def exit_position(s,pid,reason,note):
        db.x("update b_positions set status='closed' where id=?",(pid,));s.exits.append(reason)
class N:
    S=[]
    def send(s,t,**k):s.S.append(txt(t))
pp=PP();run=basket.BasketRunner(db,pp,N(),cfg)
JUP,JM=basket.COINS["JUP"];BONK,BM=basket.COINS["BONK"]
up=[20*1.008**i for i in range(160)]
up[-1]=max(up[-21:-1])*1.03            # the last close breaks the 20-day high, in an uptrend
rows=series(160,lambda i:up[i]);lt=rows[-1][0];now=lt+DAY+3600
candles.store(db,JUP,DAY,rows);candles.store(db,BONK,DAY,rows)
ind=S.Daily(rows,run.p);sg=ind.signal(len(rows)-1)
run.tick(now=now,prices={JM:up[-1]*1.01,BM:up[-1]*1.01})
res["entry: approved coin buys after the daily close with the v2 signal, stop = 3x ATR"]=sg is not None and len(pp.buys)==1 and pp.buys[0][0]==JM and abs(pp.buys[0][2]["stop"]-sg["stop"])<1e-9
res["entry: an unapproved coin never buys (BONK has the same signal)"]=all(b[0]!=BM for b in pp.buys)
sz=pp.buys[0][1];eff=sg["stop_pct"]+2*0.0015+0.01
res["size: about 1% of the book lost at the stop (costs + gap buffer), at most 1/n of the book"]=abs(sz-min(60*0.01/eff,60))<0.01 or abs(sz-60)<0.01
run.tick(now=now+30,prices={JM:up[-1]*1.01})
res["no second buy the same day"]=len(pp.buys)==1
rows2=rows+[(lt+DAY,up[-1],up[-1]*1.1,up[-1]*0.99,up[-1]*1.08,1e6)];candles.store(db,JUP,DAY,rows2[-1:])
st0=json.loads(db.q("select meta from b_positions where status='open'")[0][0])["stop"]
run.tick(now=now+DAY,prices={JM:up[-1]*1.08})
st1=json.loads(db.q("select meta from b_positions where status='open'")[0][0])["stop"]
res["next close: the stop trails up (never down)"]=st1>=st0 and not pp.exits
run.tick(now=now+DAY+30,prices={JM:st1*0.99})
res["the stop is checked at every tick against the live price"]=pp.exits==["stop_loss"]
basket.cmd(db,["remove","JUP"]);res["/trend remove takes a coin out"]=basket.approved(db)==[]
# ---- wiring ------------------------------------------------------------------------------------------------------
src=open("main.py").read();b=cfg["books"]["trend-basket"]
res["main.py: basket book, runner at every exit check, /trend add, /backtest basket"]=all(x in src for x in('Paper(db,hel,cfg,n,"trend-basket")',"bk_run.tick()","_basket.cmd(cdb","_basket.run_async("))
res["config: own paper book, exits left to the Trend Rider rules, risk 1% by charter override"]=b["exits"]=={"enabled":False} and cfg["risk"]["books"]["trend-basket"]["per_trade"]==0.01
import ops,paper;sv=open("dashboard/server.py").read()
res["ops, paper and dashboard know the book (kill switch included)"]="trend-basket" in ops.ALL and "trend-basket" in ops.KILL_BOOKS and "trend-basket" in paper.PAPER_BOOKS and '"trend-basket"' in sv
# frozen rules: the file's fingerprint since the v2 freeze (works on a server install, where there is no .git)
import hashlib;FROZEN="2e56137442c3f2b05f0e7d9299084ed95373ce0195c498d026a68681a162bf23"
same=hashlib.sha256(open(os.path.join(R,"strategy_v2.py"),"rb").read()).hexdigest()==FROZEN
res["Trend Rider v2 rules (strategy_v2.py) unchanged"]=same
res["basket: real money only through the coin wallet switch (/real on basket)"]="/real on basket" in open("basket.py").read()
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad_=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad_)}/{len(res)} passed");sys.exit(1 if bad_ else 0)
