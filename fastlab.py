"""/backtest fast: downloads 1-hour SOL/BTC/ETH history (first run 5-10 minutes), measures costs, tests both faster
candidates (strategy_fast) with backtest_fast, stores kv 'backtest:fast' and sends one report per candidate.
Research only: nothing here trades, on paper or for real."""
import time,logging,threading
import candles,costs,backtest_fast as BF
from tgfmt import term,kv,SEP,wrap
log=logging.getLogger("bot");H=3600;KEY="backtest:fast"
def pct(v,d=1):return "–" if v is None else f"{v:+.{d}%}"
def f2(v):return "–" if v is None else ("∞" if v>=99 else f"{v:.2f}")
def money(v):return "–" if v is None else f"${v:+,.0f}"
def report(bt):
    """-> list of Telegram messages: one per candidate, then a short summary."""
    if bt.get("error"):return [term("backtest fast",wrap(bt["error"]),("ERR","fast backtest failed"))]
    msgs=[];period=time.strftime("%b %Y",time.gmtime(bt["from_ts"]))+" to "+time.strftime("%b %Y",time.gmtime(bt["to_ts"]))
    for c in bt["candidates"]:
        C=c["coins"];P=c["pooled"];s=C["SOL"]["all"]
        rows=[kv("version",c["version"]),kv("period",period),kv("held bk","from "+time.strftime("%d %b %Y",time.gmtime(bt["cut_ts"]))),
              kv("cost",f"{bt['cost']:.2%}/side"),*wrap("  "+bt.get("cost_source",""),"","  "),SEP,"coin trd/mo    PF  return  maxDD"]
        for k in BF.COINS:
            a=C[k]["all"];rows.append(f"{k:<4}{a['per_month']:>7.1f}{f2(a['pf']):>6}{pct(a['ret']):>8}{a['max_dd']:>7.1%}")
        rows+=[f"all: {P['trades']} trades, PF {f2(P['pf'])}",SEP,"SOL per $1,000 (all years):",f"  won {money(s['won'])} lost {money(-s['lost'])}",
               f"  kept {money(s['pnl'])} in {s['trades']} trades",f"  win rate {s['win_rate']:.0%}" if s["win_rate"] is not None else "  win rate –",
               "held-back period only:"]+[f"  {k} {pct(C[k]['holdout']['ret'])} ({C[k]['holdout']['trades']} trades)" for k in BF.COINS]
        rows+=["costs doubled:"]+[f"  {k} {pct(C[k]['dbl']['ret'])}" for k in BF.COINS]
        rows+=[SEP,"gate (fixed before results):"]
        for ch in c["checks"]:
            v=ch["value"];vs="–" if v is None else (f"{v:.1%}" if "drawdown" in ch["name"] else f"{v:.2f}" if "PF" in ch["name"] else
                f"${v:+,.2f} per $1k books" if isinstance(v,float) else str(v))
            rows+=wrap(f"{'PASS' if ch['ok'] else 'FAIL'} {ch['name']} {ch['need']}");rows.append(f"     got {vs}")
        msgs.append(term("backtest --fast "+c["version"].split()[0],rows,("OK","✅ "+c["name"]+" passed") if c["passed"] else ("WARN",c["name"]+": NOT passed")))
    rows=[]
    for c in bt["candidates"]:
        s=c["coins"]["SOL"]["all"]
        rows+=[f"{c['name']}: {'PASSED' if c['passed'] else 'not passed'}",f"  SOL {s['per_month']:.1f} trades a month,",f"  {pct(s['ret'])} over the period"]
    rows+=[SEP]+sum((wrap(n,"· ") for n in bt.get("notes",[])),[])
    ok=[c for c in bt["candidates"] if c["passed"]]
    rows+=[SEP]+(wrap("next: tell Claude; the passing one goes on paper first, real money only after that.") if ok else
                 wrap("next: no fast strategy earned real money. v2 stays the real-money strategy."))
    rows+=[f"held-back viewed {bt.get('holdout_views',1)}x"]
    msgs.append(term("backtest --fast --summary",rows,("OK" if ok else "WARN","fast: "+str(len(ok))+" of "+str(len(bt["candidates"]))+" passed")))
    return msgs
_lock=threading.Lock()
def run_async(db_path,notify,cfg):
    if not _lock.acquire(blocking=False):return False
    def job():
        from db import DB;from notify import Notifier
        try:
            d=DB(db_path);n=Notifier(d,bool(getattr(notify,"token","")));b=(cfg.get("books") or {}).get("trend") or {}
            data={}
            for k,sym in candles.COINS.items():
                candles.backfill(d,sym,H,start=candles.HISTORY_FROM);data[k]=candles.load(d,sym,H,since=candles.HISTORY_FROM)
            try:costs.measure(d,n=3,usd=20,priority_lamports=(cfg.get("live") or {}).get("priority_max_lamports",100000))
            except Exception as e:log.warning("cost measurement: %s",e)
            cost,src=costs.backtest_cost(d,b.get("backtest_cost",0.0015))
            v2=d.kv_get("backtest:v2") or {}
            bt=BF.full(data,cost=cost,seen_from=v2.get("from_ts"));bt["cost_source"]=src
            if not bt.get("error"):
                seen=d.kv_get("backtest:holdout_views",{})
                for c in bt["candidates"]:seen[c["version"]]=seen.get(c["version"],0)+1
                d.kv_set("backtest:holdout_views",seen);bt["holdout_views"]=max(seen[c["version"]] for c in bt["candidates"])
            d.kv_set(KEY,bt)
            for m in report(bt):n.send(m)
        except Exception as e:
            log.exception("fast backtest failed");notify.send(term("backtest fast",wrap(f"{type(e).__name__}: {str(e)[:160]}"),("ERR","fast backtest failed")))
        finally:_lock.release()
    threading.Thread(target=job,daemon=True).start();return True
