"""Trend Rider basket: the SAME Trend Rider v2 rules (strategy_v2.py, untouched) on more Solana coins, the way the
Master Build Prompt asks: "only after each coin passes its own backtest and I approve".
  1 /backtest basket   tests every candidate coin on its own daily history (Coinbase): the first 75% is training, the
                       last 25% is held back; each coin must pass its own gate (below). Nothing trades from this.
  2 /trend add COIN    you approve a coin that passed (6-digit code). /trend remove COIN takes it out again.
  3 the paper book 'trend-basket' then trades the approved coins with the v2 rules: entry after the daily close,
    stop 3x ATR14 trailing up, exit on a close under the 50-day average; risk 1% of the book per coin at the stop.
    Paper only: the real-money signer only swaps SOL and USDC.
Per-coin gate (fixed before any result): 10+ trades, profit factor >= 1.3 with costs x3, a profitable held-back
period, worst drawdown <= 25%, and profitable without its single best trade."""
import time,json,logging,threading,secrets
import candles,risk,strategy_v2 as S,backtest_v2 as BT
from tgfmt import term,kv,usd,SEP,wrap
log=logging.getLogger("bot");DAY=86400;KEY="backtest:basket";BOOK="trend-basket"
COINS={"JUP":("JUP-USD","JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"),"BONK":("BONK-USD","DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"),
       "WIF":("WIF-USD","EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"),"JTO":("JTO-USD","jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL"),
       "PYTH":("PYTH-USD","HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3"),"RAY":("RAY-USD","4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R")}
GATE=dict(min_trades=10,min_pf_x3=1.3,max_dd=0.25)
def judge(rows,cost=0.0015,split=0.75,p=None):
    """One coin, its own history -> metrics and its own gate verdict."""
    ind=S.Daily(rows,p);f=next((i for i in range(len(ind.ts)) if ind.ready(i)),None)
    if f is None or len(rows)<120:return dict(error=f"only {len(rows)} days of history (need 120+)")
    t0=ind.ts[f];t1=ind.ts[-1]+DAY;cut=int(t0+(t1-t0)*split)//DAY*DAY
    a=BT.run(rows,cost=cost,start_ts=t0,end_ts=t1,ind=ind);x3=BT.run(rows,cost=cost*3,start_ts=t0,end_ts=t1,ind=ind);ho=BT.run(rows,cost=cost,start_ts=cut,end_ts=t1,ind=ind)
    m=BT.metrics(a);m3=BT.metrics(x3);mh=BT.metrics(ho);P=[x["pnl"] for x in a["trades"]]
    wob=sum(P)-max(P+[0])
    checks=[("trades",f">= {GATE['min_trades']}",m["trades"],m["trades"]>=GATE["min_trades"]),("PF, costs x3",f">= {GATE['min_pf_x3']}",m3["pf"],(m3["pf"] or 0)>=GATE["min_pf_x3"]),
            ("held-back profit","> 0",mh["pnl"],mh["pnl"]>0),("worst drawdown",f"<= {GATE['max_dd']:.0%}",m["max_dd"],m["max_dd"]<=GATE["max_dd"]),("profit w/o best trade","> 0",wob,wob>0)]
    return dict(from_ts=t0,to_ts=t1,cut_ts=cut,days=len(rows),all=m,x3=m3,holdout=mh,hold=BT.hold(rows,t0,t1),
                checks=[dict(name=n,need=nd,value=v,ok=bool(ok)) for n,nd,v,ok in checks],passed=all(ok for *_,ok in checks))
def report(bt):
    rows=[kv("rules","Trend Rider v2 (unchanged)"),kv("cost",f"{bt.get('cost',0.0015):.2%}/side, gate at x3"),SEP,"coin trd  PFx3   ret  DD ok"]
    f2=lambda v:"–" if v is None else ("∞" if v>=99 else f"{v:.2f}")
    for k,r in bt["coins"].items():
        if r.get("error"):rows+=wrap(f"{k:<5} {r['error']}");continue
        a=r["all"];rows.append(f"{k:<5}{a['trades']:>3}{f2(r['x3']['pf']):>6}{a['ret']:>+6.0%}{a['max_dd']:>4.0%} {'✓' if r['passed'] else '✗'}")
    ok=[k for k,r in bt["coins"].items() if r.get("passed")]
    rows+=[SEP]+wrap("passed its own gate: "+(", ".join(ok) if ok else "none"))+[SEP]+wrap("nothing trades from this. approve a coin that passed: /trend add COIN (paper book trend-basket)")
    rows+=wrap("gate per coin: 10+ trades, PF >= 1.3 at costs x3, held-back profit, drawdown <= 25%, profitable without the best trade","· ")
    return term("backtest --basket",rows,("OK",f"🧺 basket: {len(ok)} of {len(bt['coins'])} passed"))
