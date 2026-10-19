"""Real-money book 'trend-live' (Trend Rider only). Built, OFF by default, and YOU decide when it turns on:
  /live arm    the normal way: every SAFETY check and every STRATEGY check passes (Tier 1). The first
               `approvals_required` live buys need your Approve tap.
  /live trial  your override while the strategy is not proven yet: every SAFETY check must still pass, the strategy
               checks may fail. Tighter: positions at most `trial_max_usd`, and EVERY buy needs your tap.
Both switch themselves off after `arm_days` (renew with the same command) and on any restart, /update, /panic,
wallet mismatch or the drawdown switch. Switching off only stops NEW buys: an open position keeps its stop and exits.
SAFETY checks (arm and trial): signer running, home address confirmed, a test withdrawal you confirmed arriving,
wallet funded (USDC + fee SOL), a self-test with a funded simulation in the last 24h, no panic, no unresolved wallet
mismatch, drawdown switch not tripped.
STRATEGY checks (arm only): the current strategy version passed its backtest gate, and has run 7+ days on paper.
TIER 2 (shown, never automatic): raising the signer's limits above $20/swap $100/day is only worth discussing after
30+ paper days with a closed paper trade AND 20+ clean live round trips.
The bot never sees the private key: it asks the signer (signer/signer.py), which only signs SOL<->USDC swaps inside the
wallet within hard limits, or transfers to home. Exits are never blocked by the signer's buy limits."""
import os,sys,json,time,socket,random,base64,logging,datetime as dt,urllib.request,urllib.parse
from tgfmt import term,kv,usd as fusd,SEP,esc,wrap
sys.path.insert(0,os.path.join(os.path.dirname(os.path.abspath(__file__)),"signer"))
import solwire as W                    # wire helpers only (no key, no crypto) for tx ids and self-test transactions
def ticks(items):
    """[(ok, text)] -> wrapped '✓ ' / '✗ ' rows."""
    return sum((wrap(t,("✓ " if ok else "✗ ")) for ok,t in items),[])
from chain import USDC,WSOL,JUP,_jh,jup_price
log=logging.getLogger("bot");BOOK="trend-live"
SOCK=os.environ.get("SIGNER_SOCK","/run/copybot-signer/signer.sock")
DEF=dict(approvals_required=10,slippage_bps=100,priority_max_lamports=100000,sol_reserve=0.02,min_usdc_to_arm=5.0,
         min_paper_days=14,daily_loss_limit_pct=0.02,weekly_loss_limit_pct=0.04,max_drawdown_pct=0.06,approval_seconds=120,
         arm_days=7,trial_max_usd=10.0,test_withdraw_usdc=1.0,test_withdraw_sol=0.002,selftest_max_age_h=24,
         tier2_paper_days=30,tier2_live_trades=20,max_send_seconds=180)
class LiveError(Exception):pass
class Unknown(LiveError):
    """A transaction was sent but its outcome is not known yet. It is kept and checked before anything new is sent."""
def signer(op,timeout=40,**k):
    try:
        s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.settimeout(timeout);s.connect(SOCK)
        s.sendall(json.dumps(dict(op=op,**k)).encode()+b"\n");buf=b""
        while not buf.endswith(b"\n"):
            c=s.recv(65536)
            if not c:break
            buf+=c
        s.close();r=json.loads(buf)
    except (FileNotFoundError,ConnectionRefusedError,PermissionError,socket.timeout,OSError,ValueError) as e:
        raise LiveError(f"signer not reachable ({type(e).__name__}); send /update to install it") from None
    if not r.get("ok"):raise LiveError(r.get("error","signer refused"))
    return r
def jup_quote_full(inm,outm,amount_raw,bps):
    u=JUP+"/swap/v1/quote?"+urllib.parse.urlencode({"inputMint":inm,"outputMint":outm,"amount":int(amount_raw),"slippageBps":int(bps),"restrictIntermediateTokens":"true"})
    return json.load(urllib.request.urlopen(urllib.request.Request(u,headers=_jh()),timeout=15))
def jup_swap_tx(quote,user,max_lamports):
    body=dict(quoteResponse=quote,userPublicKey=user,wrapAndUnwrapSol=True,dynamicComputeUnitLimit=True,
              prioritizationFeeLamports={"priorityLevelWithMaxLamports":{"maxLamports":int(max_lamports),"priorityLevel":"high"}})
    h=dict(_jh(),**{"content-type":"application/json"})
    r=json.load(urllib.request.urlopen(urllib.request.Request(JUP+"/swap/v1/swap",json.dumps(body).encode(),h),timeout=20))
    if "swapTransaction" not in r:raise LiveError("Jupiter returned no transaction: "+str(r)[:120])
    return r["swapTransaction"],r.get("lastValidBlockHeight")
def tx_sig(tx_b64):
    """Transaction id = base58 of its first signature (known before sending)."""
    raw=base64.b64decode(tx_b64);n,i=W.cu16_read(raw,0);return W.b58e(raw[i:i+64])
