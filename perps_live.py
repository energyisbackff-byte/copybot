"""Real-money perps on Hyperliquid: built and switched OFF. It has its OWN permission, separate from /live (Trend Rider):
/live arm or /live trial can never switch it on, and it switches itself off on every restart, after 7 days, on
/panic and on any mismatch between the exchange and the bot's records.
Everything goes through the perps guard (perpsguard/perpsguard.py), which holds a Hyperliquid API ("agent") key that
can trade but can never withdraw; the guard's network (testnet by default), coins, order size, daily limits and
maximum leverage are fixed on the server. This module only proposes; the guard decides.
How a live trade works (the same signals as the paper perps books, perps.py):
  1 the hourly signal passes every rule of the paper book it follows (charter, or grok if you chose it)
  2 size from the REAL account value: charter = 0.25% of the account at risk at the stop; grok = up to half the
    account as margin at 8-19x (the guard's leverage cap still applies: default 3x, only the server owner raises it)
  3 your Approve tap in Telegram (the first 10 orders after /perps live arm; EVERY order in /perps live trial)
  4 entry: an immediate-or-cancel order at most 0.5% past the market (no chasing). A partial fill is fine: the
    protection below is sized to what really filled
  5 protection right after the fill: a stop (market trigger), TP1 for half and TP2 for the rest, all reduce-only,
    then checked against the exchange's open orders. Missing protection = the position is closed at once and live
    switches off. Targets are intended prices: a real fill can be worse (gaps, slippage)
  6 every 60 s: reconcile the exchange's positions with the bot's records; TP1 filled -> the stop moves to breakeven;
    position gone -> the exit is recorded from the exchange's own fills (real P&L, fees and funding)."""
import os,time,json,logging,secrets,socket,math
from tgfmt import term,kv,usd,SEP,wrap
import risk,ledger
log=logging.getLogger("bot")
BOOK="perps-live"
SOCK=os.environ.get("PERPS_SOCK","/run/copybot-perps/guard.sock")
DEF=dict(approvals_required=10,trial_max_margin=10.0,trial_max_lev=3,arm_days=7,min_paper_days=28,min_trades=30,slip=0.005,approval_seconds=120,
         min_account_usd=10.0,follow="perps")
class GuardError(Exception):pass
def guard(op,timeout=60,**k):
    """One request to the perps guard over its Unix socket. Raises GuardError with the guard's own reason."""
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.settimeout(timeout)
    try:
        s.connect(SOCK);s.sendall(json.dumps(dict(k,op=op)).encode()+b"\n");buf=b""
        while not buf.endswith(b"\n"):
            ch=s.recv(65536)
            if not ch:break
            buf+=ch
    except (FileNotFoundError,ConnectionRefusedError):raise GuardError("the perps guard is not running (it is installed by /update)")
    except socket.timeout:raise GuardError("the perps guard did not answer in time")
    finally:s.close()
    r=json.loads(buf or b"{}")
    if not r.get("ok"):raise GuardError(r.get("error") or "refused")
    return r
def fill_of(res):
    """Hyperliquid order answer -> (filled size, average price, oid) for the first status, or (0, None, oid/None)."""
    try:st=res["response"]["data"]["statuses"][0]
    except Exception:return 0.0,None,None
    if "filled" in st:f=st["filled"];return float(f.get("totalSz") or 0),float(f.get("avgPx") or 0),f.get("oid")
    if "resting" in st:return 0.0,None,st["resting"].get("oid")
    return 0.0,None,None
def oids_of(res):
    out=[]
    try:
        for st in res["response"]["data"]["statuses"]:
            x=st.get("resting") or st.get("filled") or {};out.append(x.get("oid"))
    except Exception:pass
    return out
