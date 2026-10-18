#!/usr/bin/env python3
"""Read-only dashboard for the paper copy bot.
Never imports execution. Opens bot.db read-only. Standard library only (runs on the system python3)."""
import os,re,json,time,sqlite3,statistics as st,threading,urllib.request,hmac
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
HERE=os.path.dirname(os.path.abspath(__file__));APP=os.path.normpath(os.path.join(HERE,".."))
DB=os.environ.get("COPYBOT_DB") or os.path.join(APP,"bot.db")
HEARTBEAT=os.environ.get("COPYBOT_HEARTBEAT") or os.path.join(APP,"heartbeat")
TOKEN=open(os.path.join(HERE,"token.txt")).read().strip();PORT=int(os.environ.get("DASH_PORT","8787"))
HTML_PATH=os.path.join(HERE,"index.html");ROOM_JS=os.path.join(HERE,"room.js")

# ---- config (plain-text read, so the dashboard needs no third-party packages) ----
def _cfg_num(text,key,default):
    m=re.search(r"^\s*"+re.escape(key)+r"\s*:\s*([0-9.]+)",text,re.M)
    try:return float(m.group(1)) if m else default
    except ValueError:return default
def load_cfg():
    try:t=open(os.path.join(APP,"config.yaml")).read()
    except Exception:t=""
    m=re.search(r"^exits:[^\n]*\n((?:[ \t]+.*\n?|[ \t]*\n)*)",t,re.M);ex=m.group(1) if m else ""
    on=re.search(r"^\s*enabled\s*:\s*(\S+)",ex,re.M)
    exits=dict(enabled=bool(ex) and (on.group(1).lower() in("true","yes","on","1") if on else False),
        sl=_cfg_num(ex,"stop_loss_pct",0),tp=_cfg_num(ex,"take_profit_pct",0),max_hold=_cfg_num(ex,"max_hold_hours",0))
    return dict(exits=exits,start=_cfg_num(t,"start_equity_usd",52.0),max_dd=_cfg_num(t,"max_drawdown_pct",0.40),
        daily=_cfg_num(t,"daily_loss_limit_pct",0.15),max_pos=int(_cfg_num(t,"max_open_positions",6)),
        fee=_cfg_num(t,"fee_pct",0.02),min_liq=_cfg_num(t,"min_liquidity_usd",50000))
def _env_key(name):
    try:
        for line in open(os.path.join(APP,".env")):
            if line.startswith(name+"="):return line.split("=",1)[1].strip()
    except Exception:pass
    return os.environ.get(name,"").strip()

# Go-live readiness: every check must pass before even a small real-money test is worth discussing.
READY=[("days","paper days","≥ 14",lambda v:v>=14),
       ("trips","closed round trips","≥ 30",lambda v:v>=30),
       ("net","net profit after fees","> $0",lambda v:v>0),
       ("pf","profit factor","≥ 1.3",lambda v:v is not None and v>=1.3),
       ("dd","max drawdown","≤ 20%",lambda v:v<=0.20),
       ("top","best trip share of profit","≤ 40%",lambda v:v is not None and v<=0.40),
       ("fail","fill failure rate","≤ 10%",lambda v:v is not None and v<=0.10),
       ("slip","median slip vs trader","≤ 150 bps",lambda v:v is not None and v<=150)]

_pc={};_jk=_env_key("JUPITER_API_KEY")
def prices(mints):
    need=[m for m in mints if time.time()-_pc.get(m,(0,0))[0]>30]
    h={"user-agent":"copybot-dash"}
    if _jk:h["x-api-key"]=_jk
    for i in range(0,len(need),50):
        try:
            r=json.load(urllib.request.urlopen(urllib.request.Request("https://api.jup.ag/price/v3?ids="+",".join(need[i:i+50]),headers=h),timeout=8))
            for m in need[i:i+50]:_pc[m]=(time.time(),(r.get(m) or {}).get("usdPrice"))
        except Exception:pass
    return {m:_pc.get(m,(0,None))[1] for m in mints}

def skip_kind(reason):
    r=(reason or "").lower()
    for k,words in (("safety veto",("safety veto",)),("exit check (round trip)",("sell check",)),("stale signal",("stale signal",)),("market storm",("storm",)),
                    ("panic / paused / live off",("panic","paused","switched off","live is off")),("late / chasing",("late:","moved past")),("too small / costs",("too small","costs $","below $")),("low liquidity",("liquidity",)),("price impact",("impact",)),("no route / quote",("quote","route")),
                    ("max positions",("max open",)),("already holding",("already holding",)),("daily loss limit",("daily loss",)),
                    ("drawdown shutdown",("drawdown",)),("too small / no cash",("below minimum","no cash"))):
        if any(w in r for w in words):return k
    return "other"