_lock=threading.Lock()
def run_async(db_path,notify,cfg):
    if not _lock.acquire(blocking=False):return False
    def job():
        from db import DB;from notify import Notifier
        try:
            d=DB(db_path);n=Notifier(d,bool(getattr(notify,"token","")));out=dict(ran=time.time(),cost=0.0015,coins={})
            for k,(sym,_) in COINS.items():
                try:candles.backfill(d,sym,DAY,start=candles.HISTORY_FROM);rows=candles.load(d,sym,DAY,since=candles.HISTORY_FROM)
                except Exception as e:out["coins"][k]=dict(error=f"no Coinbase history ({type(e).__name__})");continue
                out["coins"][k]=judge(rows) if rows else dict(error="no Coinbase history")
            d.kv_set(KEY,out);n.send(report(out))
        except Exception as e:log.exception("basket backtest");notify.send(term("backtest basket",wrap(f"{type(e).__name__}: {str(e)[:160]}"),("ERR","basket backtest failed")))
        finally:_lock.release()
    threading.Thread(target=job,daemon=True).start();return True
# ---- approval ------------------------------------------------------------------------------------------------
def approved(db):return list(db.kv_get("basket:coins") or [])
def cmd(db,args):
    """/trend add COIN [code] | /trend remove COIN | /trend basket"""
    bt=db.kv_get(KEY) or {}
    if not args or args[0]=="basket":
        a=approved(db);return term("trend basket",[kv("approved",", ".join(a) or "none"),kv("tested",time.strftime("%d %b",time.gmtime(bt["ran"])) if bt else "not yet"),SEP]+
            ([f"{k}: {'PASSED' if r.get('passed') else r.get('error') or 'failed'}" for k,r in (bt.get("coins") or {}).items()] or ["send /backtest basket"])+[SEP,"/trend add COIN | remove COIN"],("OK","🧺 Trend Rider basket"))
    if args[0]=="remove" and len(args)>=2:
        a=[x for x in approved(db) if x!=args[1].upper()];db.kv_set("basket:coins",a);return term("trend remove",[f"{args[1].upper()} removed","open positions keep their","stops and exits"],("OK","removed"))
    if args[0]=="add" and len(args)>=2:
        k=args[1].upper();r=(bt.get("coins") or {}).get(k)
        if k not in COINS:return term("trend add",["candidates: "+", ".join(COINS)],("WARN","unknown coin"))
        if not r or not r.get("passed"):return term("trend add",[f"{k} has not passed its own","backtest (/backtest basket)"],("WARN","not allowed"))
        if len(args)==2:
            c=f"{secrets.randbelow(10**6):06d}";db.kv_set("basket:code",dict(code=c,coin=k,exp=time.time()+120))
            return term("trend add",[f"{k} passed its own gate.","the paper book trend-basket","will trade it with the","Trend Rider v2 rules.",SEP,f"confirm: /trend add {k} {c}"],("WARN","confirm with code"))
        w=db.kv_get("basket:code") or {}
        if args[2]!=w.get("code") or w.get("coin")!=k or time.time()>w.get("exp",0):return term("trend add",["code wrong or expired"],("ERR","not added"))
        db.kv_set("basket:code",None);a=approved(db)
        if k not in a:a.append(k)
        db.kv_set("basket:coins",a);return term("trend add",[f"{k} added (paper)","trades after the next","daily close"],("OK","🧺 added"))
    return None