class PerpsLive:
    def __init__(s,db,cfg,notify,call=None):
        s.db,s.cfg,s.n=db,cfg,notify;s.call=call or guard;s.c=dict(DEF,**(cfg.get("perps_live") or {}));s._st=(0,None);s.last_rec=0
    def k(s,n,d=None):return s.db.kv_get("perps-live:"+n,d)
    def ks(s,n,v):s.db.kv_set("perps-live:"+n,v)
    # ---- permission --------------------------------------------------------------------------------------
    def arm_info(s):
        a=s.k("armed") or {}
        if not a or a.get("boot")!=s.db.kv_get("boot_id") or time.time()>=a.get("until",0) or s.db.kv_get("panic") or s.k("mismatch"):return None
        return a
    def armed(s):return bool(s.arm_info())
    def mode(s):a=s.arm_info();return a.get("mode") if a else None
    def disarm(s,why,critical=False):
        if s.k("armed"):
            s.ks("armed",None);s.ks("approval",None)
            s.n.send(term("perps live --off",wrap(why)+[SEP,"real-money perps are OFF.","open positions keep their","stop and targets on the","exchange."],("ERR" if critical else "WARN","🔒 perps live off")),tier="critical" if critical else "instant")
    def status(s,fresh=False):
        t,v=s._st
        if fresh or time.time()-t>20:
            try:v=s.call("status")
            except GuardError as e:v={"error":str(e),"down":True}
            s._st=(time.time(),v)
        return v
    def paper_days(s):
        t=s.db.kv_get(s.c["follow"]+":started");return (time.time()-t)/86400 if t else 0.0
    def checks(s):
        st=s.status(True);out=dict(safety=[],strategy=[])
        up=not st.get("down");out["safety"].append((up,"perps guard running" if up else st.get("error","guard not running")))
        if up:
            out["safety"].append((True,f"network: {'TESTNET (practice money)' if st.get('network')=='test' else 'MAINNET (real money)'}"))
            out["safety"].append((bool(st.get("master")),"your Hyperliquid wallet (master) set" if st.get("master") else "master not set: /perps setmaster 0x..."))
            out["safety"].append((bool(st.get("approved")),"agent approved in the Hyperliquid app" if st.get("approved") else f"approve the agent {str(st.get('agent',''))[:10]}… in the Hyperliquid app (API page)"))
            stt=st.get("selftest") or {};ok=bool(stt.get("ok")) and time.time()-stt.get("ts",0)<30*86400
            out["safety"].append((ok,"testnet selftest passed" if ok else "run /perps live selftest (on testnet)"))
            av=st.get("account_value");out["safety"].append(((av or 0)>=s.c["min_account_usd"],f"account value {usd(av or 0)} (need {usd(s.c['min_account_usd'])})"))
        out["safety"].append((not s.k("mismatch"),"exchange matches the bot's records" if not s.k("mismatch") else "MISMATCH: review, then /perps live clear"))
        bt=(s.db.kv_get("backtest:perps") or {}).get("charter") or {}
        out["strategy"].append((bool(bt.get("passed")),"perps backtest passed the evidence bar" if bt.get("passed") else "perps backtest has not passed the bar (/backtest perps)"))
        d=s.paper_days();out["strategy"].append((d>=s.c["min_paper_days"],f"{s.c['follow']} on paper {d:.1f} days (need {s.c['min_paper_days']})"))
        n=s.db.q("select count(*) from b_positions where book=? and status='closed'",(s.c["follow"],))[0][0]
        out["strategy"].append((n>=s.c["min_trades"],f"{n} closed paper trades (need {s.c['min_trades']})"))
        return out
    def arm(s,code=None,mode="trial",sizing="charter"):
        ch=s.checks();safe=all(ok for ok,_ in ch["safety"]);strat=all(ok for ok,_ in ch["strategy"])
        rows=[("OK " if ok else "NO ")+t for ok,t in ch["safety"]+ch["strategy"]]
        if not safe:return term("perps live "+mode,sum((wrap(r) for r in rows),[])+[SEP,"fix the NO lines above first"],("WARN","not yet"))
        if mode=="arm" and not strat:return term("perps live arm",sum((wrap(r) for r in rows),[])+[SEP,"the strategy is not proven:","/perps live trial = capped","trial, every order tapped"],("WARN","not proven yet"))
        if not code:
            c=f"{secrets.randbelow(10**6):06d}";s.ks("arm_code",dict(code=c,exp=time.time()+120,mode=mode,sizing=sizing))
            st=s.status();net="TESTNET" if st.get("network")=="test" else "MAINNET (REAL MONEY)"
            return term("perps live "+mode,[kv("network",net),kv("follows",sizing+(" (8-19x spec)" if sizing=="grok" else " (0.25% risk)")),
                kv("caps",f"guard {st.get('limits',{}).get('max_order_usd','?')}$/order, {st.get('limits',{}).get('max_leverage','?')}x max"),
                kv("taps","every order" if mode=="trial" else f"first {s.c['approvals_required']} orders")]+([kv("trial",f"margin <= ${s.c['trial_max_margin']:.0f}, <= {s.c['trial_max_lev']}x")] if mode=="trial" else [])+
                [SEP,"switches off after 7 days,","on restart and on /panic",SEP,f"confirm: /perps live {mode} {c}"],("WARN","confirm with code"))
        a=s.k("arm_code") or {}
        if code!=a.get("code") or time.time()>a.get("exp",0) or a.get("mode")!=mode:return term("perps live "+mode,["code wrong or expired"],("ERR","not switched on"))
        s.ks("arm_code",None);s.ks("armed",dict(ts=time.time(),boot=s.db.kv_get("boot_id"),until=time.time()+s.c["arm_days"]*86400,mode=mode,sizing=a.get("sizing","charter")))
        if s.k("start") is None:s.ks("start",s.status(True).get("account_value"))
        return term("perps live "+mode,["real-money perps ON ("+mode+")","following "+a.get("sizing","charter"),"off: /perps live off"],("OK","🔓 perps live "+mode.upper()))
    # ---- proposing a trade ----------------------------------------------------------------------------------
    def size(s,sg,mt,b):
        """-> (notional USD, leverage, note) for the real account, or (0, 0, why)."""
        st=s.status(True);av=float(st.get("account_value") or 0);cap=int((st.get("limits") or {}).get("max_leverage") or 1)
        mo=s.mode();a=s.arm_info() or {};mx=min(cap,mt["max_lev"]) if mt else cap
        if av<s.c["min_account_usd"]:return 0,0,f"account value {usd(av)} is too small"
        if a.get("sizing")=="grok":
            import perps as PZ
            lev,note=PZ.choose_leverage(sg["stop_dist"],abs(sg["tp1"]-sg["entry_ref"])/sg["entry_ref"],mx,b,PZ.DEF)
            if not lev:return 0,0,"grok spec: "+note+f" (the guard allows {cap}x)"
            margin=av*(b["margin_a_plus"] if sg["score"]>=PZ.DEF["a_plus"] else b["margin_a"])
        else:
            rf,_=risk.risk_fraction(s.db,s.cfg,"perps",live=True)
            if rf<=0:return 0,0,"no measurable edge yet (Kelly <= 0)"
            notional=risk.size_for_risk(av,sg["stop_dist"],0.00045+0.0002,rf);margin=min(notional,av*0.25);lev=max(1,math.ceil(notional/max(margin,1e-9)-1e-9))
            if lev>mx:lev=mx
        if mo=="trial":margin=min(margin,s.c["trial_max_margin"]);lev=min(lev,s.c["trial_max_lev"])
        lev=min(lev,mx);notional=margin*lev
        lim=float((st.get("limits") or {}).get("max_order_usd") or 0)
        if lim and notional>lim:notional=lim
        if notional<10:return 0,0,f"order ${notional:.2f} below Hyperliquid's $10 minimum"
        return notional,lev,f"{lev}x, margin {usd(notional/lev)} of {usd(av)}"
    def needs_tap(s):return s.mode()=="trial" or (s.k("approved_count") or 0)<s.c["approvals_required"]
    def consider(s,coin,sg,mt,b,meta_info=None):
        """An hourly signal that the paper book took. Proposes the same trade with real money (if switched on)."""
        if not s.armed():return None
        if any(p[0]==coin for p in s.db.q("select mint from b_positions where book=? and status='open'",(BOOK,))):return None
        why=risk.entry_block(s.db,s.cfg,BOOK,coin=coin)
        if why:s.signal(coin,"refused",why);return None
        notional,lev,note=s.size(sg,mt,b)
        if not notional:s.signal(coin,"refused",note);return None
        px=sg["entry_ref"];sz=notional/px
        plan=dict(coin=coin,side=sg["direction"],sz=sz,lev=lev,notional=notional,ref=px,stop=sg["stop"],tp1=sg["tp1"],tp2=sg["tp2"],score=sg["score"],why=sg["why"],note=note,sz_dec=(mt or {}).get("sz_dec",0),
                  narrative=(meta_info or {}).get("narrative"),narrative_name=(meta_info or {}).get("name"),t=sg.get("t"))
        if s.needs_tap():
            aid=secrets.token_hex(6);s.ks("approval",dict(plan,id=aid,status="pending",exp=time.time()+s.c["approval_seconds"]))
            s.n.send(term("perps live --approve",[kv("coin",coin),kv("side","LONG" if plan["side"]>0 else "SHORT"),kv("size",usd(notional)+f" at {lev}x"),kv("price",f"~${px:.6g}"),
                kv("stop",f"${plan['stop']:.6g}"),kv("TP1 50%",f"${plan['tp1']:.6g}"),kv("TP2",f"${plan['tp2']:.6g}"),SEP]+wrap(sg["why"])+[SEP,f"answer within {s.c['approval_seconds']//60} min"],
                ("WARN","🔔 REAL-MONEY perps order: approve?")),buttons=[[("✅ Approve","pappr:"+aid+":yes"),("❌ Reject","pappr:"+aid+":no")]],tier="instant")
            return "pending"
        return s.execute(plan)
    def signal(s,coin,decision,why,size=None):
        try:s.db.ins("b_signals",ts=time.time(),book=BOOK,source="perps-live",mint=coin,side="buy",decision=decision,reason=why,size_usd=size,features="{}")
        except Exception:pass
    def process_approval(s,mids):
        a=s.k("approval")
        if not a or a.get("status")=="pending" and time.time()<a.get("exp",0):return
        s.ks("approval",None)
        if a.get("status")!="yes":
            s.signal(a["coin"],"refused","you rejected it" if a.get("status")=="no" else "approval expired");return
        px=(mids or {}).get(a["coin"]);d=a["side"]
        if not px or d*(px-a["stop"])<=0 or d*(a["tp1"]-px)<=0 or abs(px/a["ref"]-1)>s.c["slip"]*2:
            s.signal(a["coin"],"refused",f"price moved (${px}) while waiting for the tap");return
        s.ks("approved_count",(s.k("approved_count") or 0)+1);s.execute(a)
    # ---- executing with protection -----------------------------------------------------------------------------
    def execute(s,p):
        coin,d=p["coin"],p["side"];side="buy" if d>0 else "sell";close_side="sell" if d>0 else "buy"
        try:r=s.call("order",orders=[dict(coin=coin,side=side,sz=p["sz"],kind="ioc",px=p["ref"]*(1+d*s.c["slip"]))],lev=p["lev"])
        except GuardError as e:s.signal(coin,"refused","guard: "+str(e),p["notional"]);s.n.send(term("perps live --entry",[kv("coin",coin)]+wrap("guard refused: "+str(e)),("WARN","entry not sent")),tier="instant");return None
        sz,avg,oid=fill_of(r.get("result"))
        if sz<=0:s.signal(coin,"refused","not filled (price ran away; IOC order cancelled)",p["notional"]);return None
        dd=int(p.get("sz_dec") or 0);h1=math.floor(sz*0.5*10**dd)/10**dd;h2=round(sz-h1,dd)   # the two halves add up exactly to what filled
        prot=[dict(coin=coin,side=close_side,sz=sz,kind="sl",trigger=p["stop"],reduce_only=True)]+\
             ([dict(coin=coin,side=close_side,sz=h1,kind="tp",trigger=p["tp1"],reduce_only=True)] if h1>0 else [])+\
             [dict(coin=coin,side=close_side,sz=h2,kind="tp",trigger=p["tp2"],reduce_only=True)]
        ok=False;oids=[]
        try:pr=s.call("order",orders=prot);oids=oids_of(pr.get("result"));ok=self_check(s,coin,sz)
        except GuardError as e:log.error("perps live protection failed: %s",e)
        meta=dict(p,entry=avg,size=sz,sl_oid=oids[0] if oids else None,tp1_oid=oids[1] if len(oids)>2 else None,tp2_oid=oids[-1] if len(oids)>1 else None,tp1_done=h1<=0,opened=time.time(),stop0=p["stop"])
        pid=s.db.ins("b_positions",book=BOOK,trader="perps-live:"+("long" if d>0 else "short"),mint=coin,tokens=sz,cost=sz*avg/p["lev"],opened=time.time(),status="open",open_sig=f"pl:{coin}:{int(time.time())}",meta=json.dumps(meta))
        s.db.ins("b_trades",ts=time.time(),book=BOOK,trader="perps-live",mint=coin,side="buy",usd=sz*avg/p["lev"],tokens=sz,price=avg,pnl=0,sig=f"pl-open:{pid}",note=f"{'LONG' if d>0 else 'SHORT'} {p['lev']}x REAL")
        s.signal(coin,"filled","passed every check",sz*avg)
        if not ok:
            for o in [x for x in oids if x]:
                try:s.call("cancel",coin=coin,oid=o)
                except Exception as e:log.warning("cancel %s: %s",o,e)
            s.flatten(coin,sz,close_side,"protection missing")
            s.disarm(f"{coin}: the stop/targets could not be confirmed on the exchange, so the position was closed at once.",critical=True)
            s.record_exit(dict(id=pid,coin=coin,size=sz,meta=meta),why="protection_missing");return pid
        s.n.send(term("perps live --open",[kv("coin",coin),kv("side",("LONG" if d>0 else "SHORT")+f" {p['lev']}x"),kv("filled",f"{sz:g} @ ${avg:.6g}"),kv("notional",usd(sz*avg)),SEP,
            "on the exchange (reduce-only):",kv(" stop",f"${p['stop']:.6g}"),kv(" TP1 50%",f"${p['tp1']:.6g}"),kv(" TP2",f"${p['tp2']:.6g}")]+wrap("targets are intended prices; fills can differ"),
            ("OK","🟢 REAL perps entry confirmed")),tier="instant")
        return pid
    def flatten(s,coin,sz,side,why):
        try:s.call("order",orders=[dict(coin=coin,side=side,sz=sz,kind="ioc",reduce_only=True)])
        except GuardError as e:
            s.n.send(term("perps live --close",[kv("coin",coin)]+wrap(f"could not close ({why}): {e}. Close it in the Hyperliquid app."),("ERR","🔴 CLOSE FAILED")),tier="critical")
    # ---- every check ------------------------------------------------------------------------------------------
    def tick(s,mids=None):
        if s.db.kv_get("perps-live:panic"):s.panic();return
        if s.k("armed") and not s.arm_info() and not s.k("mismatch"):
            a=s.k("armed") or {};s.ks("armed",None)
            if a.get("boot")!=s.db.kv_get("boot_id"):log.info("perps live: off after restart")
            elif time.time()>=a.get("until",0):s.n.send(term("perps live --expired",["7 days are over: off.","/perps live to switch on"],("OK","🔒 perps live off")),tier="instant")
        try:s.process_approval(mids)
        except Exception:log.exception("perps live approval")
        if time.time()-s.last_rec>=60:
            s.last_rec=time.time()
            try:s.reconcile()
            except Exception:log.exception("perps live reconcile")
    def reconcile(s):
        mine=[dict(id=i,coin=m,size=t,meta=json.loads(mt or "{}")) for i,m,t,mt in s.db.q("select id,mint,tokens,meta from b_positions where book=? and status='open'",(BOOK,))]
        st=s.status(True)
        if st.get("down") or st.get("error"):
            if mine:log.warning("perps live reconcile: guard unavailable (%s)",st.get("error"))
            return
        ex={p["coin"]:p for p in st.get("positions") or [] if p.get("szi")}
        for p in mine:
            x=ex.pop(p["coin"],None);m=p["meta"]
            if not x:s.record_exit(p);continue
            szi=abs(x["szi"])
            if not m.get("tp1_done") and szi<p["size"]*0.75:
                m["tp1_done"]=True;close_side="sell" if m["side"]>0 else "buy";be=m["entry"]*(1+m["side"]*0.0009)
                try:
                    if m.get("sl_oid"):s.call("cancel",coin=p["coin"],oid=m["sl_oid"])
                    r=s.call("order",orders=[dict(coin=p["coin"],side=close_side,sz=szi,kind="sl",trigger=be,reduce_only=True)]);m["sl_oid"]=(oids_of(r.get("result")) or [None])[0];m["stop"]=be
                    s.n.send(term("perps live --tp1",[kv("coin",p["coin"]),"TP1 filled: half closed","stop moved to breakeven",kv("stop",f"${be:.6g}")],("OK","🟢 REAL TP1 hit")),tier="instant")
                except GuardError as e:s.n.send(term("perps live --tp1",[kv("coin",p["coin"])]+wrap(f"TP1 filled but the stop could not move: {e}"),("ERR","🔴 stop not moved")),tier="critical")
                s.db.x("update b_positions set tokens=?,meta=? where id=?",(szi,json.dumps(m),p["id"]))
        if ex:
            s.ks("mismatch",dict(ts=time.time(),coins=list(ex)))
            s.disarm("the exchange shows position(s) the bot did not open: "+", ".join(ex)+". Review them in the Hyperliquid app, then /perps live clear.",critical=True)
    def record_exit(s,p,why=None):
        """The position is gone on the exchange: book the real result from the exchange's own fills."""
        m=p["meta"];since=int((m.get("opened") or time.time()-86400)*1000)
        try:F=[f for f in s.call("fills",since_ms=since).get("fills") or [] if f.get("coin")==p["coin"]]
        except GuardError:F=[]
        pnl=sum(float(f.get("closedPnl") or 0) for f in F)-sum(float(f.get("fee") or 0) for f in F)
        cl=[f for f in F if str(f.get("dir","")).startswith("Close")];px=float(cl[-1]["px"]) if cl else None
        why=why or ("liquidation" if any("liquidat" in json.dumps(f).lower() for f in cl) else "stop_loss" if px is not None and m["side"]*(px-m["entry"])<0 else
             "breakeven_stop" if m.get("tp1_done") and px is not None and abs(px/m["entry"]-1)<0.002 else "take_profit" if px is not None else "closed")
        s.db.x("update b_positions set tokens=0,cost=0,status='closed',closed=?,realized=? where id=?",(time.time(),pnl,p["id"]))
        s.db.ins("b_trades",ts=time.time(),book=BOOK,trader="perps-live",mint=p["coin"],side="sell",usd=0,tokens=p["size"],price=px,pnl=pnl,sig=f"pl-close:{p['id']}",note=why+" (exchange fills)")
        ledger.record(s.db,book=BOOK,asset=p["coin"]+"-PERP",dir=("long" if m["side"]>0 else "short")+f" {m.get('lev')}x REAL",narrative=m.get("narrative_name"),score=m.get("score"),entry=m["entry"],exit=px,
            size=m["entry"]*m["size"],stop=m.get("stop0"),target=m.get("tp2"),trigger=why,market="exchange fills",result="win" if pnl>0 else "loss",pnl=pnl,
            fees=sum(float(f.get("fee") or 0) for f in F),notes="real money, from the exchange's fills",source=m.get("why"))
        risk.on_exit(s.db,s.cfg,BOOK,p["coin"],why,pnl,narrative=m.get("narrative"))
        s.n.send(term("perps live --close",[kv("coin",p["coin"]),kv("why",why),kv("exit",f"${px:.6g}" if px else "–"),kv("P&L",usd(pnl,True)+" net (real)")],
            ("OK" if pnl>=0 else "ERR",("🟢 " if pnl>=0 else "🔴 ")+"REAL perps closed")),tier="critical" if why=="liquidation" else "instant")
    def panic(s):
        s.db.kv_set("perps-live:panic",None);s.ks("armed",None);s.ks("approval",None)
        open_=s.db.q("select count(*) from b_positions where book=? and status='open'",(BOOK,))[0][0]
        try:r=s.call("close_all",timeout=90);msg=["every order cancelled,","every position closed","(reduce-only)"]
        except GuardError as e:
            if not open_:return          # real-money perps were never used: nothing to close, no noise
            msg=wrap(f"guard: {e}. Check the Hyperliquid app.")
        s.n.send(term("perps live --panic",msg,("ERR","🛑 perps PANIC")),tier="critical")
    def clear(s):s.ks("mismatch",None)
    def text(s):
        ch=s.checks();a=s.arm_info();st=s.status()
        rows=[kv("state",("ON ("+a["mode"]+", "+a.get("sizing","charter")+")") if a else "OFF"),kv("network",{"test":"testnet","main":"MAINNET"}.get(st.get("network"),"–")),
              kv("account",usd(st.get("account_value") or 0)),kv("agent",str(st.get("agent") or "–")[:12]+"…"),SEP,"SAFETY:"]
        rows+=sum((wrap(("[OK] " if ok else "[NO] ")+t,"","     ") for ok,t in ch["safety"]),[])+["STRATEGY (for arm):"]+sum((wrap(("[OK] " if ok else "[NO] ")+t,"","     ") for ok,t in ch["strategy"]),[])
        rows+=[SEP,"/perps agent      agent address","/perps setmaster 0x...","/perps live selftest","/perps live trial | arm","/perps live off"]
        return term("perps live --status",rows,("OK","🔐 real-money perps: "+("ON" if a else "OFF")))
def self_check(s,coin,sz):
    """After placing the protection: the exchange must show reduce-only stop/targets covering the filled size."""
    st=s.status(True);O=[o for o in st.get("open_orders") or [] if o.get("coin")==coin and o.get("reduce_only")]
    stop=[o for o in O if "stop" in str(o.get("kind","")).lower() or (o.get("trigger") and "take" not in str(o.get("kind","")).lower() and "tp" not in str(o.get("kind","")).lower())]
    tps=[o for o in O if o not in stop]
    return bool(stop) and sum(o.get("sz") or 0 for o in stop)>=sz*0.99 and sum(o.get("sz") or 0 for o in tps)>=sz*0.99