EXIT_KIND={None:"trader","trader_sold":"trader","stop_loss":"stop-loss","take_profit":"take-profit","max_hold":"time limit","write_off":"written off",
           "trend_break":"trend exit","panic":"panic","strategy_replaced":"v1 closed"}
def _med(x):return st.median(x) if x else None
def _mean(x):return st.mean(x) if x else None
def downsample(pts,n=1500):
    if len(pts)<=n:return pts
    step=len(pts)/n;out=[pts[int(i*step)] for i in range(n)]
    return out+[pts[-1]] if out[-1]!=pts[-1] else out

BOOKS=("copy","consensus","trend","trend-live")
BOOK_KEYS=("cash","peak","last_equity","shutdown","day_start","pending_sells","paused","limits","started")
def books_summary(q,now):
    out=[]
    for b in BOOKS:
        r=q("select v from kv where k=?",(b+":cash",))
        if not r:continue
        g=lambda k,d=None:(lambda x:json.loads(x[0][0]) if x else d)(q("select v from kv where k=?",(f"{b}:{k}",)))
        lim=g("limits",{}) or {};cash=json.loads(r[0][0]) or 0;eq=g("last_equity",cash) or cash;start=lim.get("start") or 0
        op=q("select count(*) from b_positions where book=? and status='open'",(b,))[0][0]
        state="off" if lim.get("enabled") is False else "paused" if g("paused") else "shutdown" if g("shutdown") else "ok"
        out.append(dict(book=b,equity=eq,start=start,pnl=eq-start,open=op,state=state,live=b.endswith("-live")))
    return out
