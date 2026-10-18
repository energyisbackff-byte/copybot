"""Trend Rider v2 runner, called about every 30 s from the main loop. Two legs share the same signals:
  'trend'       the paper book (always runs)
  'trend-live'  the real-money book (only while you switched it on with /live arm or /live trial)
Every check: an open position's stop is compared with the live SOL price, so a stop is never late by a day. Exits
never wait for arming or approvals.
Once a day, after the daily candle closes (00:05 UTC, to let the data settle): open positions get the trend exit
(close below SMA50) or their trailing stop raised; then a new entry if the rules say so. Entries are skipped if the bot
is more than 6 h late for that close, or if the price already ran 5% past the signal."""
import time,json,logging,threading
import candles,strategy_v2 as S,backtest_v2 as BT,costs
from chain import jup_price,WSOL
from tgfmt import term,kv,usd as fusd,SEP,wrap
log=logging.getLogger("bot");SYM="SOL-USD";DAY=86400
BT_KEY="backtest:v2"     # v2 results live under their own key: v1 code (after a /rollback) never mistakes them for its own
class TrendRunner:
    def __init__(s,db,pp,notify,cfg,live=None):
        s.db,s.pp,s.n,s.cfg,s.live=db,pp,notify,cfg,live
        s.b=(cfg.get("books") or {}).get("trend") or {};s.p=S.params(s.b.get("params"));s.ind=None;s.retry_at=0
        s.cost=s.b.get("backtest_cost",0.0015);s.prio=((cfg.get("live") or {}).get("priority_max_lamports",100000))
        if not db.kv_get("trend:v2_started"):
            db.kv_set("trend:v2_started",time.time());was=db.kv_get("trend:paused");db.kv_set("trend:paused",False)
            db.kv_set("trend:shutdown",None);db.kv_set("trend:peak",db.kv_get("trend:last_equity",db.kv_get("trend:cash")))  # fresh start for v2
            notify.send(term("trend --v2",[kv("version",S.VERSION),kv("book","paper"),SEP,"daily SOL trend follower;","checks after 00:00 UTC.",
                "v1 positions are closed."]+(["the trend book was paused","for v1; it is on again.","pause: /book trend off"] if was else []),("OK","📈 Trend Rider v2 started")))
    def k(s,name,d=None):return s.db.kv_get(f"trend:{name}",d)
    def ks(s,name,v):s.db.kv_set(f"trend:{name}",v)
    def paper_pos(s):
        r=s.db.q("select id,tokens,cost,opened,meta from b_positions where book='trend' and status='open' limit 1")
        return dict(id=r[0][0],tokens=r[0][1],cost=r[0][2],opened=r[0][3],meta=json.loads(r[0][4] or "{}")) if r else None
    def legs(s):
        out=[("trend",s.paper_pos())]
        if s.live:
            try:out.append(("trend-live",s.live.position()))
            except Exception:log.exception("live position lookup")
        return out
    def valid(s,px,meta):
        """Still worth buying at px? Above the stop and not more than max_chase past the signal close."""
        return meta["stop"]<px<=meta["signal_ref"]*(1+s.p["max_chase"])
    def fee_usd(s,px):return (5000+s.prio/2)/1e9*px      # typical network fee: base + half the priority cap
    # ---- the loop ---------------------------------------------------------------------------------------
    def tick(s,now=None):
        now=now or time.time();closed_end=int(now//DAY*DAY)
        lt=candles.last_ts(s.db,SYM,DAY)
        if (lt is None or lt<closed_end-DAY) and now>=s.retry_at and (now-closed_end>=300 or lt is None or lt<closed_end-2*DAY):
            try:lt=candles.sync(s.db,SYM,DAY,need_days=200,now=now)
            except Exception as e:log.warning("trend candles: %s (retry in 2 min)",e);s.retry_at=now+120
        if not lt:return
        if s.ind is None or s.ind.ts[-1]!=lt:s.ind=S.Daily(candles.load(s.db,SYM,DAY,since=lt-220*DAY,until=lt+DAY),s.p)
        ind=s.ind;i=len(ind.ts)-1
        if i<0:return
        px=jup_price(WSOL)[0] or ind.c[i];new_day=s.k("last_eval")!=lt
        if new_day:                                          # the daily decision, once per closed candle: exits/trail first
            s.ks("last_eval",lt);st=ind.state(i);s.ks("state",dict(st,t=lt+DAY,price=px,version=S.VERSION))
            for leg,pos in s.legs():
                if pos:s.daily(pos,ind,i,leg)
        for leg,pos in s.legs():                             # every check: the stop against the live price
            if pos:s.watch(pos,px,leg)
        if s.live:
            try:s.live.process_approval(px,s.valid)
            except Exception:log.exception("live approval (continuing)")
        if not new_day:return
        sg=ind.signal(i)
        if not sg:return
        meta=dict(v=2,stop=sg["stop"],hi=sg["ref"],signal_ref=sg["ref"],signal_ts=lt,version=S.VERSION,features=sg["features"])
        late=now-(lt+DAY)>s.b.get("max_entry_delay_h",6)*3600
        why="late: more than 6h after the daily close" if late else None if s.valid(px,meta) else f"price ${px:.2f} moved past the entry rules"
        if not s.paper_pos():
            if why:s.pp.signal("trend-rider",WSOL,"buy","refused",why,None,sg["features"])
            else:s.enter_paper(sg,meta,lt,px)
        if s.live and s.live.armed():
            try:
                if s.live.position():return
                if why:s.live.signal(WSOL,"refused",why,None,sg["features"]);return
                eq=s.live.equity();size=S.size_usd(eq,sg["stop_pct"],s.cost,s.p,cap_usd=s.live.cap())
                ok,why2=S.worth_it(size,eq,s.cost,s.fee_usd(px),s.p)
                if not ok:s.live.signal(WSOL,"refused",why2,size,sg["features"]);return
                s.live.propose(size,meta,f"trend-live:{lt}")
            except Exception as e:log.exception("live propose");s.live.signal(WSOL,"refused",f"live error: {type(e).__name__}",None)
    def enter_paper(s,sg,meta,lt,px):
        eq=s.pp.equity(mark=False);size=S.size_usd(eq,sg["stop_pct"],s.cost,s.p)
        ok,why=S.worth_it(size,eq,s.cost,s.fee_usd(px),s.p)
        if not ok:s.pp.signal("trend-rider",WSOL,"buy","refused",why,size,sg["features"]);return None
        pid=s.pp.on_buy("trend-rider",WSOL,None,None,f"trend2:{lt}",{},meta=meta,size_usd=size)
        if pid:
            fill=s.db.q("select price from b_trades where book='trend' and side='buy' and sig=?",(f"trend2:{lt}",))[0][0]
            s.db.x("update b_positions set meta=? where id=?",(json.dumps(dict(meta,entry=fill)),pid))
            s.n.send(term("trend --entry",[kv("mode",s.pp.label()),SEP,kv("entry",f"${fill:.3f}"),kv("stop",f"${meta['stop']:.3f}"),
                kv("risk",f"{(fill-meta['stop'])/fill:.1%} to the stop"),kv("size",fusd(size)),SEP,"exits: trailing stop (3 ATR)","or a close below the","50-day average"],("OK","📈 TREND RIDER entry")))
        return pid
    def exit(s,leg,pos,reason,note):
        if leg=="trend-live":return s.live.exit(pos["id"],reason,note)
        s.pp.exit_position(pos["id"],reason,note);return True
    def watch(s,pos,px,leg):
        """Every check: the stop against the live price. v1 leftovers are closed."""
        m=pos["meta"]
        if m.get("v")!=2:
            if leg=="trend":s.exit(leg,pos,"strategy_replaced","v1 position closed (strategy replaced)")
            return
        if px<=m["stop"]:s.exit(leg,pos,"stop_loss",f"stop ${m['stop']:.3f} hit")
    def daily(s,pos,ind,i,leg):
        """At a new daily close: trend exit, else raise the trailing stop."""
        m=pos["meta"]
        if m.get("v")!=2 or ind.ts[i]<=m.get("signal_ts",0):return
        if ind.trend_exit(i):s.exit(leg,pos,"trend_break",f"close ${ind.c[i]:.2f} under 50-day avg");return
        hi=max(m["hi"],ind.c[i]);new=max(m["stop"],ind.trail(i,hi));moved=new>m["stop"]*1.01
        s.db.x("update b_positions set meta=? where id=?",(json.dumps(dict(m,hi=hi,stop=new)),pos["id"]))
        if moved:s.n.send(term("trend --trail",[kv("book","REAL MONEY" if leg=="trend-live" else "paper"),kv("close",f"${ind.c[i]:.2f}"),kv("stop",f"${m['stop']:.3f} -> ${new:.3f}")],("OK","🛡 stop raised")))
    # ---- status --------------------------------------------------------------------------------------------
    def status_rows(s):
        st=s.k("state") or {};pos=s.paper_pos();rows=[kv("version",S.VERSION),kv("book",s.pp.label())]
        if st.get("ready"):
            rows+=[kv("SOL",f"${st['price']:.2f} (close ${st['close']:.2f})"),kv("avg 20d",f"${st['sma20']:.2f}"),kv("avg 50d",f"${st['sma50']:.2f}"),
                   kv("20d high",f"${st['high20']:.2f}"),kv("trend","UP" if st["uptrend"] else "not up"),kv("signal","BUY" if st["signal"] else "none")]
        else:rows.append("waiting for daily candles")
        rows.append(kv("next","after 00:00 UTC daily close"))
        if pos and pos["meta"].get("v")==2:
            m=pos["meta"];rows+=[SEP,"paper position:",kv("entry",f"${m.get('entry',0):.3f}"),kv("stop",f"${m['stop']:.3f}"),kv("high",f"${m['hi']:.2f}")]
        else:rows+=[SEP,"paper: no position (USDC)"]
        if s.live:
            try:rows+=[SEP,kv("live",{"arm":"🔓 ARMED","trial":"🔓 TRIAL"}.get(s.live.mode(),"🔓 available, off (/live)"))]
            except Exception:pass
        bt=s.db.kv_get(BT_KEY)
        if bt and bt.get("version")==S.VERSION and "checks" in bt:rows+=[SEP,kv("backtest","PASSED" if bt["passed"] else "did NOT pass"),kv("ran",time.strftime("%d %b %H:%M",time.localtime(bt["ran"])))]
        else:rows+=[SEP,"v2 backtest: not run yet","send /backtest"]
        return rows
# ---- backtest command ------------------------------------------------------------------------------------
def pct(v,d=1):return "–" if v is None else f"{v:+.{d}%}"
def backtest_report(bt):
    if bt.get("error"):return term("backtest",wrap(bt["error"]),("ERR","backtest failed"))
    if bt.get("version")!=S.VERSION:return term("backtest",[f"last result is {bt.get('version','?')}","(retired). send /backtest","for "+S.VERSION],("WARN","no v2 result yet"))
    C=bt["coins"];P=bt["pooled"];f2=lambda v:"–" if v is None else ("∞" if v>=99 else f"{v:.2f}")
    rows=[kv("version",bt["version"]),kv("period",time.strftime("%b %Y",time.gmtime(bt["from_ts"]))+" to "+time.strftime("%b %Y",time.gmtime(bt["to_ts"]))),
          kv("held bk","from "+time.strftime("%d %b %Y",time.gmtime(bt["cut_ts"]))),kv("cost",f"{bt['cost']:.2%}/side"),*wrap("  "+bt.get("cost_source",""),"","  "),SEP,
          "coin  trd    PF  return   maxDD"]
    for k in BT.COINS:
        a=C[k]["all"];rows.append(f"{k:<4}{a['trades']:>5}{f2(a['pf']):>6}{pct(a['ret']):>8}{a['max_dd']:>8.1%}")
    rows.append(f"{'all':<4}{P['trades']:>5}{f2(P['pf']):>6}")
    rows+=[SEP,"held-back period only:"]+[f"  {k} {pct(C[k]['holdout']['ret'])} ({C[k]['holdout']['trades']} trades)" for k in BT.COINS]
    rows+=["just holding the coin:"]+[f"  {k} {pct(C[k]['hold']['ret'],0)} (worst drop {C[k]['hold']['max_dd']:.0%})" for k in BT.COINS]
    rows+=[SEP,"gate (fixed before results):"]
    for c in bt["checks"]:
        v=c["value"];vs="–" if v is None else (f"{v:.0%}" if "drawdown" in c["name"] else f"{v:.2f}" if isinstance(v,float) and "PF" in c["name"] else
              f"${v:+.2f} per $1k books" if isinstance(v,float) else str(v))
        rows+=wrap(f"{'PASS' if c['ok'] else 'FAIL'} {c['name']} {c['need']}");rows.append(f"     got {vs}")
    rows+=[SEP]+sum((wrap(n,"· ") for n in bt.get("notes",[])),[])
    br=bt.get("bar")
    if br:
        rows+=[SEP,"new evidence bar (Master","Build Prompt):"]
        for c in br["checks"]:
            v=c["value"];vs="–" if v is None else (f"{v:.0%}" if any(w in c["name"] for w in("drawdown","pass","green")) else f"{v:.2f}" if isinstance(v,float) else str(v))
            rows+=wrap(f"{'PASS' if c['ok'] else 'FAIL'} {c['name']} {c['need']}");rows.append(f"     got {vs}")
        rows+=wrap("bar "+("PASSED" if br["passed"] else "NOT passed: /live arm needs it; /live trial does not"),"",)
    rows+=[SEP,f"held-back period viewed {bt.get('holdout_views',1)}x","real money: /live (it shows","exactly what is still missing)"]
    return term("backtest --trend-rider-v2",rows,("OK","✅ v2 passed its gate") if bt["passed"] else ("WARN","v2 did NOT pass its gate"))
_bt_lock=threading.Lock()
def run_backtest_async(db_path,notify,cfg):
    """/backtest: fetch daily history for SOL, BTC, ETH (first run: about a minute), measure costs, run, store, report."""
    if not _bt_lock.acquire(blocking=False):return False
    def job():
        from db import DB;from notify import Notifier
        try:
            d=DB(db_path);n=Notifier(d,bool(getattr(notify,"token","")));b=(cfg.get("books") or {}).get("trend") or {}
            data={}
            for k,sym in candles.COINS.items():
                candles.backfill(d,sym,DAY);data[k]=candles.load(d,sym,DAY,since=candles.HISTORY_FROM)
            try:costs.measure(d,n=3,usd=20,priority_lamports=(cfg.get("live") or {}).get("priority_max_lamports",100000))
            except Exception as e:log.warning("cost measurement: %s",e)
            cost,src=costs.backtest_cost(d,b.get("backtest_cost",0.0015))
            v1=d.kv_get("backtest:last") or {}     # v1's result stays under its old key (so a /rollback to v1 still sees it)
            bt=BT.full(data,b.get("params"),cost=cost,seen_from=v1.get("from_ts"));bt["cost_source"]=src
            seen=d.kv_get("backtest:holdout_views",{});seen[S.VERSION]=seen.get(S.VERSION,0)+1;d.kv_set("backtest:holdout_views",seen)
            bt["holdout_views"]=seen[S.VERSION];d.kv_set(BT_KEY,bt);n.send(backtest_report(bt))
        except Exception as e:
            log.exception("backtest failed");notify.send(term("backtest",wrap(f"{type(e).__name__}: {str(e)[:160]}"),("ERR","backtest failed")))
        finally:_bt_lock.release()
    threading.Thread(target=job,daemon=True).start();return True
