"""Trend Rider live runner (paper book 'trend'). Called about every 30-60 s from the main loop.
Uses strategy_trend on stored closed candles (same code as the backtest); fills go through Paper.on_buy / exit_position,
so the risk gate, real Jupiter quotes and fees apply. Exits use the live SOL price between candles."""
import time,json,logging,threading
import candles,strategy_trend as S,backtest as BT
from chain import jup_price,WSOL
from tgfmt import term,kv,usd as fusd,SEP
log=logging.getLogger("bot")
class TrendRunner:
    """Drives one or two legs with the same signals: the paper book 'trend' (always) and the real-money book
    'trend-live' (only when armed). Each leg has its own position, cooldown and loss streak."""
    def __init__(s,db,pp,notify,cfg,live=None):
        s.db,s.pp,s.n,s.cfg,s.live=db,pp,notify,cfg,live;s.b=(cfg.get("books") or {}).get("trend") or {};s.p=S.params(s.b.get("params"))
        s.ind=None
    def k(s,name,d=None,leg="trend"):return s.db.kv_get(f"{leg}:{name}",d)
    def ks(s,name,v,leg="trend"):s.db.kv_set(f"{leg}:{name}",v)
    def position(s):
        r=s.db.q("select id,tokens,cost,opened,meta from b_positions where book='trend' and status='open' limit 1")
        return dict(id=r[0][0],tokens=r[0][1],cost=r[0][2],opened=r[0][3],meta=json.loads(r[0][4] or "{}")) if r else None
    def plan_fn(s,px,meta):return S.plan(px,meta["stop"],s.p)
    def valid_fn(s,px,meta):
        sp=(px-meta["stop"])/px if px else 0;return s.p["min_stop"]<=sp<=s.p["max_stop"]
    def tick(s,now=None):
        now=now or time.time()
        if now>=getattr(s,"retry_at",0):
            try:lt=candles.sync(s.db,need_days=12,now=now)
            except Exception as e:log.warning("trend candles: %s (retry in 2 min)",e);s.retry_at=now+120;lt=candles.last_ts(s.db)
        else:lt=candles.last_ts(s.db)
        if not lt:return
        if s.ind is None or s.ind.ts[-1]!=lt:s.ind=S.Ind(candles.load(s.db,since=lt-12*86400),s.p)
        ind=s.ind;i=len(ind.ts)-1;px=jup_price(WSOL)[0] or ind.c[i]
        pos=s.position()
        if pos:s.manage(pos,ind,i,now,px,"trend")
        lpos=None
        if s.live:
            try:
                lpos=s.live.position()
                if lpos:s.manage(lpos,ind,i,now,px,"trend-live")
                s.live.process_approval(px,s.plan_fn,s.valid_fn)
            except Exception:log.exception("live leg (continuing)")
        if s.k("last_eval")==lt:return                       # decide once per closed candle
        s.ks("last_eval",lt)
        if lt+900<now-1800:return                           # candles are stale: never trade on old data
        sg=ind.signal(i);hold=max(s.k("cooldown_until",0),s.k("pause_until",0))
        s.ks("state",dict(t=lt+900,trend=ind.trend_ok(i) if ind.ready(i) else None,ready=ind.ready(i),price=ind.c[i],signal=bool(sg),blocked_until=hold if hold>now else None))
        if not sg:return
        meta=dict(stop=sg["stop"],signal_ref=sg["ref"],signal_ts=lt+900,version=S.VERSION,features=sg["features"])
        if not s.position():
            if now<hold:s.pp.signal("trend-rider",WSOL,"buy","refused","cooldown after an exit or a losing streak",None,sg["features"])
            else:s.enter_paper(sg,meta,lt)
        if s.live and not lpos and s.live.armed():
            lhold=max(s.k("cooldown_until",0,"trend-live"),s.k("pause_until",0,"trend-live"))
            if now<lhold:s.live.signal(WSOL,"refused","cooldown after an exit or a losing streak",None,sg["features"])
            else:
                try:
                    eq=s.live.equity();s.live.propose(S.size_usd(eq,sg["stop_pct"],s.p),meta,f"trend-live:{lt+900}",s.plan_fn)
                except Exception as e:log.exception("live propose");s.live.signal(WSOL,"refused",f"live error: {type(e).__name__}",None)
    def enter_paper(s,sg,meta,lt):
        eq=s.pp.equity(mark=False);size=S.size_usd(eq,sg["stop_pct"],s.p)
        pid=s.pp.on_buy("trend-rider",WSOL,None,None,f"trend:{lt+900}",{},meta=meta,size_usd=size)
        if pid:
            px=s.db.q("select price from b_trades where book='trend' and side='buy' and sig=?",(f"trend:{lt+900}",))[0][0]
            plan=S.plan(px,sg["stop"],s.p);s.db.x("update b_positions set meta=? where id=?",(json.dumps(dict(meta,**plan)),pid))
            s.n.send(term("trend --entry",[kv("mode",s.pp.label()),SEP,kv("entry",f"${px:.3f}"),kv("stop",f"${plan['stop']:.3f}"),kv("target",f"${plan['target']:.3f}"),
                kv("risk",f"{(px-plan['stop'])/px:.2%} of position"),kv("size",fusd(size)),SEP,"exits: stop, 2R target,","break-even at +1R, trend","break, 48h max"],("OK","📈 TREND RIDER entry")))
    def manage(s,pos,ind,i,now,px,leg):
        m=pos["meta"];p=s.p;live=leg=="trend-live"
        if "target" not in m:return
        if not m.get("be") and px>=m["be_at"]:
            m["stop"]=max(m["stop"],m["entry"]*(1+p["be_buffer"]));m["be"]=True
            s.db.x("update b_positions set meta=? where id=?",(json.dumps(m),pos["id"]))
            s.n.send(term("trend --breakeven",[kv("book","REAL MONEY" if live else "paper"),kv("price",f"${px:.3f}"),kv("stop",f"${m['stop']:.3f}"),SEP,"worst case now: about","break-even after fees"],("OK","🛡 stop moved to break-even")))
        why=None
        if px<=m["stop"]:why=("stop_loss","break-even stop" if m.get("be") else f"stop ${m['stop']:.3f} hit")
        elif px>=m["target"]:why=("take_profit",f"2R target ${m['target']:.3f} hit")
        else:
            j=ind.hidx[i]
            if j>=0 and ind.hts[j]>pos["opened"] and ind.trend_broken_at_hour(j) and s.k("last_break_check",None,leg)!=ind.hts[j]:
                s.ks("last_break_check",ind.hts[j],leg);why=("trend_break","hourly close below 200h average")
            elif now-pos["opened"]>=p["max_hold_h"]*3600:why=("max_hold",f"held {p['max_hold_h']}h max")
        if not why:return
        if live:s.live.exit(pos["id"],*why)
        else:s.pp.exit_position(pos["id"],*why)
        r=s.db.q("select status,realized from b_positions where id=?",(pos["id"],))[0]
        if r[0]=="closed":
            s.ks("cooldown_until",now+p["cooldown_h"]*3600,leg)
            streak=s.k("loss_streak",0,leg)+1 if r[1]<=0 else 0
            if streak>=p["loss_streak"]:s.ks("pause_until",now+p["streak_pause_h"]*3600,leg);streak=0
            s.ks("loss_streak",streak,leg)
    def status_rows(s):
        st=s.k("state") or {};pos=s.position();rows=[kv("version",S.VERSION),kv("book",s.pp.label())]
        rows+=[kv("SOL",f"${st.get('price',0):.2f}" if st else "–"),kv("trend","UP" if st.get("trend") else "not up" if st.get("ready") else "warming up"),
               kv("signal","yes" if st.get("signal") else "no")]
        if st.get("blocked_until"):rows.append(kv("pause",time.strftime("until %H:%M",time.localtime(st["blocked_until"]))))
        if pos and "target" in pos["meta"]:
            m=pos["meta"];rows+=[SEP,"open position:",kv("entry",f"${m['entry']:.3f}"),kv("stop",f"${m['stop']:.3f}"+(" (b/e)" if m.get("be") else "")),kv("target",f"${m['target']:.3f}")]
        else:rows+=[SEP,"no position (in USDC)"]
        if s.live:rows+=[SEP,kv("live","🔓 ARMED" if s.live.armed() else "🔒 off (/live)")]
        bt=s.db.kv_get("backtest:last")
        if bt and "checks" in bt:rows+=[SEP,kv("backtest","PASSED gate A" if bt["passed"] else "did NOT pass"),kv("ran",time.strftime("%d %b %H:%M",time.localtime(bt["ran"])))]
        else:rows+=[SEP,"backtest: not run yet","send /backtest to run it"]
        return rows