def state(book="copy"):
    C=load_cfg();now=time.time()
    c=sqlite3.connect(f"file:{DB}?mode=ro",uri=True,timeout=5)
    try:
        q=lambda s,a=():c.execute(s,a).fetchall()
        tables={r[0] for r in q("select name from sqlite_master where type='table'")}
        if "b_positions" not in tables:raise RuntimeError("bot has not started the books version yet; send /update")
        names=[f"{book}:{k}" for k in BOOK_KEYS]+["selected","selected_at","last_poll","ws","panic"]
        kv={(k.split(":",1)[1] if k.startswith(book+":") else k):json.loads(v) for k,v in q(f"select k,v from kv where k in ({','.join('?'*len(names))})",names)}
        lim=kv.get("limits") or {};START=lim.get("start") or C["start"]
        C=dict(C,start=START,max_pos=lim.get("max_pos",C["max_pos"]),daily=lim.get("daily",C["daily"]),max_dd=lim.get("max_dd",C["max_dd"]))
        cash=kv.get("cash",START)
        # ---- open positions (marked live) ----
        pos=q("select trader,mint,tokens,cost,opened,open_sig from b_positions where book=? and status='open'",(book,));px=prices([p[1] for p in pos])
        P=[];unrl=0
        for tr,m,tok,cost,op,osig in pos:
            cur=px.get(m);val=tok*cur if cur else None;u=(val-cost) if val is not None else None;unrl+=u or 0
            ent=q("select price from b_trades where book=? and side='buy' and sig=?",(book,osig)) or q("select price from b_trades where book=? and side='buy' and trader=? and mint=? order by ts desc limit 1",(book,tr,m))
            P.append(dict(trader=tr,mint=m,tokens=tok,cost=cost,entry=ent[0][0] if ent else None,price=cur,value=val,upnl=u,
                upct=(u/cost if u is not None and cost else None),opened=op,age=now-op if op else None))
        equity=cash+sum((p["value"] if p["value"] is not None else p["cost"]) for p in P)
        # ---- equity curve + drawdown ----
        eq=[(t,e) for t,e in q("select ts,equity from b_equity where book=? order by ts",(book,))]
        pk=START;mdd=0
        for _,e in eq:pk=max(pk,e);mdd=max(mdd,(pk-e)/pk if pk else 0)
        peak=max(kv.get("peak",START),pk);cur_dd=max(0,(peak-equity)/peak) if peak else 0
        first_ts=eq[0][0] if eq else (q("select min(ts) from b_trades where book=?",(book,))[0][0])
        # ---- fills (execution quality) ----
        has_fills=True
        fills=q("select sig,trader,mint,side,status,reason,trader_px,our_px,slip_bps,detect_ms,total_ms,size_usd,fee_usd,fill_ts from b_fills where book=?",(book,))
        F=[f for f in fills if f[4] in("filled","failed")];FL=[f for f in F if f[4]=="filled"]
        det=[f[9] for f in fills if f[9] is not None];tot=[f[10] for f in FL if f[10] is not None];sl=[f[8] for f in FL if f[8] is not None]
        ex=dict(n_fills=len(FL),n_failed=len(F)-len(FL),fail_rate=(len(F)-len(FL))/len(F) if F else None,
            det_med_ms=_med(det),det_avg_ms=_mean(det),total_med_ms=_med(tot),slip_avg_bps=_mean(sl),slip_med_bps=_med(sl))
        fee_total=sum(f[12] or 0 for f in FL)
        buy_fill={f[0]:f for f in FL if f[3]=="buy"}
        # ---- closed round trips (one per position, partial sells merged) ----
        trips=[]
        for pid,tr,m,op,cl,real,osig in q("select id,trader,mint,opened,closed,realized,open_sig from b_positions where book=? and status='closed' order by closed desc",(book,)):
            b=q("select usd from b_trades where book=? and side='buy' and sig=?",(book,osig));cost=b[0][0] if b else None
            bf=buy_fill.get(osig);sells=[f for f in FL if f[3]=="sell" and f[1]==tr and f[2]==m and op and (f[13] or 0)>=op and (f[13] or 0)<=(cl or now)+5]
            gap=tret=oret=None
            if bf and sells and bf[6] and bf[7]:
                w=sum(f[11] or 0 for f in sells)
                if w>0 and all(f[6] for f in sells):
                    tsell=sum((f[6])*(f[11] or 0) for f in sells)/w;osell=sum((f[7] or 0)*(f[11] or 0) for f in sells)/w
                    tret=tsell/bf[6]-1;oret=osell/bf[7]-1;gap=tret-((real or 0)/cost if cost else oret)
            xr=q("select reason from b_fills where book=? and side='sell' and status in('filled','writeoff') and trader=? and mint=? and fill_ts>=? and fill_ts<=? order by fill_ts desc limit 1",
                 (book,tr,m,op or 0,(cl or now)+5))
            exit_kind=EXIT_KIND.get(xr[0][0] if xr else None,"trader")
            trips.append(dict(trader=tr,mint=m,opened=op,closed=cl,hold=(cl-op) if cl and op else None,cost=cost,pnl=real or 0,
                pct=(real or 0)/cost if cost else None,trader_ret=tret,our_ret=oret,gap=gap,exit=exit_kind))
        wins=[t["pnl"] for t in trips if t["pnl"]>0];losses=[t["pnl"] for t in trips if t["pnl"]<=0]
        gw=sum(wins);gl=-sum(losses);pf=(gw/gl if gl else (None if not gw else 99.0))
        realized=sum(t["pnl"] for t in trips)
        byexit={}
        for t in trips:
            b=byexit.setdefault(t["exit"],{"n":0,"pnl":0.0,"wins":0});b["n"]+=1;b["pnl"]+=t["pnl"];b["wins"]+=t["pnl"]>0
        exits_by=sorted(([k,v["n"],v["pnl"],v["wins"]] for k,v in byexit.items()),key=lambda x:-x[1])
        pend=kv.get("pending_sells") or {}
        gaps=[t["gap"] for t in trips if t["gap"] is not None]
        perf=dict(trips=len(trips),wins=len(wins),losses=len(losses),win_rate=len(wins)/len(trips) if trips else None,pf=pf,
            avg_win=_mean(wins),avg_loss=_mean(losses),expectancy=realized/len(trips) if trips else None,realized=realized,
            fees=fee_total,best=max(wins) if wins else None,worst=min(losses) if losses else None,
            gap_med=_med(gaps),hold_med=_med([t["hold"] for t in trips if t["hold"]]),top_share=(max(wins)/gw if gw else None))
        # ---- today vs daily loss limit ----
        ds=kv.get("day_start") or {};today_start=ds.get("eq")
        today=dict(start=today_start,pnl=(kv.get("last_equity",equity)-today_start) if today_start else None,limit=C["daily"])
        today["used"]=max(0,-today["pnl"]/(today_start*C["daily"])) if today_start and today["pnl"] is not None else 0
        # ---- traders ----
        ts=q("select max(ts) from decisions")[0][0]
        dec={t:(json.loads(mm or "{}"),r) for t,mm,r in q("select trader,metrics,reason from decisions where ts=? and selected=1",(ts,))} if ts else {}
        sel=(kv.get("selected") or []) if book=="copy" else [];T=[]
        for s in sel:
            m=dec.get(s["handle"],({},""))[0];mine=[t for t in trips if t["trader"]==s["handle"]]
            sl_t=[f[8] for f in FL if f[1]==s["handle"] and f[8] is not None];g_t=[t["gap"] for t in mine if t["gap"] is not None]
            T.append(dict(source=s.get("source","fomo"),handle=s["handle"],wallet=s["wallet"],pnl30=m.get("pnl"),win=m.get("win"),pf=m.get("pf"),
                trades=m.get("trades"),our_pnl=sum(t["pnl"] for t in mine),our_trips=len(mine),our_win=(sum(t["pnl"]>0 for t in mine)/len(mine)) if mine else None,
                our_slip=_mean(sl_t),our_gap=_med(g_t),open=sum(p["trader"]==s["handle"] for p in P)))
        # ---- skip reasons ----
        sk={}
        for (reason,) in q("select reason from b_signals where book=? and decision='refused'",(book,)):
            k=skip_kind(reason);sk[k]=sk.get(k,0)+1
        skips=sorted(sk.items(),key=lambda x:-x[1])
        # ---- activity log ----
        log=[]
        if book=="copy":
            for t,tr,r in q("select ts,trader,reason from decisions where selected=1 order by ts desc limit 10"):log.append(dict(ts=t,kind="select",text=f"PICKED {tr}: {r}"))
        for t,tr,m,side,usd,pnl,note in q("select ts,trader,mint,side,usd,pnl,note from b_trades where book=? order by ts desc limit 80",(book,)):
            txt={"buy":f"BUY {m[:6]}… ${usd:.2f} copying {tr}","sell":f"SELL {m[:6]}… ${usd:.2f} PnL {pnl:+.2f} ({tr})","skip":f"SKIP {m[:6]}… from {tr}: {note}"}.get(side,f"{side} {m[:6]}…")
            log.append(dict(ts=t,kind=side if side in("buy","sell","skip") else "info",text=txt,mint=m))
        for t,tr,m,r in q("select fill_ts,trader,mint,reason from b_fills where book=? and status='failed' order by ts desc limit 15",(book,)):
            log.append(dict(ts=t or now,kind="guard",text=f"FILL FAILED {m[:6]}… ({tr}): {r}",mint=m))
        if kv.get("shutdown"):log.append(dict(ts=now,kind="guard",text="SAFEGUARD: drawdown auto-shutdown active"))
        if kv.get("panic"):log.append(dict(ts=now,kind="guard",text="PANIC STOP active: no new buys in any book"))
        log.sort(key=lambda x:-x["ts"]);books=books_summary(q,now)
        bt=None;realmoney=None
        if book.startswith("trend"):
            r=q("select v from kv where k='backtest:v2'") or q("select v from kv where k='backtest:last'")
            if r:
                b=json.loads(r[0][0]) or {}
                if b.get("version")=="trend-rider v2":
                    bt={k:b.get(k) for k in("version","ran","from_ts","to_ts","cut_ts","candles","checks","passed","coins","pooled","notes","cost","cost_source","holdout_views","error")}
                else:bt=dict(retired=b.get("version") or "v1")
            r=q("select v from kv where k='trend:state'");bt=dict(bt or {},live_state=json.loads(r[0][0]) if r else None)
            r=q("select v from kv where k='live:readiness'");realmoney=json.loads(r[0][0]) if r else None
    finally:c.close()
    # ---- readiness ----
    days=(now-first_ts)/86400 if first_ts else 0
    vals=dict(days=days,trips=perf["trips"],net=equity-START,pf=perf["pf"],dd=mdd,top=perf["top_share"],fail=ex["fail_rate"],slip=ex["slip_med_bps"])
    checks=[dict(key=k,label=l,need=n,value=vals[k],ok=bool(f(vals[k]))) for k,l,n,f in READY]
    try:hb=now-os.path.getmtime(HEARTBEAT)
    except OSError:hb=None
    lp=kv.get("last_poll")
    return dict(now=now,cfg=C,start=START,cash=cash,equity=equity,peak=peak,pnl=equity-START,unrealized=unrl,maxdd=mdd,dd=cur_dd,
        perf=perf,today=today,exits_by=exits_by,pending_sells=len(pend),equity_curve=downsample(eq),positions=P,traders=T,trips=trips[:60],skips=skips,log=log[:80],
        exec=ex,ws=kv.get("ws") or {},shutdown=bool(kv.get("shutdown")),last_poll=lp,heartbeat_age=hb,
        live=bool(lp) and now-lp<180,ready=dict(checks=checks,passed=sum(x["ok"] for x in checks),total=len(checks),days=days),
        selected_at=kv.get("selected_at"),book=book,backtest=bt,realmoney=realmoney,books=books,panic=bool(kv.get("panic")),paused=bool(kv.get("paused")))