# ---- the paper book -------------------------------------------------------------------------------------------
class BasketRunner:
    """Trend Rider v2 on the approved coins, paper book 'trend-basket'. Called at every exit check (~30 s).
    Each coin is its own Trend Rider: the same entry, stop, trailing stop and trend exit as the tested rules."""
    def __init__(s,db,pp,notify,cfg):
        s.db,s.pp,s.n,s.cfg=db,pp,notify,cfg;s.b=((cfg.get("books") or {}).get(BOOK) or {})
        s.p=S.params(((cfg.get("books") or {}).get("trend") or {}).get("params"));s.cost=s.b.get("backtest_cost",0.0015);s.ind={};s.retry={}
    def pos(s,mint):
        r=s.db.q("select id,tokens,cost,opened,meta from b_positions where book=? and status='open' and mint=? limit 1",(BOOK,mint))
        return dict(id=r[0][0],tokens=r[0][1],cost=r[0][2],opened=r[0][3],meta=json.loads(r[0][4] or "{}")) if r else None
    def tick(s,now=None,prices=None):
        now=now or time.time();coins=approved(s.db)
        held=[c for c in COINS if s.pos(COINS[c][1])]
        live=sorted(set(coins)|set(held))
        if not live:return
        if prices is None:
            from chain import jup_prices
            try:prices=jup_prices([COINS[c][1] for c in live])
            except Exception as e:log.warning("basket prices: %s",e);prices={}
        for c in live:
            sym,mint=COINS[c]
            try:s.coin(c,sym,mint,prices.get(mint),now,new_ok=c in coins and s.pp.enabled,n=max(1,len(coins)))
            except Exception:log.exception("basket %s",c)
    def coin(s,c,sym,mint,px,now,new_ok,n):
        closed_end=int(now//DAY*DAY);lt=candles.last_ts(s.db,sym,DAY)
        if (lt is None or lt<closed_end-DAY) and now>=s.retry.get(c,0) and now-closed_end>=300:
            try:lt=candles.sync(s.db,sym,DAY,need_days=200,now=now)
            except Exception as e:log.warning("basket candles %s: %s",c,e);s.retry[c]=now+300
        if not lt:return
        if c not in s.ind or s.ind[c].ts[-1]!=lt:s.ind[c]=S.Daily(candles.load(s.db,sym,DAY,since=lt-220*DAY,until=lt+DAY),s.p)
        ind=s.ind[c];i=len(ind.ts)-1
        if i<0:return
        p=s.pos(mint);new_day=s.db.kv_get(f"basket:{c}:last_eval")!=lt
        if new_day:s.db.kv_set(f"basket:{c}:last_eval",lt)
        if p:
            m=p["meta"]
            if new_day and ind.ts[i]>m.get("signal_ts",0):      # at a new daily close: trend exit, else raise the stop
                if ind.trend_exit(i):s.pp.exit_position(p["id"],"trend_break",f"{c} closed under its 50-day average");return
                hi=max(m["hi"],ind.c[i]);m=dict(m,hi=hi,stop=max(m["stop"],ind.trail(i,hi)))
                s.db.x("update b_positions set meta=? where id=?",(json.dumps(m),p["id"]))
            if px and px<=m["stop"]:s.pp.exit_position(p["id"],"stop_loss",f"{c} stop ${m['stop']:.6g} hit")
            return
        if not(new_day and new_ok):return
        sg=ind.signal(i)
        if not sg:return
        meta=dict(v=2,stop=sg["stop"],hi=sg["ref"],signal_ref=sg["ref"],signal_ts=lt,version=S.VERSION,features=sg["features"],coin=c)
        if now-(lt+DAY)>s.b.get("max_entry_delay_h",6)*3600:return s.pp.signal(BOOK,mint,"buy","refused","late: more than 6h after the daily close",None,sg["features"])
        if not px or not(sg["stop"]<px<=sg["ref"]*(1+s.p["max_chase"])):return s.pp.signal(BOOK,mint,"buy","refused","price moved past the entry rules",None,sg["features"])
        eq=s.pp.tradable(mark=False);rf,_=risk.risk_fraction(s.db,s.cfg,BOOK);p2=dict(s.p,risk=min(s.p["risk"],rf))
        size=S.size_usd(eq,sg["stop_pct"],s.cost,p2,cap_usd=eq/n)      # 1% of the book at the stop, at most 1/n of the book per coin
        ok,why=S.worth_it(size,eq,s.cost,0.0,p2)
        if not ok:return s.pp.signal(BOOK,mint,"buy","refused",why,size,sg["features"])
        sig=f"basket:{c}:{lt}";pid=s.pp.on_buy(BOOK,mint,None,None,sig,{},meta=meta,size_usd=size)
        if pid:
            fill=s.db.q("select price from b_trades where book=? and side='buy' and sig=?",(BOOK,sig))
            if fill:s.db.x("update b_positions set meta=? where id=?",(json.dumps(dict(meta,entry=fill[0][0])),pid))
            s.n.send(term("trend basket --entry",[kv("coin",c),kv("stop",f"${sg['stop']:.6g}"),kv("size",usd(size)),SEP,"exits: trailing stop (3 ATR)","or a close under the","50-day average (paper)"],("OK","🧺 Trend Rider basket entry")))
    def status_rows(s):
        rows=[]
        for c in approved(s.db):
            sym,mint=COINS[c];p=s.pos(mint)
            rows.append(f"{c}: "+(f"open, stop ${p['meta']['stop']:.6g}" if p else "waiting for a signal"))
        return rows or ["no coin approved yet"]