def backtest_report(bt):
    if bt.get("error"):return term("backtest",[bt["error"]],("ERR","backtest failed"))
    M=bt["metrics"];f=lambda v,d=2:"–" if v is None else f"{v:.{d}f}";pc=lambda v:"–" if v is None else f"{v:+.1%}"
    rows=[kv("version",bt["version"]),kv("data",f"{bt['candles']:,} candles"),kv("period",time.strftime("%b %Y",time.gmtime(bt["from_ts"]))+" to "+time.strftime("%b %Y",time.gmtime(bt["to_ts"]))),
          kv("held back",f"from {time.strftime('%d %b %Y',time.gmtime(bt['cut_ts']))}"),SEP,"        train  heldback all"]
    for name,key,fmt in(("trades","trades",lambda v:f"{v}"),("win",'win_rate',lambda v:"–" if v is None else f"{v:.0%}"),("PF","pf",f),("return","ret",pc),("max DD","max_dd",lambda v:f"{v:.1%}")):
        rows.append(f"{name:<7} "+" ".join(f"{fmt(M[p][key]):>7}" for p in("train","holdout","all")))
    rows+=[kv("PF x2 cost",f(M["double_cost"]["pf"]),10)]
    a=M["all"]
    if a.get("dd_switch_at"):rows.append("6% drawdown switch would trip "+time.strftime("%d %b %y",time.gmtime(a["dd_switch_at"])))
    if a.get("limit_blocked"):rows.append(f"{a['limit_blocked']} entries blocked by loss limits")
    rows+=[SEP,"gate A:"]
    for c in bt["checks"]:
        v=c["value"];vs="–" if v is None else (f"{v:.0%}" if isinstance(v,float) and abs(v)<5 and c["name"]!="PF, costs x2" else f"{v:.2f}" if isinstance(v,float) else str(v))
        rows.append(f"{'PASS' if c['ok'] else 'FAIL'} {c['name']} {c['need']}");rows.append(f"     got {vs}")
    rows+=[SEP,"no real money unless every","check passes AND you approve"]
    return term("backtest --trend-rider",rows,("OK","✅ gate A passed") if bt["passed"] else ("WARN","gate A NOT passed"))