STATE_DIR=os.environ.get("CB_STATE","/var/lib/copybot")
def activity(since=0):
    """Recent bot events + current 'work' context for the live scene. Read-only. Wallets are public addresses."""
    now=time.time();c=sqlite3.connect(f"file:{DB}?mode=ro",uri=True,timeout=5)
    try:
        q=lambda s,a=():c.execute(s,a).fetchall()
        kvg=lambda k,d=None:(lambda r:json.loads(r[0][0]) if r else d)(q("select v from kv where k=?",(k,)))
        tables={r[0] for r in q("select name from sqlite_master where type='table'")}
        ev=[]
        if "activity" in tables:
            rows=q("select id,ts,kind,data from activity where id>? order by id desc limit 60",(int(since or 0),))
            for i,t,k,d in reversed(rows):
                try:x=json.loads(d or "{}")
                except Exception:x={}
                ev.append(dict(x,id=i,ts=t,kind=k))
        sel=kvg("selected",[]) or [];last={}
        if "activity" in tables:
            for t,d in q("select ts,data from activity where kind='tx' order by id desc limit 200"):
                try:h=json.loads(d).get("trader")
                except Exception:h=None
                if h and h not in last:last[h]=t
        watch=[dict(handle=x.get("handle"),wallet=x.get("wallet"),source=x.get("source"),last_tx=last.get(x.get("handle"))) for x in sel]
        cards=[]
        ts=q("select max(ts) from decisions")[0][0]
        if ts:
            for tr,ok,m in q("select trader,selected,metrics from decisions where ts=? order by selected desc limit 400",(ts,)):
                try:m=json.loads(m or "{}")
                except Exception:m={}
                if m.get("pnl") is not None:cards.append(dict(handle=tr,picked=bool(ok),pnl=m.get("pnl"),pf=m.get("pf"),win=m.get("win")))
            cards=sorted(cards,key=lambda x:(-x["picked"],-(x["pnl"] or 0)))[:8]
        ws=kvg("ws",{}) or {};lp=kvg("last_poll")
    finally:c.close()
    try:hb=now-os.path.getmtime(HEARTBEAT)
    except OSError:hb=None
    try:u=json.load(open(os.path.join(STATE_DIR,"update_status.json")));upd={k:u.get(k) for k in("result","version","from","ts")}
    except Exception:upd=None
    return dict(now=now,events=ev,watch=watch,cards=cards,ws=dict(connected=bool(ws.get("connected")),last_msg=ws.get("last_msg"),reconnects=ws.get("reconnects")),
        last_poll=lp,heartbeat_age=hb,update=upd)

