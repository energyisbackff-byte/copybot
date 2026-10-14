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
HTML_PATH=os.path.join(HERE,"index.html")

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
    for k,words in (("low liquidity",("liquidity",)),("price impact",("impact",)),("no route / quote",("quote","route")),
                    ("max positions",("max open",)),("already holding",("already holding",)),("daily loss limit",("daily loss",)),
                    ("drawdown shutdown",("drawdown",)),("too small / no cash",("below minimum","no cash"))):
        if any(w in r for w in words):return k
    return "other"

EXIT_KIND={None:"trader","trader_sold":"trader","stop_loss":"stop-loss","take_profit":"take-profit","max_hold":"time limit","write_off":"written off"}
def _med(x):return st.median(x) if x else None
def _mean(x):return st.mean(x) if x else None
def downsample(pts,n=1500):
    if len(pts)<=n:return pts
    step=len(pts)/n;out=[pts[int(i*step)] for i in range(n)]
    return out+[pts[-1]] if out[-1]!=pts[-1] else out

def state():
    C=load_cfg();START=C["start"];now=time.time()
    c=sqlite3.connect(f"file:{DB}?mode=ro",uri=True,timeout=5)
    try:
        q=lambda s,a=():c.execute(s,a).fetchall()
        tables={r[0] for r in q("select name from sqlite_master where type='table'")}
        kv={k:json.loads(v) for k,v in q("select k,v from kv where k in ('cash','peak','last_equity','selected','selected_at','shutdown','last_poll','day_start','ws','pending_sells')")}
        cash=kv.get("cash",START)
        # ---- open positions (marked live) ----
        pos=q("select trader,mint,tokens,cost,opened,open_sig from positions where status='open'");px=prices([p[1] for p in pos])
        P=[];unrl=0
        for tr,m,tok,cost,op,osig in pos:
            cur=px.get(m);val=tok*cur if cur else None;u=(val-cost) if val is not None else None;unrl+=u or 0
            ent=q("select price from trades where side='buy' and sig=?",(osig,)) or q("select price from trades where side='buy' and trader=? and mint=? order by ts desc limit 1",(tr,m))
            P.append(dict(trader=tr,mint=m,tokens=tok,cost=cost,entry=ent[0][0] if ent else None,price=cur,value=val,upnl=u,
                upct=(u/cost if u is not None and cost else None),opened=op,age=now-op if op else None))
        equity=cash+sum((p["value"] if p["value"] is not None else p["cost"]) for p in P)
        # ---- equity curve + drawdown ----
        eq=[(t,e) for t,e in q("select ts,equity from equity order by ts")]
        pk=START;mdd=0
        for _,e in eq:pk=max(pk,e);mdd=max(mdd,(pk-e)/pk if pk else 0)
        peak=max(kv.get("peak",START),pk);cur_dd=max(0,(peak-equity)/peak) if peak else 0
        first_ts=eq[0][0] if eq else (q("select min(ts) from trades")[0][0])
        # ---- fills (execution quality) ----
        has_fills="fills" in tables
        fills=q("select sig,trader,mint,side,status,reason,trader_px,our_px,slip_bps,detect_ms,total_ms,size_usd,fee_usd,fill_ts from fills") if has_fills else []
        F=[f for f in fills if f[4] in("filled","failed")];FL=[f for f in F if f[4]=="filled"]
        det=[f[9] for f in fills if f[9] is not None];tot=[f[10] for f in FL if f[10] is not None];sl=[f[8] for f in FL if f[8] is not None]
        ex=dict(n_fills=len(FL),n_failed=len(F)-len(FL),fail_rate=(len(F)-len(FL))/len(F) if F else None,
            det_med_ms=_med(det),det_avg_ms=_mean(det),total_med_ms=_med(tot),slip_avg_bps=_mean(sl),slip_med_bps=_med(sl))
        fee_total=sum(f[12] or 0 for f in FL)
        buy_fill={f[0]:f for f in FL if f[3]=="buy"}
        # ---- closed round trips (one per position, partial sells merged) ----
        trips=[]
        for pid,tr,m,op,cl,real,osig in q("select id,trader,mint,opened,closed,realized,open_sig from positions where status='closed' order by closed desc"):
            b=q("select usd from trades where side='buy' and sig=?",(osig,));cost=b[0][0] if b else None
            bf=buy_fill.get(osig);sells=[f for f in FL if f[3]=="sell" and f[1]==tr and f[2]==m and op and (f[13] or 0)>=op and (f[13] or 0)<=(cl or now)+5]
            gap=tret=oret=None
            if bf and sells and bf[6] and bf[7]:
                w=sum(f[11] or 0 for f in sells)
                if w>0 and all(f[6] for f in sells):
                    tsell=sum((f[6])*(f[11] or 0) for f in sells)/w;osell=sum((f[7] or 0)*(f[11] or 0) for f in sells)/w
                    tret=tsell/bf[6]-1;oret=osell/bf[7]-1;gap=tret-((real or 0)/cost if cost else oret)
            xr=q("select reason from fills where side='sell' and status in('filled','writeoff') and trader=? and mint=? and fill_ts>=? and fill_ts<=? order by fill_ts desc limit 1",
                 (tr,m,op or 0,(cl or now)+5)) if has_fills else []
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
        sel=kv.get("selected") or [];T=[]
        for s in sel:
            m=dec.get(s["handle"],({},""))[0];mine=[t for t in trips if t["trader"]==s["handle"]]
            sl_t=[f[8] for f in FL if f[1]==s["handle"] and f[8] is not None];g_t=[t["gap"] for t in mine if t["gap"] is not None]
            T.append(dict(source=s.get("source","fomo"),handle=s["handle"],wallet=s["wallet"],pnl30=m.get("pnl"),win=m.get("win"),pf=m.get("pf"),
                trades=m.get("trades"),our_pnl=sum(t["pnl"] for t in mine),our_trips=len(mine),our_win=(sum(t["pnl"]>0 for t in mine)/len(mine)) if mine else None,
                our_slip=_mean(sl_t),our_gap=_med(g_t),open=sum(p["trader"]==s["handle"] for p in P)))
        # ---- skip reasons ----
        sk={}
        for (reason,) in q("select note from trades where side='skip'"):
            k=skip_kind(reason);sk[k]=sk.get(k,0)+1
        skips=sorted(sk.items(),key=lambda x:-x[1])
        # ---- activity log ----
        log=[]
        for t,tr,r in q("select ts,trader,reason from decisions where selected=1 order by ts desc limit 10"):log.append(dict(ts=t,kind="select",text=f"PICKED {tr}: {r}"))
        for t,tr,m,side,usd,pnl,note in q("select ts,trader,mint,side,usd,pnl,note from trades order by ts desc limit 80"):
            txt={"buy":f"BUY {m[:6]}… ${usd:.2f} copying {tr}","sell":f"SELL {m[:6]}… ${usd:.2f} PnL {pnl:+.2f} ({tr})","skip":f"SKIP {m[:6]}… from {tr}: {note}"}.get(side,f"{side} {m[:6]}…")
            log.append(dict(ts=t,kind=side if side in("buy","sell","skip") else "info",text=txt,mint=m))
        for t,tr,m,r in q("select fill_ts,trader,mint,reason from fills where status='failed' order by ts desc limit 15") if has_fills else []:
            log.append(dict(ts=t or now,kind="guard",text=f"FILL FAILED {m[:6]}… ({tr}): {r}",mint=m))
        if kv.get("shutdown"):log.append(dict(ts=now,kind="guard",text="SAFEGUARD: drawdown auto-shutdown active"))
        log.sort(key=lambda x:-x["ts"])
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
        selected_at=kv.get("selected_at"))

_cache={"t":0,"body":None};_lock=threading.Lock()
def state_json():
    with _lock:
        if time.time()-_cache["t"]>3 or _cache["body"] is None:
            _cache["body"]=json.dumps(state(),separators=(",",":"),allow_nan=False,default=str).encode();_cache["t"]=time.time()
        return _cache["body"]

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
        if u.path=="/api/state":
            if not s._authed(u):return s._send(403,"application/json",b'{"error":"forbidden"}')
            try:return s._send(200,"application/json",state_json())
            except Exception as e:return s._send(500,"application/json",json.dumps({"error":type(e).__name__+": "+str(e)[:200]}).encode())
        s._send(404,"text/plain",b"not found")
    def _send(s,code,ct,b):
        s.send_response(code)
        for k,v in(("content-type",ct),("cache-control","no-store"),("referrer-policy","no-referrer"),("x-content-type-options","nosniff"),
                   ("content-security-policy",CSP),("permissions-policy","camera=(), microphone=(), geolocation=()")):s.send_header(k,v)
        s.send_header("content-length",str(len(b)));s.end_headers();s.wfile.write(b)
if __name__=="__main__":
    ThreadingHTTPServer((os.environ.get("DASH_HOST","127.0.0.1"),PORT),H).serve_forever()