def short(a):return (a[:4]+"…"+a[-4:]) if a else "–"
class Live:
    def __init__(s,db,hel,notify,cfg):
        s.db,s.hel,s.n,s.cfg=db,hel,notify,cfg;s.c=dict(DEF,**(cfg.get("live") or {}));s._wallet=None
        s.last_rec=0;s.last_mark=0;s.last_ready=0;s.last_pending=0
    # ---- state ----------------------------------------------------------------------------------
    def K(s,k):return f"{BOOK}:{k}"
    def kget(s,k,d=None):return s.db.kv_get(s.K(k),d)
    def kset(s,k,v):s.db.kv_set(s.K(k),v)
    def wallet(s):
        if not s._wallet:s._wallet=signer("pubkey",timeout=10)["wallet"]
        return s._wallet
    def arm_info(s):
        a=s.db.kv_get("live:armed")
        if not a or a.get("boot")!=s.db.kv_get("boot_id") or s.db.kv_get("panic") or s.kget("shutdown") or s.db.kv_get("live:mismatch"):return None
        if time.time()>=a.get("until",0):return None
        return a
    def armed(s):return bool(s.arm_info())
    def mode(s):a=s.arm_info();return a.get("mode","arm") if a else None
    def disarm(s,why,quiet=False):
        was=s.db.kv_get("live:armed");s.db.kv_set("live:armed",None);s.db.kv_set("live:approval",None)
        if was and not quiet:s.n.send(term("live --disarm",[kv("reason",why),SEP,"real-money buys are OFF.","an open position still","exits by its rules.","turn on again: /live"],("WARN","🔒 LIVE OFF")))
    def balances(s):
        w=s.wallet();lam=s.hel.rpc("getBalance",[w,{"commitment":"confirmed"}])["value"]
        r=s.hel.rpc("getTokenAccountsByOwner",[w,{"mint":USDC},{"encoding":"jsonParsed","commitment":"confirmed"}])["value"]
        usdc=sum(float(x["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmountString"] or 0) for x in r)
        return dict(sol=lam/1e9,usdc=usdc,usdc_account=bool(r))
    def position(s):
        r=s.db.q("select id,tokens,cost,opened,meta from b_positions where book=? and status='open' limit 1",(BOOK,))
        return dict(id=r[0][0],tokens=r[0][1],cost=r[0][2],opened=r[0][3],meta=json.loads(r[0][4] or "{}")) if r else None
    def equity(s,b=None):
        b=b or s.balances();pos=s.position();px=jup_price(WSOL)[0] or 0
        return b["usdc"]+(pos["tokens"]*px if pos else 0)
    def blocked(s):
        """Reason NEW buys are not allowed right now, or None. Exits never ask this."""
        if not s.armed():return "live is off" if not s.db.kv_get("live:armed") else "live switched off (panic, mismatch, drawdown or expiry)"
        if s.db.kv_get("live:pending_tx"):return "a transaction is still being confirmed"
        day=dt.datetime.utcnow().date().isoformat();ds=s.kget("day_start") or {}
        if ds.get("day")==day and s.kget("last_equity",ds["eq"])<ds["eq"]*(1-s.c["daily_loss_limit_pct"]):return "daily loss limit hit"
        ws=s.kget("week_start") or {}
        if ws.get("week")==dt.datetime.utcnow().strftime("%G-W%V") and s.kget("last_equity",ws["eq"])<ws["eq"]*(1-s.c["weekly_loss_limit_pct"]):return "weekly loss limit hit"
        return None
    def signal(s,mint,decision,reason,size=None,features=None):
        try:s.db.ins("b_signals",ts=time.time(),book=BOOK,source="trend-rider",mint=mint,side="buy",decision=decision,reason=reason,size_usd=size,features=json.dumps(features or {}))
        except Exception as e:log.warning("signal log: %s",e)
    # ---- the one readiness policy ------------------------------------------------------------------
    def paper_days(s):
        t=s.db.kv_get("trend:v2_started");return (time.time()-t)/86400 if t else 0
    def checks(s):
        """-> dict(safety=[(ok,text)], strategy=[...], tier2=[...], extra=[...]). Also stored for the dashboard."""
        import strategy_v2
        out=dict(safety=[],strategy=[],tier2=[],extra=[],ts=time.time())
        sa=out["safety"]
        try:st=signer("status",timeout=10);sa.append((True,"signer running"))
        except LiveError as e:st=None;sa.append((False,str(e)))
        if st:
            sa.append((bool(st.get("home")),"home address set" if st.get("home") else "home not set: /sethome ADDRESS"))
            tw=s.db.kv_get("live:test_withdraw") or {}
            sa.append((bool(tw.get("confirmed")),"test withdrawal arrived (you confirmed)" if tw.get("confirmed") else
                ("test withdrawal sent: check Phantom, then /live received" if tw.get("sig") else "test withdrawal: /live testwithdraw")))
            try:
                b=s.balances()
                sa.append((b["usdc"]>=s.c["min_usdc_to_arm"],f"wallet USDC ${b['usdc']:.2f} (need ${s.c['min_usdc_to_arm']:.0f}+)"))
                sa.append((b["sol"]>=s.c["sol_reserve"],f"fee SOL {b['sol']:.4f} (need {s.c['sol_reserve']})"))
            except Exception as e:sa.append((False,f"balance check failed ({type(e).__name__})"))
        t=s.db.kv_get("live:selftest") or {};fresh=t.get("ts",0)>time.time()-s.c["selftest_max_age_h"]*3600
        sa.append((bool(t.get("ok") and t.get("full") and fresh),"self-test passed (funded, last 24h)" if t.get("ok") and t.get("full") and fresh else
            "self-test: fund the wallet, then /live selftest" if not (t.get("ok") and t.get("full")) else "self-test older than 24h: /live selftest"))
        sa.append((not s.db.kv_get("panic"),"no panic stop" if not s.db.kv_get("panic") else "panic stop is on (/panic off)"))
        mm=s.db.kv_get("live:mismatch")
        sa.append((not mm,"wallet matches records" if not mm else "wallet mismatch: /live clear"))
        sa.append((not s.kget("shutdown"),"drawdown switch not tripped" if not s.kget("shutdown") else "drawdown switch tripped: /live clear"))
        bt=s.db.kv_get("backtest:v2") or {};cur=bt.get("version")==strategy_v2.VERSION
        out["strategy"].append((bool(cur and bt.get("passed")),f"{strategy_v2.VERSION} passed its backtest" if cur and bt.get("passed") else
            (f"{strategy_v2.VERSION} backtest FAILED" if cur and "checks" in bt else f"{strategy_v2.VERSION} backtest not run (/backtest)")))
        br=bt.get("bar") if cur else None
        out["strategy"].append((bool(br and br.get("passed")),"new evidence bar passed (Sharpe, fees x3, Monte Carlo)" if br and br.get("passed") else
            ("new evidence bar NOT passed (/backtest last)" if br else "new evidence bar: run /backtest again")))
        d=s.paper_days();out["strategy"].append((d>=s.c["min_paper_days"],f"v2 on paper {d:.1f} days (need {s.c['min_paper_days']})"))
        t0=s.db.kv_get("trend:v2_started") or time.time()
        closed=s.db.q("select count(*) from b_positions where book='trend' and status='closed' and opened>=?",(t0,))[0][0]
        live_rt=s.db.q("select count(*) from b_positions where book=? and status='closed'",(BOOK,))[0][0]
        out["tier2"]=[(d>=s.c["tier2_paper_days"] and closed>=1,f"paper {d:.0f}/{s.c['tier2_paper_days']} days, {closed} closed trade(s)"),
                      (live_rt>=s.c["tier2_live_trades"],f"live round trips {live_rt}/{s.c['tier2_live_trades']}")]
        bk=s.db.kv_get("live:backup") or {}
        out["extra"]=[(bool(bk),"key backed up offline" if bk else "recommended: back up the key (copybot wallet-export on the console), then /live backedup")]
        s.db.kv_set("live:readiness",dict(out,armed=s.armed(),mode=s.mode(),until=(s.arm_info() or {}).get("until"),
            approvals_left=max(0,s.c["approvals_required"]-(s.db.kv_get("live:approved_count") or 0))))
        return out
    def checklist_rows(s,ch=None):
        ch=ch or s.checks()
        return (["SAFETY (arm + trial):"]+ticks(ch["safety"])+["STRATEGY (arm only):"]+ticks(ch["strategy"])+
                ["TIER 2 (before raising limits):"]+ticks(ch["tier2"])+ticks(ch["extra"]))
    # ---- arming -------------------------------------------------------------------------------------
    def arm(s,code=None,mode="arm"):
        name="/live "+mode;ch=s.checks();safe=all(ok for ok,_ in ch["safety"]);strat=all(ok for ok,_ in ch["strategy"])
        if code is None:
            rows=s.checklist_rows(ch)
            if not safe:return term("live --"+mode,rows+[SEP,"fix the ✗ SAFETY items first"],("WARN","cannot turn on yet"))
            if mode=="arm" and not strat:return term("live --arm",rows+[SEP,"strategy not proven yet.","/live trial = your capped","trial anyway (every buy","needs your tap)"],("WARN","cannot arm yet"))
            c=f"{random.SystemRandom().randrange(10**6):06d}";s.db.kv_set("live:arm_code",dict(code=c,exp=time.time()+120,mode=mode))
            warn=["TRIAL: the strategy is NOT","proven. Real money can be","lost. Max "+fusd(s.c["trial_max_usd"])+" per position,","every buy needs your tap."] if mode=="trial" else ["real money will trade."]
            return term("live --"+mode,rows+[SEP]+warn+[f"off again after {s.c['arm_days']} days.",SEP,"confirm within 2 min:",f"{name} {c}"],("WARN","⚠ confirm with code"))
        a=s.db.kv_get("live:arm_code") or {}
        if not a or time.time()>a.get("exp",0) or code!=a.get("code") or a.get("mode")!=mode:return term("live --"+mode,["code wrong or expired",f"send {name} again"],("ERR","not turned on"))
        s.db.kv_set("live:arm_code",None)
        if not safe or (mode=="arm" and not strat):return term("live --"+mode,["a check failed meanwhile:"]+s.checklist_rows(ch),("ERR","not turned on"))
        b=s.balances();eq=s.equity(b)
        if s.kget("cash") is None:s.kset("start",eq);s.kset("peak",eq)
        s.kset("cash",b["usdc"]);s.kset("last_equity",eq)
        s.kset("limits",dict(start=s.kget("start",eq),max_pos=1,daily=s.c["daily_loss_limit_pct"],max_dd=s.c["max_drawdown_pct"],enabled=True))
        until=time.time()+s.c["arm_days"]*86400
        s.db.kv_set("live:armed",dict(ts=time.time(),boot=s.db.kv_get("boot_id"),until=until,mode=mode))
        left=max(0,s.c["approvals_required"]-(s.db.kv_get("live:approved_count") or 0));s.checks()
        rows=[kv("mode","TRIAL (capped)" if mode=="trial" else "ARMED"),kv("wallet",short(s.wallet())),kv("equity",fusd(eq)),
              kv("approve","every buy" if mode=="trial" else f"next {left} buys"),kv("max buy",fusd(s.c["trial_max_usd"]) if mode=="trial" else "signer limit"),
              kv("until",time.strftime("%d %b %H:%M UTC",time.gmtime(until))),SEP,"Trend Rider now trades","real money. Off: /live off","stop all: /panic"]
        return term("live --"+mode,rows,("OK","🔓 LIVE "+("TRIAL ON" if mode=="trial" else "ARMED")))
    def clear(s,code=None):
        """After you reviewed a wallet mismatch or the drawdown switch: reset the records to the real wallet."""
        mm=s.db.kv_get("live:mismatch");sd=s.kget("shutdown")
        if not mm and not sd:return term("live --clear",["nothing to clear"],("OK","all clear"))
        if code is None:
            c=f"{random.SystemRandom().randrange(10**6):06d}";s.db.kv_set("live:clear_code",dict(code=c,exp=time.time()+120))
            rows=([kv("mismatch",mm.get("detail","?"))] if mm else [])+([kv("drawdown","switch tripped")] if sd else [])
            return term("live --clear",rows+[SEP,"records will be reset to","what the wallet holds now.","live stays OFF afterwards.",SEP,f"confirm: /live clear {c}"],("WARN","confirm with code"))
        a=s.db.kv_get("live:clear_code") or {}
        if not a or time.time()>a.get("exp",0) or code!=a.get("code"):return term("live --clear",["code wrong or expired"],("ERR","not cleared"))
        s.db.kv_set("live:clear_code",None)
        if s.db.kv_get("live:pending_tx"):return term("live --clear",["a transaction is still being","confirmed; try again in a","few minutes"],("WARN","not cleared"))
        b=s.balances();pos=s.position()
        if pos and b["sol"]<pos["tokens"]*0.98:return term("live --clear",["the position's SOL is not in","the wallet. Sell what is","there first: /panic"],("WARN","not cleared"))
        s.kset("cash",b["usdc"]);eq=s.equity(b);s.kset("last_equity",eq);s.kset("peak",eq);s.kset("shutdown",None);s.db.kv_set("live:mismatch",None)
        s.kset("day_start",{"day":dt.datetime.utcnow().date().isoformat(),"eq":eq});s.kset("week_start",{"week":dt.datetime.utcnow().strftime("%G-W%V"),"eq":eq});s.checks()
        return term("live --clear",[kv("USDC",fusd(b["usdc"])),kv("equity",fusd(eq)),SEP,"records match the wallet.","live is still off: /live"],("OK","cleared"))
    # ---- sending: every transaction is tracked until its outcome is known ------------------------------
    def send(s,tx_b64,last_valid=None,timeout=None):
        """Send a signed tx and wait until it CONFIRMS, FAILS or EXPIRES (blockhash too old to ever land). Re-broadcasts the
        SAME signed tx (same signature, so it can only land once). Raises Unknown only if the RPC stays unreachable."""
        timeout=timeout or s.c["max_send_seconds"];sig=tx_sig(tx_b64);t0=time.time();errors=0
        try:s.hel.rpc("sendTransaction",[tx_b64,{"encoding":"base64","skipPreflight":True,"maxRetries":0}])
        except Exception as e:log.warning("send %s: %s",sig[:10],e)
        while time.time()-t0<timeout:
            time.sleep(2)
            try:
                st=s.hel.rpc("getSignatureStatuses",[[sig],{"searchTransactionHistory":False}])["value"][0]
                if st and st.get("confirmationStatus") in("confirmed","finalized"):
                    if st.get("err"):raise LiveError(f"transaction failed on chain: {json.dumps(st['err'])[:100]}")
                    return sig
                if last_valid and s.hel.rpc("getBlockHeight",[{"commitment":"confirmed"}])>last_valid:
                    st=s.hel.rpc("getSignatureStatuses",[[sig],{"searchTransactionHistory":True}])["value"][0]
                    if st and st.get("confirmationStatus") in("confirmed","finalized") and not st.get("err"):return sig
                    raise LiveError("transaction expired without landing (nothing changed)")
                s.hel.rpc("sendTransaction",[tx_b64,{"encoding":"base64","skipPreflight":True,"maxRetries":0}])
            except LiveError:raise
            except Exception as e:
                errors+=1;log.warning("confirm %s: %s",sig[:10],e)
        raise Unknown(f"no answer about {sig[:10]}… after {timeout}s; it is kept and checked before anything new is sent")
    def tx_deltas(s,sig):
        """Exact SOL/USDC change of the wallet from the confirmed transaction, or None."""
        try:t=s.hel.rpc("getTransaction",[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0,"commitment":"confirmed"}])
        except Exception as e:log.warning("getTransaction %s: %s",sig[:10],e);return None
        if not t or not t.get("meta"):return None
        m=t["meta"];w=s.wallet();keys=[k["pubkey"] if isinstance(k,dict) else k for k in t["transaction"]["message"]["accountKeys"]]
        if w not in keys:return None
        i=keys.index(w);dl=m["postBalances"][i]-m["preBalances"][i]
        def tok(lst,mint):return sum(int(x["uiTokenAmount"]["amount"]) for x in (lst or []) if x.get("owner")==w and x.get("mint")==mint)
        dw=tok(m.get("postTokenBalances"),WSOL)-tok(m.get("preTokenBalances"),WSOL);du=tok(m.get("postTokenBalances"),USDC)-tok(m.get("preTokenBalances"),USDC)
        return dict(d_sol=(dl+dw)/1e9,d_usdc=du/1e6)
    def resolve_pending(s):
        """Settle a transaction whose outcome was unknown (timeout, crash, restart) BEFORE sending anything new.
        -> None (nothing pending), 'landed', 'failed', 'expired'; raises Unknown if still undecidable."""
        p=s.db.kv_get("live:pending_tx")
        if not p:return None
        st=s.hel.rpc("getSignatureStatuses",[[p["sig"]],{"searchTransactionHistory":True}])["value"][0]
        if st and st.get("confirmationStatus") in("confirmed","finalized"):
            if st.get("err"):s.db.kv_set("live:pending_tx",None);return "failed"
            s._finalize(p);return "landed"
        if p.get("lv") and s.hel.rpc("getBlockHeight",[{"commitment":"confirmed"}])>p["lv"]:
            st=s.hel.rpc("getSignatureStatuses",[[p["sig"]],{"searchTransactionHistory":True}])["value"][0]
            if st and st.get("confirmationStatus") in("confirmed","finalized") and not st.get("err"):s._finalize(p);return "landed"
            s.db.kv_set("live:pending_tx",None);return "expired"
        try:s.send(p["tx"],p.get("lv"))
        except Unknown:raise
        except LiveError:s.db.kv_set("live:pending_tx",None);return "expired"
        s._finalize(p);return "landed"
    def _finalize(s,p):
        d=s.tx_deltas(p["sig"]) or dict(d_sol=0,d_usdc=0);r=dict(d,sig=p["sig"],impact=p.get("impact",0),seconds=time.time()-p["ts"])
        s.db.kv_set("live:pending_tx",None);ctx=p.get("ctx") or {}
        if p["kind"]=="buy" and r["d_sol"]>0:s._record_buy(r,ctx.get("meta") or {})
        elif p["kind"]=="sell":
            pos=s.position()
            if pos and pos["id"]==ctx.get("pid"):s._record_sell(pos,r,ctx.get("amt",pos["tokens"]),ctx.get("reason","exit"),ctx.get("note","exit"))
        s.n.send(term("live --recover",[kv("tx",p["sig"][:10]+"…"),kv("kind",p["kind"]),kv("SOL",f"{r['d_sol']:+.5f}"),kv("USDC",f"{r['d_usdc']:+.2f}"),SEP,"an earlier transaction did","land; records updated"],("WARN","🔁 recovered pending tx")))
    def swap(s,inm,outm,amount_raw,kind,ctx):
        """Quote -> Jupiter tx -> signer checks + signs -> track -> send -> confirm -> exact balance change."""
        if s.db.kv_get("live:pending_tx"):
            r=s.resolve_pending()
            if r=="landed":raise LiveError("an earlier transaction landed meanwhile; records updated, decide again")
        b0=s.balances();q=jup_quote_full(inm,outm,amount_raw,s.c["slippage_bps"]);imp=float(q.get("priceImpactPct") or 0)
        if imp>0.005:raise LiveError(f"price impact {imp:.2%} too high")
        tx,lv=jup_swap_tx(q,s.wallet(),s.c["priority_max_lamports"]);r=signer("sign_swap",tx=tx);t0=time.time();sig=tx_sig(r["tx"])
        s.db.kv_set("live:pending_tx",dict(sig=sig,tx=r["tx"],kind=kind,lv=lv,ts=t0,ctx=ctx,impact=imp))
        try:s.send(r["tx"],lv)
        except Unknown:raise
        except LiveError:s.db.kv_set("live:pending_tx",None);raise
        d=s.tx_deltas(sig)
        if d is None:time.sleep(2);b1=s.balances();d=dict(d_sol=b1["sol"]-b0["sol"],d_usdc=b1["usdc"]-b0["usdc"])
        s.db.kv_set("live:pending_tx",None)
        return dict(d,sig=sig,impact=imp,seconds=time.time()-t0,checks=r.get("steps"))
    # ---- buys ---------------------------------------------------------------------------------------------
    def cap(s):
        st=signer("status",timeout=10);c=st["limits"]["max_swap_usd"]
        return min(c,s.c["trial_max_usd"]) if s.mode()=="trial" else c
    def buy(s,size,meta,sig_id):
        """Executes a live buy now (approval already handled). -> position id or None."""
        why=s.blocked()
        if why:s.signal(WSOL,"refused",why,size);return None
        b=s.balances();size=min(size,b["usdc"]-0.05,s.cap())
        if size<1:s.signal(WSOL,"refused",f"live size ${size:.2f} too small",size);return None
        try:r=s.swap(USDC,WSOL,int(size*1e6),"buy",dict(meta=meta,sig_id=sig_id))
        except Exception as e:
            s.signal(WSOL,"refused",f"live buy failed: {str(e)[:80]}",size)
            s.n.send(term("live buy",wrap(str(e)[:200])+[SEP,"see /live for the state"],("ERR","🔴 LIVE BUY failed")));return None
        if r["d_sol"]<=0 or r["d_usdc"]>=0:
            s.n.send(term("live buy",[kv("SOL change",f"{r['d_sol']:+.6f}",10),kv("USDC change",f"{r['d_usdc']:+.4f}",10),SEP,"check the wallet; live","switched off"],("ERR","🔴 unexpected result")));s.disarm("unexpected buy result",quiet=True);return None
        return s._record_buy(r,meta)
    def _record_buy(s,r,meta):
        got=r["d_sol"];spent=-r["d_usdc"];px=spent/got;now=time.time()
        pid=s.db.ins("b_positions",book=BOOK,trader="trend-rider",mint=WSOL,tokens=got,cost=spent,opened=now,status="open",open_sig=r["sig"],meta=json.dumps(dict(meta,entry=px)))
        s.db.ins("b_fills",ts=now,book=BOOK,sig=r["sig"],trader="trend-rider",mint=WSOL,side="buy",status="filled",reason="trend entry",fill_ts=now,
                 quote_px=meta.get("signal_ref"),our_px=px,impact=r.get("impact"),size_usd=spent,live=1,txsig=r["sig"])
        s.db.ins("b_trades",ts=now,book=BOOK,trader="trend-rider",mint=WSOL,side="buy",usd=spent,tokens=got,price=px,pnl=0,sig=r["sig"],note=f"LIVE impact {r.get('impact',0):.2%}")
        try:s.kset("cash",s.balances()["usdc"])
        except Exception:pass
        s.signal(WSOL,"filled","live buy",spent)
        s.n.send(term("live buy",[kv("mode","REAL MONEY"+(" · TRIAL" if s.mode()=="trial" else "")),SEP,kv("spent",fusd(spent)),kv("got",f"{got:.5f} SOL"),kv("price",f"${px:.3f}"),
            kv("stop",f"${meta.get('stop',0):.3f}"),kv("tx",r["sig"][:10]+"…"),kv("time",f"{r.get('seconds',0):.0f}s")],("OK","🟢 LIVE BUY filled")))
        return pid
    # ---- exits: never blocked by arming, approvals or the signer's buy limits ---------------------------
    def exit(s,pid,reason,note):
        pos=s.position()
        if not pos or pos["id"]!=pid:return False
        f=s.kget("exit_fail") or {};now=time.time()
        if f.get("pid")==pid and now-f.get("last_try",0)<60:return False          # retry at most once a minute
        try:
            if s.db.kv_get("live:pending_tx"):
                s.resolve_pending();pos=s.position()
                if not pos or pos["id"]!=pid:return True
            b=s.balances();amt=min(pos["tokens"],b["sol"]-0.005)
            if amt<=0:raise LiveError("no SOL in the wallet to sell")
            r=s.swap(WSOL,USDC,int(amt*1e9),"sell",dict(pid=pid,amt=amt,reason=reason,note=note))
        except Exception as e:
            tries=(f.get("tries",0) if f.get("pid")==pid else 0)+1;first=f.get("first",now) if f.get("pid")==pid else now
            alert=now-f.get("last_alert",0)>=600 or tries==1
            s.kset("exit_fail",dict(pid=pid,tries=tries,first=first,last_try=now,last_alert=now if alert else f.get("last_alert",0),error=str(e)[:160],reason=reason))
            if alert:s.n.send(term("live sell",[kv("why",note),kv("tries",tries),kv("holding",f"{pos['tokens']:.5f} SOL")]+wrap(str(e)[:160])+[SEP,"position kept and watched;","retrying every minute"],("ERR","🔴 LIVE SELL failed")))
            return False
        s.kset("exit_fail",None);s._record_sell(pos,r,amt,reason,note);return True
    def _record_sell(s,pos,r,amt,reason,note):
        back=r["d_usdc"];pnl=back-pos["cost"]*min(1,amt/pos["tokens"]);px=back/amt if amt else 0;now=time.time();pid=pos["id"]
        s.db.x("update b_positions set tokens=0,cost=0,status='closed',closed=?,realized=realized+? where id=?",(now,pnl,pid))
        s.db.ins("b_fills",ts=now,book=BOOK,sig=r["sig"],trader="trend-rider",mint=WSOL,side="sell",status="filled",reason=reason,fill_ts=now,our_px=px,impact=r.get("impact"),size_usd=back,live=1,txsig=r["sig"])
        s.db.ins("b_trades",ts=now,book=BOOK,trader="trend-rider",mint=WSOL,side="sell",usd=back,tokens=amt,price=px,pnl=pnl,sig=r["sig"],note=f"LIVE {note}")
        try:s.kset("cash",s.balances()["usdc"])
        except Exception:pass
        s.n.send(term("live sell",[kv("mode","REAL MONEY"),SEP,kv("why",note),kv("sold",f"{amt:.5f} SOL"),kv("got",fusd(back)),kv("pnl",fusd(pnl,True)),kv("tx",r["sig"][:10]+"…")],("OK",("🟢" if pnl>=0 else "🔴")+" LIVE SELL")))
    # ---- approvals ---------------------------------------------------------------------------------------
    def needs_approval(s):
        return s.mode()=="trial" or (s.db.kv_get("live:approved_count") or 0)<s.c["approvals_required"]
    def propose(s,size,meta,sig_id):
        """Trend Rider wants a live buy. Either execute now, or ask for an Approve tap first."""
        why=s.blocked()
        if why:s.signal(WSOL,"refused",why,size,meta.get("features"));return None
        size=min(size,s.cap())
        if not s.needs_approval():return s.buy(size,meta,sig_id)
        aid=f"{random.SystemRandom().randrange(16**6):06x}";trial=s.mode()=="trial"
        s.db.kv_set("live:approval",dict(id=aid,ts=time.time(),exp=time.time()+s.c["approval_seconds"],size=size,meta=meta,sig=sig_id,status="pending"))
        f=meta.get("features",{});left=s.c["approvals_required"]-(s.db.kv_get("live:approved_count") or 0)
        s.n.send(term("live --approve",[kv("mode","TRIAL (not proven)" if trial else "ARMED"),kv("buy",f"{fusd(size)} of SOL"),kv("price",f"${f.get('close',0):.3f}"),kv("stop",f"${meta['stop']:.3f}"),
            kv("risk",f"{f.get('stop_pct',0):.1%} to the stop"),SEP,f"tap within {s.c['approval_seconds']//60} min or it is","skipped."+("" if trial else f" {left} approvals left.")],("WARN","❓ LIVE BUY needs your OK")),
            buttons=[[("✅ Approve",f"appr:{aid}:yes"),("❌ Reject",f"appr:{aid}:no")]])
        return "pending"
    def process_approval(s,price_now,valid_fn):
        a=s.db.kv_get("live:approval")
        if not a:return
        if a["status"]=="pending" and time.time()>a["exp"]:
            s.db.kv_set("live:approval",None);s.signal(WSOL,"refused","approval timed out",a["size"]);s.n.send(term("live --approve",["no answer in time, skipped"],("WARN","approval expired")));return
        if a["status"]=="no":s.db.kv_set("live:approval",None);s.signal(WSOL,"refused","you rejected it",a["size"]);return
        if a["status"]=="yes":
            s.db.kv_set("live:approval",None)
            if not valid_fn(price_now,a["meta"]):s.signal(WSOL,"refused","price moved too far while waiting",a["size"]);s.n.send(term("live --approve",["price moved too far while","waiting; skipped"],("WARN","not bought")));return
            pid=s.buy(a["size"],a["meta"],a["sig"])
            if pid:s.db.kv_set("live:approved_count",(s.db.kv_get("live:approved_count") or 0)+1)
    # ---- housekeeping (main loop, every ~30 s) ---------------------------------------------------------
    def tick(s):
        now=time.time()
        if s.db.kv_get("live:pending_tx") and now-s.last_pending>=30:
            s.last_pending=now
            try:r=s.resolve_pending();r and log.info("pending tx resolved: %s",r)
            except Exception as e:log.warning("pending tx still open: %s",e)
        a=s.db.kv_get("live:armed")
        if a and a.get("until") and now>=a["until"]:
            s.disarm(f"switched off after {s.c['arm_days']} days (send /live {a.get('mode','arm')} to renew)")
        elif a and a.get("until") and now>=a["until"]-86400 and not a.get("warned"):
            s.db.kv_set("live:armed",dict(a,warned=True));s.n.send(term("live",["live switches off in 24h.",f"renew: /live {a.get('mode','arm')}"],("WARN","⏰ live expires soon")))
        if s.db.kv_get("live:panic_sell"):s.panic_tick()
        w=s.db.kv_get("live:withdraw")
        if w:s.db.kv_set("live:withdraw",None);s.do_withdraw(test=(w=="test"))
        if not s.db.kv_get("live:armed") and not s.position():
            if now-s.last_ready>=600 and s.db.kv_get("live:readiness") is not None:
                s.last_ready=now
                try:s.checks()
                except Exception as e:log.warning("readiness: %s",e)
            return
        if now-s.last_rec>=60:s.last_rec=now;s.reconcile()
        if now-s.last_mark>=300:s.last_mark=now;s.mark()
        if now-s.last_ready>=600:
            s.last_ready=now
            try:s.checks()
            except Exception as e:log.warning("readiness: %s",e)
    def panic_tick(s):
        pos=s.position()
        if not pos:
            s.db.kv_set("live:panic_sell",None);s.n.send(term("panic",["live position: none left","paper books: exits continue"],("OK","🛑 panic: live is flat")));return
        if s.exit(pos["id"],"panic","/panic: sold at market"):
            s.db.kv_set("live:panic_sell",None);s.n.send(term("panic",["live position sold.","no real money is in SOL."],("OK","🛑 panic sell done")))
    def reconcile(s):
        """Our records vs the real wallet, after settling any pending transaction. A gap switches live off and alerts."""
        if s.db.kv_get("live:pending_tx"):
            try:s.resolve_pending()
            except Exception as e:log.warning("reconcile: pending tx still unknown (%s), judging later",e);return
        try:b=s.balances()
        except Exception as e:log.warning("reconcile: %s",e);return
        rec=s.kget("cash");why=None
        if rec is not None and b["usdc"]<rec-max(0.05,rec*0.01):why=f"USDC: expected {fusd(rec)}, wallet {fusd(b['usdc'])}"
        elif rec is not None and b["usdc"]>rec+0.05 and not s.position():s.kset("cash",b["usdc"])
        pos=s.position()
        if pos and b["sol"]<pos["tokens"]*0.98:why=f"SOL: position {pos['tokens']:.5f}, wallet {b['sol']:.5f}"
        if why and not s.db.kv_get("live:mismatch"):
            s.db.kv_set("live:mismatch",dict(ts=time.time(),detail=why));s.disarm("wallet mismatch",quiet=True)
            s.n.send(term("live --reconcile",wrap(why)+[SEP,"live switched OFF. Nothing","new is bought. Review the","wallet, then /live clear"],("ERR","🔴 WALLET MISMATCH")))
    def mark(s):
        try:b=s.balances();eq=s.equity(b)
        except Exception as e:log.warning("live mark: %s",e);return
        s.kset("last_equity",eq);s.db.ins("b_equity",ts=time.time(),book=BOOK,equity=eq,cash=b["usdc"])
        peak=max(s.kget("peak") or eq,eq);s.kset("peak",peak)
        day=dt.datetime.utcnow().date().isoformat()
        if (s.kget("day_start") or {}).get("day")!=day:s.kset("day_start",{"day":day,"eq":eq})
        wk=dt.datetime.utcnow().strftime("%G-W%V")
        if (s.kget("week_start") or {}).get("week")!=wk:s.kset("week_start",{"week":wk,"eq":eq})
        if eq<peak*(1-s.c["max_drawdown_pct"]) and not s.kget("shutdown"):
            s.kset("shutdown",True);s.disarm("drawdown switch",quiet=True)
            s.n.send(term("live --safeguard",[kv("peak",fusd(peak)),kv("equity",fusd(eq)),SEP,"live switched OFF. Trend","Rider continues on paper.","review, then /live clear"],("ERR","🔴 LIVE DRAWDOWN SWITCH")))
    # ---- withdraw ----------------------------------------------------------------------------------------
    def do_withdraw(s,test=False):
        rows=[]
        try:
            if s.db.kv_get("live:pending_tx"):s.resolve_pending()
            b=s.balances()
            if test:
                if b["usdc"]>=s.c["test_withdraw_usdc"]:kind,amt="usdc",int(s.c["test_withdraw_usdc"]*1e6)
                elif b["sol"]>=s.c["test_withdraw_sol"]+0.004:kind,amt="sol",int(s.c["test_withdraw_sol"]*1e9)
                else:raise LiveError(f"fund the wallet first (needs ${s.c['test_withdraw_usdc']:.0f} USDC or {s.c['test_withdraw_sol']+0.004:.3f} SOL)")
                r=signer("withdraw",kind=kind,amount=amt);sig=s.send(r["tx"],r.get("last_valid"))
                what=f"{amt/1e6:.2f} USDC" if kind=="usdc" else f"{amt/1e9:.4f} SOL"
                s.db.kv_set("live:test_withdraw",dict(ts=time.time(),kind=kind,amount=amt,sig=sig,home=r["home"],confirmed=False))
                if kind=="usdc" and s.kget("cash") is not None:s.kset("cash",max(0,s.kget("cash")-amt/1e6))
                s.n.send(term("live --testwithdraw",[kv("sent",what),kv("to",short(r["home"])),kv("tx",sig[:10]+"…"),SEP,"open Phantom and check it","arrived. Then send:","/live received"],("OK","💸 test withdrawal sent")));return
            if s.position():raise LiveError("close the position first (/panic sells it)")
            if b["usdc"]>0.01:
                r=signer("withdraw",kind="usdc");s.send(r["tx"],r.get("last_valid"));rows.append(f"sent {r['amount']/1e6:.2f} USDC home")
            r=signer("withdraw",kind="sol");s.send(r["tx"],r.get("last_valid"));rows.append(f"sent {r['amount']/1e9:.5f} SOL home")
            s.kset("cash",0);s.n.send(term("live --withdraw",rows+[kv("to",short(r["home"]))],("OK","💸 withdrawn to your wallet")))
        except Exception as e:
            s.n.send(term("live --withdraw",rows+wrap(str(e)[:200]),("ERR","withdraw stopped")))
    # ---- self-test: the whole chain on real Jupiter transactions + the signer's refusals, no money moves --
    def selftest(s):
        rows=[];ok=True;full=True
        def step(good,text):
            nonlocal ok;rows.extend(wrap(text,("✓ " if good else "✗ ")));ok=ok and good
        try:st=signer("status",timeout=10);step(True,"signer running");step(True,"wallet "+short(st["wallet"]))
        except LiveError as e:step(False,str(e));s.db.kv_set("live:selftest",dict(ts=time.time(),ok=False,full=False));return term("live --selftest",rows,("ERR","self-test failed"))
        step(bool(st.get("home")),"home set" if st.get("home") else "home not set (/sethome)")
        try:b=s.balances();step(True,f"RPC ok: {b['sol']:.4f} SOL, ${b['usdc']:.2f} USDC")
        except Exception as e:step(False,f"RPC failed ({type(e).__name__})");b=dict(sol=0,usdc=0)
        for side,inm,outm,amt,funded in (("buy",USDC,WSOL,1_000_000,b["usdc"]>=1),("sell",WSOL,USDC,1_000_000,b["sol"]>=s.c["sol_reserve"]+0.002)):
            try:
                q=jup_quote_full(inm,outm,amt,s.c["slippage_bps"]);tx,lv=jup_swap_tx(q,st["wallet"],s.c["priority_max_lamports"])
                signer("check_static",tx=tx);step(True,f"{side}: real Jupiter tx passes the instruction checks")
                if funded:
                    r=signer("check_swap",tx=tx);step(True,f"{side}: simulation ok, limits ok")
                else:full=False;rows.extend(wrap(f"{side}: simulation skipped (wallet not funded yet)","· "))
            except Exception as e:step(False,f"{side}: {str(e)[:90]}")
        # the signer must REFUSE these (built here, never sent)
        bh=bytes(32);me=W.pk(st["wallet"]);stranger=bytes(range(1,33))
        memo=W.pk("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")
        bad=(("SOL sent to a stranger",W.sol_transfer(me,stranger,1000,bh)),
             ("unknown program",W.legacy_tx([me,memo],1,0,1,bh,[(1,[],b"zetrax")])),
             ("someone else pays",W.sol_transfer(stranger,me,1000,bh)))
        for name,raw in bad:
            try:signer("check_static",tx=base64.b64encode(raw).decode());step(False,f"signer ACCEPTED '{name}'")
            except LiveError as e:step(True,f"refuses {name}")
        s.db.kv_set("live:selftest",dict(ts=time.time(),ok=ok,full=ok and full))
        rows+=[SEP,"no money moved"]+([] if full else ["fund the wallet and run it","again for the full test"])
        return term("live --selftest",rows,("OK","✅ chain works"+("" if full else " (unfunded part)")) if ok else ("WARN","self-test found problems"))
    def status_rows(s):
        rows=[]
        try:st=signer("status",timeout=10)
        except LiveError as e:return wrap(str(e))
        rows+=[kv("wallet",st["wallet"][:6]+"…"+st["wallet"][-4:]),kv("home",short(st.get("home")) if st.get("home") else "not set")]
        try:b=s.balances();rows+=[kv("SOL",f"{b['sol']:.4f}"),kv("USDC",f"${b['usdc']:.2f}")]
        except Exception as e:rows.append(kv("balance",f"failed ({type(e).__name__})"))
        L=st["limits"];a=s.arm_info();left=max(0,s.c["approvals_required"]-(s.db.kv_get("live:approved_count") or 0))
        rows+=[SEP,kv("live",("🔓 TRIAL" if a.get("mode")=="trial" else "🔓 ARMED")+" until "+time.strftime("%d %b",time.gmtime(a["until"])) if a else "🔓 available, off"),
               kv("approve","every buy" if a and a.get("mode")=="trial" else f"{left} left"),
               kv("today",f"{st['today_swaps']} buys ${st['today_usd']:.0f}, {st.get('today_sells',0)} sells"),
               kv("limits",f"buy ${L['max_swap_usd']:.0f}/swap ${L['max_day_usd']:.0f}/day"),kv("exits","never blocked by limits")]
        pos=s.position()
        if pos:rows+=[SEP,kv("position",f"{pos['tokens']:.4f} SOL"),kv("cost",fusd(pos["cost"])),kv("stop",f"${pos['meta'].get('stop',0):.3f}")]
        p=s.db.kv_get("live:pending_tx")
        if p:rows+=[SEP,f"[WARN] {p['kind']} tx {p['sig'][:8]}… being","confirmed"]
        f=s.kget("exit_fail")
        if f and pos:rows+=[SEP,f"[ERR] sell failing ({f['tries']} tries)"]+wrap(f.get("error",""))
        if s.db.kv_get("live:panic_sell"):rows+=[SEP,"[ERR] PANIC: selling the","live position"]
        mm=s.db.kv_get("live:mismatch")
        if mm:rows+=[SEP,"[ERR] wallet mismatch:"]+wrap(mm.get("detail",""))
        return rows