_cache={};_lock=threading.Lock()
def state_json(book="copy"):
    if book not in BOOKS:book="copy"
    with _lock:
        t,body=_cache.get(book,(0,None))
        if time.time()-t>3 or body is None:
            body=json.dumps(state(book),separators=(",",":"),allow_nan=False,default=str).encode();_cache[book]=(time.time(),body)
        return body

# ---- bot room: what every part of the bot is doing right now, for the animated room (all books, read-only) ----
# Each buy proposal walks the real gate order of paper.Paper.on_buy; the refusal reason tells where it stopped.
STATIONS=("guard","window","vet","trader","exit","vault")
def room_station(reason,source=None):
    r=(reason or "").lower()
    if r.startswith("passed"):return "vault"
    if source=="trend-rider" and any(w in r for w in("late:","moved past","below $","costs $","approval","rejected","moved too far","live error")):return "chart"
    for st,words in(("guard",("panic","switched off","paused","drawdown","daily loss","weekly loss","stale signal","already holding","max open","below minimum","no cash")),
                    ("window",("storm",)),("vet",("liquidity","safety veto")),("trader",("no jupiter route","price impact")),("exit",("sell check",))):
        if any(w in r for w in words):return st
    return "guard"
WSOL="So11111111111111111111111111111111111111112";_sol={"t":0,"px":None,"ch":None}
def sol_now():
    """SOL price and 24 h change (Jupiter price API, percent), refreshed at most once a minute. (None, None) on failure."""
    if time.time()-_sol["t"]>60:
        _sol["t"]=time.time();h={"user-agent":"copybot-dash"}
        if _jk:h["x-api-key"]=_jk
        try:
            r=json.load(urllib.request.urlopen(urllib.request.Request("https://api.jup.ag/price/v3?ids="+WSOL,headers=h),timeout=8)).get(WSOL) or {}
            _sol.update(px=float(r["usdPrice"]) if r.get("usdPrice") else None,ch=float(r["priceChange24h"]) if r.get("priceChange24h") is not None else None)
        except Exception:pass
    return _sol["px"],_sol["ch"]