_bt_lock=threading.Lock()
def run_backtest_async(db_path,notify,cfg):
    """/backtest: fetch ~2 years of candles (first run takes a few minutes), run, store, report. One at a time."""
    if not _bt_lock.acquire(blocking=False):return False
    def job():
        from db import DB;from notify import Notifier
        try:
            d=DB(db_path);notify_=Notifier(d,bool(getattr(notify,"token","")));days=((cfg.get("books") or {}).get("trend") or {}).get("backtest_days",730)
            n=candles.backfill(d,days);c=candles.load(d,since=int(time.time())-days*86400)
            bt=BT.full(c,((cfg.get("books") or {}).get("trend") or {}).get("params"),cost=((cfg.get("books") or {}).get("trend") or {}).get("backtest_cost",0.0015))
            seen=d.kv_get("backtest:holdout_views",{});seen[S.VERSION]=seen.get(S.VERSION,0)+1;d.kv_set("backtest:holdout_views",seen)
            bt["holdout_views"]=seen[S.VERSION];d.kv_set("backtest:last",bt);notify_.send(backtest_report(bt))
        except Exception as e:
            log.exception("backtest failed");notify.send(term("backtest",[f"{type(e).__name__}: {str(e)[:120]}"],("ERR","backtest failed")))
        finally:_bt_lock.release()
    threading.Thread(target=job,daemon=True).start();return True