def room_cfg():
    try:t=open(os.path.join(APP,"config.yaml")).read()
    except Exception:t=""
    return dict(top10=_cfg_num(t,"max_top10_share",0.30),roundtrip=_cfg_num(t,"max_roundtrip_loss_pct",0.06),age=_cfg_num(t,"max_signal_age_s",120),
        liq=_cfg_num(t,"min_liquidity_usd",50000),impact=_cfg_num(t,"max_price_impact_pct",0.03),reselect_h=_cfg_num(t,"reevaluate_hours",24),
        exit_s=_cfg_num(t,"check_seconds",30),poll_s=_cfg_num(t,"fallback_poll_seconds",120))
def _jl(v,d=None):
    try:return json.loads(v) if v is not None else d
    except Exception:return d
def room_state():
    now=time.time();C=load_cfg();R=room_cfg()
    c=sqlite3.connect(f"file:{DB}?mode=ro",uri=True,timeout=5)
    try:
        q=lambda s,a=():c.execute(s,a).fetchall()
        tables={r[0] for r in q("select name from sqlite_master where type='table'")}
        if "b_signals" not in tables:raise RuntimeError("bot has not started the books version yet; send /update")
        # only these kv keys are read: never codes, chat ids or anything secret
        keys=["panic","storm_until","last_poll","ws","selected","selected_at","trend:state","live:readiness","live:armed","boot_id","live:mismatch",
              "trend-live:shutdown","live:approval"]+[f"{b}:{k}" for b in BOOKS for k in("pending_sells","day_start","limits")]
        K={k:_jl(v) for k,v in q(f"select k,v from kv where k in ({','.join('?'*len(keys))})",keys)}
        day0=now//86400*86400
        # ---- buy proposals (b_signals) with their fill record (b_fills, same book+coin, nearest time) ----
        sigs=q("select id,ts,book,source,mint,decision,reason,size_usd,features from b_signals order by id desc limit 40")
        fills={}
        for b in {r[2] for r in sigs}:
            t0=min(r[1] for r in sigs if r[2]==b)-120
            for f in q("select ts,mint,status,reason,detect_ms,total_ms,impact,slip_bps,our_px,size_usd,fee_usd from b_fills where book=? and side='buy' and ts>=?",(b,t0)):
                fills.setdefault((b,f[1]),[]).append(f)
        ev=[]
        for i,ts,b,src,m,dec,why,size,feat in sigs:
            near=min(fills.get((b,m),[]),key=lambda f:abs(f[0]-ts),default=None)
            f=near if near and abs(near[0]-ts)<=90 else None
            ev.append(dict(id=f"s{i}",kind="buy",ts=ts,book=b,source=src,mint=m,decision=dec,reason=why,size=size,features=_jl(feat,{}) or {},
                station=room_station(why,src) if dec!="filled" else "vault",
                fill=dict(status=f[2],detect_ms=f[4],total_ms=f[5],impact=f[6],slip_bps=f[7],price=f[8],usd=f[9],fee=f[10]) if f else None))
        # ---- sells (b_trades) with why they happened (b_fills reason) ----
        for i,ts,b,tr,m,u,pnl,sig,note in q("select id,ts,book,trader,mint,usd,pnl,sig,note from b_trades where side='sell' order by id desc limit 20"):
            x=q("select reason from b_fills where book=? and sig=? and side='sell' and status in('filled','writeoff') order by id desc limit 1",(b,sig))
            op=q("select opened from b_positions where book=? and mint=? and closed between ? and ? order by closed desc limit 1",(b,m,ts-5,ts+5))
            basis=(u or 0)-(pnl or 0)
            ev.append(dict(id=f"t{i}",kind="sell",ts=ts,book=b,source=tr,mint=m,usd=u,pnl=pnl,pct=(pnl/basis if basis>0 else None),note=note,
                exit=(x[0][0] if x else "trader_sold") or "trader_sold",held=(ts-op[0][0]) if op and op[0][0] else None))
        for i,ts,b,tr,m,why in q("select id,coalesce(fill_ts,ts),book,trader,mint,reason from b_fills where side='sell' and status='failed' order by id desc limit 10"):
            ev.append(dict(id=f"f{i}",kind="sell_failed",ts=ts,book=b,source=tr,mint=m,reason=why))
        # the activity stream (activity.py): every trader transaction seen, selection start, stream up/down, errors
        if "activity" in tables:
            for i,ts,k,dat in q("select id,ts,kind,data from activity where kind in('tx','select_start','ws','error') order by id desc limit 30"):
                x=_jl(dat,{}) or {}
                ev.append(dict({k2:x.get(k2) for k2 in("trader","side","mint","usd","detect_s","up","reconnects","what","msg")},id=f"a{i}",kind="act_"+k,ts=ts,book="all"))
        ev.sort(key=lambda e:(e["ts"] or 0,e["id"]));ev=ev[-50:]
        # ---- today, per station (what each part of the gate did since 00:00 UTC) ----
        today=dict(proposals=0,filled=0,refused=0,by_station={},vet_checked=0,vet_reasons={})
        for ts,b,src,dec,why in q("select ts,book,source,decision,reason from b_signals where ts>=?",(day0,)):
            today["proposals"]+=1;st="vault" if dec=="filled" else room_station(why,src)
            if dec=="filled":today["filled"]+=1
            else:today["refused"]+=1;today["by_station"][st]=today["by_station"].get(st,0)+1
            if st in STATIONS and STATIONS.index(st)>=STATIONS.index("vet"):today["vet_checked"]+=1
            if st=="vet" and dec!="filled":
                k=skip_kind(why);today["vet_reasons"][k]=today["vet_reasons"].get(k,0)+1
        # ---- open positions, priced now, with their exits ----
        pos=q("select id,book,trader,mint,tokens,cost,opened,meta from b_positions where status='open' order by opened")
        px=prices([p[3] for p in pos]) if pos else {};P=[];E=C["exits"]
        for pid,b,tr,m,tok,cost,op,meta in pos:
            cur=px.get(m);val=tok*cur if cur else None;mt=_jl(meta,{}) or {}
            P.append(dict(id=pid,book=b,trader=tr,mint=m,cost=cost,value=val,ch=(val/cost-1) if val is not None and cost else None,age=now-op if op else None,
                stop=mt.get("stop"),entry=mt.get("entry"),price=cur,sl=None if b.startswith("trend") else (E["sl"] if E["enabled"] else None),
                tp=None if b.startswith("trend") else (E["tp"] if E["enabled"] else None),max_hold=None if b.startswith("trend") else (E["max_hold"] if E["enabled"] else None)))
        pend=[]
        for b in BOOKS:
            for pid,p in (K.get(f"{b}:pending_sells") or {}).items():
                r=q("select mint from b_positions where id=?",(int(pid),)) if str(pid).isdigit() else []
                pend.append(dict(book=b,mint=r[0][0] if r else None,tries=p.get("tries",0),next_in=max(0,(p.get("next") or now)-now),why=p.get("note") or p.get("reason")))
        # ---- bookkeeping, trader picks, Trend Rider, real-money lock ----
        lm=q("select max(ts) from b_equity")[0][0]
        sel_at=q("select max(ts) from decisions")[0][0]
        sel=dict(at=sel_at,next_at=(sel_at+R["reselect_h"]*3600) if sel_at else None,picked=[s.get("handle") for s in (K.get("selected") or [])],checked=0,qualified=0)
        if sel_at:
            n,ok=q("select count(*),sum(case when reason like 'PASS%' then 1 else 0 end) from decisions where ts=?",(sel_at,))[0];sel.update(checked=n,qualified=ok or 0)
        closes=[[t,cl] for t,cl in reversed(q("select ts,c from candles where sym='SOL-USD' and tf=86400 order by ts desc limit 60"))] if "candles" in tables else []
        a=K.get("live:armed") or {}
        armed=bool(a) and a.get("boot")==K.get("boot_id") and not K.get("panic") and not K.get("trend-live:shutdown") and not K.get("live:mismatch") and now<(a.get("until") or 0)
        rd=K.get("live:readiness") or {};ap=K.get("live:approval") or {}
        live=dict(armed=armed,mode=a.get("mode") if armed else None,until=a.get("until") if armed else None,
            safety_ok=sum(1 for x in rd.get("safety") or [] if x and x[0]),safety_total=len(rd.get("safety") or []),checked=rd.get("ts"),
            approval=dict(size=ap.get("size"),exp=ap.get("exp")) if ap.get("status")=="pending" else None,mismatch=bool(K.get("live:mismatch")))
        books=books_summary(q,now);today_s=time.strftime("%Y-%m-%d",time.gmtime(now))
        for b in books:
            ds=K.get(f"{b['book']}:day_start") or {};lim=K.get(f"{b['book']}:limits") or {}
            b["day_pnl"]=(b["equity"]-ds["eq"]) if ds.get("eq") and ds.get("day")==today_s else None;b["daily_limit"]=lim.get("daily")
        today["sold"]=q("select count(*) from b_trades where side='sell' and ts>=?",(day0,))[0][0]
    finally:c.close()
    try:hb=now-os.path.getmtime(HEARTBEAT)
    except OSError:hb=None
    lp=K.get("last_poll");ws=K.get("ws") or {};sp,sch=sol_now();sa=K.get("selected_at")
    # alive = heartbeat (a separate thread, every 30 s). The main loop can be busy for minutes while it re-selects
    # traders; that is shown as busy, not offline.
    return dict(now=now,mode="paper",online=hb is not None and hb<300,last_poll=lp,heartbeat_age=hb,loop_age=(now-lp) if lp else None,
        selection_due=bool(sa) and now-sa>R["reselect_h"]*3600,
        ws=dict(connected=bool(ws.get("connected")),notifications=ws.get("notifications"),reconnects=ws.get("reconnects"),last_msg=ws.get("last_msg")),
        panic=bool(K.get("panic")),storm_until=K.get("storm_until") or 0,sol=dict(price=sp,ch24=sch),books=books,positions=P,pending_sells=pend,
        events=ev,today=today,marks=dict(last=lm,every=300),selection=sel,trend=dict(state=K.get("trend:state"),closes=closes),live=live,
        ct=dict(connected=False,note="CT/X feed not built yet (roadmap phase 5)"),
        cfg=dict(exits=C["exits"],top10=R["top10"],roundtrip=R["roundtrip"],max_age=R["age"],min_liq=R["liq"],max_impact=R["impact"],exit_check_s=R["exit_s"],poll_s=R["poll_s"]))
def _fin(o):
    """NaN/inf (possible inside stored feature JSON) -> None, so the JSON stays valid for browsers."""
    if isinstance(o,float):return o if o==o and abs(o)!=float("inf") else None
    if isinstance(o,dict):return {k:_fin(v) for k,v in o.items()}
    if isinstance(o,(list,tuple)):return [_fin(v) for v in o]
    return o
def room_json():
    with _lock:
        t,body=_cache.get("_room",(0,None))
        if time.time()-t>3 or body is None:
            body=json.dumps(_fin(room_state()),separators=(",",":"),allow_nan=False,default=str).encode();_cache["_room"]=(time.time(),body)
        return body

CSP=("default-src 'self'; script-src 'self' 'unsafe-inline' https://telegram.org; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
     "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
     "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org")
class H(BaseHTTPRequestHandler):
    def log_message(s,*a):pass
    def _authed(s,u):
        t=(parse_qs(u.query).get("t") or [""])[0] or s.headers.get("x-token","")
        return bool(t) and hmac.compare_digest(t.encode(),TOKEN.encode())
    def do_GET(s):
        u=urlparse(s.path)
        if u.path=="/":  # the page holds no data; it asks for the token-protected API
            try:return s._send(200,"text/html; charset=utf-8",open(HTML_PATH,"rb").read())
            except OSError:return s._send(500,"text/plain",b"index.html missing")
        if u.path=="/room.js":  # code only, no data (like the page itself)
            try:return s._send(200,"text/javascript; charset=utf-8",open(ROOM_JS,"rb").read())
            except OSError:return s._send(404,"text/plain",b"room.js missing")
        if u.path=="/api/state":
            if not s._authed(u):return s._send(403,"application/json",b'{"error":"forbidden"}')
            try:return s._send(200,"application/json",state_json((parse_qs(u.query).get("book") or ["copy"])[0]))
            except Exception as e:return s._send(500,"application/json",json.dumps({"error":type(e).__name__+": "+str(e)[:200]}).encode())
        if u.path=="/api/room":
            if not s._authed(u):return s._send(403,"application/json",b'{"error":"forbidden"}')
            try:return s._send(200,"application/json",room_json())
            except Exception as e:return s._send(500,"application/json",json.dumps({"error":type(e).__name__+": "+str(e)[:200]}).encode())
        if u.path=="/api/activity":
            if not s._authed(u):return s._send(403,"application/json",b'{"error":"forbidden"}')
            try:
                sn=(parse_qs(u.query).get("since") or ["0"])[0];sn=int(sn) if sn.isdigit() else 0
                return s._send(200,"application/json",json.dumps(activity(sn),separators=(",",":"),allow_nan=False,default=str).encode())
            except Exception as e:return s._send(500,"application/json",json.dumps({"error":type(e).__name__+": "+str(e)[:200]}).encode())
        s._send(404,"text/plain",b"not found")
    def _send(s,code,ct,b):
        s.send_response(code)
        for k,v in(("content-type",ct),("cache-control","no-store"),("referrer-policy","no-referrer"),("x-content-type-options","nosniff"),
                   ("content-security-policy",CSP),("permissions-policy","camera=(), microphone=(), geolocation=()")):s.send_header(k,v)
        s.send_header("content-length",str(len(b)));s.end_headers();s.wfile.write(b)
if __name__=="__main__":
    ThreadingHTTPServer((os.environ.get("DASH_HOST","127.0.0.1"),PORT),H).serve_forever()
