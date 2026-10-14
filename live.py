"""Real-money book 'trend-live' (Trend Rider only). Built but OFF: nothing trades until ALL of these hold:
  the signer service is running and holds the wallet; you set a home address (/sethome); the wallet is funded;
  the last /backtest passed gate A; the Trend Rider paper book has run 7+ days; and you armed it with /live arm + code.
Any restart, /update, /panic or the 6% drawdown switch disarms it. The first `approvals_required` live buys need your
Approve tap in Telegram. Sells/exits never wait for approval. The bot never sees the private key: it asks the signer
(signer/signer.py), which only signs SOL<->USDC swaps inside the wallet within hard limits, or transfers to home."""
import os,json,time,socket,random,logging,datetime as dt,urllib.request,urllib.parse
from tgfmt import term,kv,usd as fusd,SEP,esc,wrap
def ticks(items):
    """[(ok, text)] -> wrapped '✓ ' / '✗ ' rows."""
    return sum((wrap(t,("✓ " if ok else "✗ ")) for ok,t in items),[])
from chain import USDC,WSOL,JUP,_jh,jup_price
import veto
log=logging.getLogger("bot");BOOK="trend-live"
SOCK=os.environ.get("SIGNER_SOCK","/run/copybot-signer/signer.sock")
DEF=dict(approvals_required=10,slippage_bps=100,priority_max_lamports=100000,sol_reserve=0.02,min_usdc_to_arm=5.0,
         min_paper_days=7,daily_loss_limit_pct=0.02,weekly_loss_limit_pct=0.04,max_drawdown_pct=0.06,approval_seconds=120)
class LiveError(Exception):pass
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
class Live:
    def __init__(s,db,hel,notify,cfg):
        s.db,s.hel,s.n,s.cfg=db,hel,notify,cfg;s.c=dict(DEF,**(cfg.get("live") or {}));s._wallet=None;s.last_rec=0;s.last_mark=0
    # ---- state ----------------------------------------------------------------------------------
    def K(s,k):return f"{BOOK}:{k}"
    def kget(s,k,d=None):return s.db.kv_get(s.K(k),d)
    def kset(s,k,v):s.db.kv_set(s.K(k),v)
    def wallet(s):
        if not s._wallet:s._wallet=signer("pubkey",timeout=10)["wallet"]
        return s._wallet
    def armed(s):
        a=s.db.kv_get("live:armed")
        return bool(a) and a.get("boot")==s.db.kv_get("boot_id") and not s.db.kv_get("panic") and not s.kget("shutdown")
    def disarm(s,why,quiet=False):
        was=s.db.kv_get("live:armed");s.db.kv_set("live:armed",None);s.db.kv_set("live:approval",None)
        if was and not quiet:s.n.send(term("live --disarm",[kv("reason",why),SEP,"real-money buys are OFF.","open positions still exit","by their rules. /live arm","to turn on again."],("WARN","🔒 LIVE DISARMED")))
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
        if not s.armed():return "live is not armed"
        if s.db.kv_get("panic"):return "panic stop active"
        if s.kget("shutdown"):return "6% drawdown switch tripped"
        day=dt.datetime.utcnow().date().isoformat();ds=s.kget("day_start") or {}
        if ds.get("day")==day and s.kget("last_equity",ds["eq"])<ds["eq"]*(1-s.c["daily_loss_limit_pct"]):return "daily loss limit hit"
        ws=s.kget("week_start") or {}
        if ws.get("week")==dt.datetime.utcnow().strftime("%G-W%V") and s.kget("last_equity",ws["eq"])<ws["eq"]*(1-s.c["weekly_loss_limit_pct"]):return "weekly loss limit hit"
        return None
    def signal(s,mint,decision,reason,size=None,features=None):
        try:s.db.ins("b_signals",ts=time.time(),book=BOOK,source="trend-rider",mint=mint,side="buy",decision=decision,reason=reason,size_usd=size,features=json.dumps(features or {}))
        except Exception as e:log.warning("signal log: %s",e)
    # ---- arming ------------------------------------------------------------------------------------
    def preconditions(s):
        """-> list of (ok, text). Every one must pass before /live arm works."""
        out=[]
        try:st=signer("status",timeout=10);out.append((True,"signer running"))
        except LiveError as e:return [(False,str(e))]
        out.append((bool(st.get("home")),"home address set" if st.get("home") else "home not set: /sethome ADDR"))
        try:b=s.balances()
        except Exception as e:return out+[(False,f"balance check failed ({type(e).__name__})")]
        out.append((b["usdc"]>=s.c["min_usdc_to_arm"],f"wallet USDC ${b['usdc']:.2f} (need ${s.c['min_usdc_to_arm']:.0f}+)"))
        out.append((b["sol"]>=s.c["sol_reserve"],f"fee SOL {b['sol']:.4f} (need {s.c['sol_reserve']})"))
        bt=s.db.kv_get("backtest:last") or {}
        out.append((bool(bt.get("passed")),"backtest passed gate A" if bt.get("passed") else "backtest not passed (/backtest)"))
        started=s.db.kv_get("trend:started");days=(time.time()-started)/86400 if started else 0
        out.append((days>=s.c["min_paper_days"],f"paper {days:.1f} days (need {s.c['min_paper_days']})"))
        out.append((not s.db.kv_get("panic"),"no panic stop" if not s.db.kv_get("panic") else "panic stop is on (/panic off)"))
        return out
    def arm(s,code=None):
        if code is None:
            pre=s.preconditions();rows=ticks(pre)
            if not all(ok for ok,_ in pre):return term("live --arm",rows+[SEP,"fix the ✗ items first"],("WARN","cannot arm yet"))
            c=f"{random.SystemRandom().randrange(10**6):06d}";s.db.kv_set("live:arm_code",dict(code=c,exp=time.time()+120))
            return term("live --arm",rows+[SEP,"real money will trade.",f"confirm within 2 min:",f"/live arm {c}"],("WARN","⚠ confirm with code"))
        a=s.db.kv_get("live:arm_code") or {}
        if not a or time.time()>a.get("exp",0) or code!=a.get("code"):return term("live --arm",["code wrong or expired","send /live arm again"],("ERR","not armed"))
        s.db.kv_set("live:arm_code",None);pre=s.preconditions()
        if not all(ok for ok,_ in pre):return term("live --arm",["a check failed meanwhile:"]+ticks(pre),("ERR","not armed"))
        b=s.balances();eq=s.equity(b)
        if s.kget("cash") is None:s.kset("start",eq);s.kset("peak",eq)
        s.kset("cash",b["usdc"]);s.kset("last_equity",eq)
        s.kset("limits",dict(start=s.kget("start",eq),max_pos=1,daily=s.c["daily_loss_limit_pct"],max_dd=s.c["max_drawdown_pct"],enabled=True))
        s.db.kv_set("live:armed",dict(ts=time.time(),boot=s.db.kv_get("boot_id")))
        left=max(0,s.c["approvals_required"]-(s.db.kv_get("live:approved_count") or 0))
        return term("live --arm",[kv("wallet",s.wallet()[:4]+"…"+s.wallet()[-4:]),kv("equity",fusd(eq)),kv("approvals",f"{left} buys need your tap"),SEP,
            "Trend Rider now trades","real money. Disarm: /live","disarm · stop all: /panic"],("OK","🔓 LIVE ARMED"))
    # ---- execution -------------------------------------------------------------------------------
    def send(s,tx_b64,last_valid=None,timeout=90):
        """Send a signed tx and wait for confirmation. Re-broadcasts the SAME signed tx (same signature, so it can
        only land once) until it confirms or its blockhash expires."""
        sig=s.hel.rpc("sendTransaction",[tx_b64,{"encoding":"base64","skipPreflight":True,"maxRetries":0}]);t0=time.time()
        while time.time()-t0<timeout:
            time.sleep(2)
            st=s.hel.rpc("getSignatureStatuses",[[sig],{"searchTransactionHistory":False}])["value"][0]
            if st and st.get("confirmationStatus") in("confirmed","finalized"):
                if st.get("err"):raise LiveError(f"transaction failed on chain: {json.dumps(st['err'])[:100]}")
                return sig
            if last_valid and s.hel.rpc("getBlockHeight",[{"commitment":"confirmed"}])>last_valid:
                st=s.hel.rpc("getSignatureStatuses",[[sig],{"searchTransactionHistory":True}])["value"][0]
                if st and not st.get("err"):return sig
                raise LiveError("transaction expired without landing (nothing changed)")
            try:s.hel.rpc("sendTransaction",[tx_b64,{"encoding":"base64","skipPreflight":True,"maxRetries":0}])
            except Exception:pass
        raise LiveError(f"no confirmation after {timeout}s; check {sig[:12]}… before retrying")
    def swap(s,inm,outm,amount_raw):
        """Quote -> Jupiter tx -> signer checks + signs -> send -> confirm -> measured balance change."""
        b0=s.balances();q=jup_quote_full(inm,outm,amount_raw,s.c["slippage_bps"]);imp=float(q.get("priceImpactPct") or 0)
        if imp>0.005:raise LiveError(f"price impact {imp:.2%} too high")
        tx,lv=jup_swap_tx(q,s.wallet(),s.c["priority_max_lamports"]);r=signer("sign_swap",tx=tx);t0=time.time()
        sig=s.send(r["tx"],lv);time.sleep(2);b1=s.balances()
        return dict(sig=sig,d_sol=b1["sol"]-b0["sol"],d_usdc=b1["usdc"]-b0["usdc"],impact=imp,seconds=time.time()-t0,checks=r.get("steps"))
    def buy(s,size,meta,sig_id):
        """Executes a live buy now (approval already handled). -> (pid, price) or None."""
        why=s.blocked()
        if why:s.signal(WSOL,"refused",why,size);return None
        b=s.balances();size=min(size,b["usdc"]-0.05,signer("status",timeout=10)["limits"]["max_swap_usd"])
        if size<1:s.signal(WSOL,"refused",f"live size ${size:.2f} too small",size);return None
        try:r=s.swap(USDC,WSOL,int(size*1e6))
        except Exception as e:
            s.signal(WSOL,"refused",f"live buy failed: {e}",size)
            s.n.send(term("live buy",wrap(str(e)[:200])+[SEP,"nothing bought"],("ERR","🔴 LIVE BUY failed")));return None
        got=r["d_sol"];spent=-r["d_usdc"]
        if got<=0 or spent<=0:
            s.n.send(term("live buy",[kv("SOL change",f"{got:+.6f}",10),kv("USDC change",f"{-spent:+.4f}",10),SEP,"check the wallet; live","trading disarmed"],("ERR","🔴 unexpected result")));s.disarm("unexpected buy result",quiet=True);return None
        px=spent/got
        pid=s.db.ins("b_positions",book=BOOK,trader="trend-rider",mint=WSOL,tokens=got,cost=spent,opened=time.time(),status="open",open_sig=r["sig"],meta=json.dumps(meta))
        s.db.ins("b_fills",ts=time.time(),book=BOOK,sig=r["sig"],trader="trend-rider",mint=WSOL,side="buy",status="filled",reason="trend entry",fill_ts=time.time(),
                 quote_px=meta.get("signal_ref"),our_px=px,impact=r["impact"],size_usd=spent,live=1,txsig=r["sig"])
        s.db.ins("b_trades",ts=time.time(),book=BOOK,trader="trend-rider",mint=WSOL,side="buy",usd=spent,tokens=got,price=px,pnl=0,sig=r["sig"],note=f"LIVE impact {r['impact']:.2%}")
        s.kset("cash",s.balances()["usdc"]);s.signal(WSOL,"filled","live buy",spent)
        s.n.send(term("live buy",[kv("mode","REAL MONEY"),SEP,kv("spent",fusd(spent)),kv("got",f"{got:.5f} SOL"),kv("price",f"${px:.3f}"),kv("tx",r["sig"][:10]+"…"),kv("time",f"{r['seconds']:.0f}s")],("OK","🟢 LIVE BUY filled")))
        return pid,px
    def exit(s,pid,reason,note):
        pos=s.position()
        if not pos or pos["id"]!=pid:return False
        b=s.balances();amt=min(pos["tokens"],b["sol"]-0.005)
        if amt<=0:s.n.send(term("live sell",["no SOL to sell?",SEP,"check the wallet"],("ERR","🔴 LIVE SELL problem")));return False
        try:r=s.swap(WSOL,USDC,int(amt*1e9))
        except Exception as e:
            s.n.send(term("live sell",[kv("why",note)]+wrap(str(e)[:160])+[SEP,"retrying on the next check"],("ERR","🔴 LIVE SELL failed")));return False
        back=r["d_usdc"];pnl=back-pos["cost"]*(amt/pos["tokens"]);px=back/amt if amt else 0;now=time.time()
        s.db.x("update b_positions set tokens=0,cost=0,status='closed',closed=?,realized=realized+? where id=?",(now,pnl,pid))
        s.db.ins("b_fills",ts=now,book=BOOK,sig=r["sig"],trader="trend-rider",mint=WSOL,side="sell",status="filled",reason=reason,fill_ts=now,our_px=px,impact=r["impact"],size_usd=back,live=1,txsig=r["sig"])
        s.db.ins("b_trades",ts=now,book=BOOK,trader="trend-rider",mint=WSOL,side="sell",usd=back,tokens=amt,price=px,pnl=pnl,sig=r["sig"],note=f"LIVE {note}")
        s.kset("cash",s.balances()["usdc"])
        s.n.send(term("live sell",[kv("mode","REAL MONEY"),SEP,kv("why",note),kv("sold",f"{amt:.5f} SOL"),kv("got",fusd(back)),kv("pnl",fusd(pnl,True)),kv("tx",r["sig"][:10]+"…")],("OK",("🟢" if pnl>=0 else "🔴")+" LIVE SELL")))
        return True
    # ---- approvals (first N live buys) --------------------------------------------------------------
    def propose(s,size,meta,sig_id,plan_fn):
        """Trend Rider wants a live buy. Either execute now, or ask for an Approve tap first."""
        why=s.blocked()
        if why:s.signal(WSOL,"refused",why,size,meta.get("features"));return None
        left=s.c["approvals_required"]-(s.db.kv_get("live:approved_count") or 0)
        if left<=0:return s._execute(size,meta,sig_id,plan_fn)
        aid=f"{random.SystemRandom().randrange(16**6):06x}"
        s.db.kv_set("live:approval",dict(id=aid,ts=time.time(),exp=time.time()+s.c["approval_seconds"],size=size,meta=meta,sig=sig_id,status="pending"))
        f=meta.get("features",{})
        s.n.send(term("live --approve",[kv("buy",f"{fusd(size)} of SOL"),kv("price",f"${f.get('close',0):.3f}"),kv("stop",f"${meta['stop']:.3f}"),kv("risk",f"{f.get('stop_pct',0):.2%}"),
            SEP,f"tap within {s.c['approval_seconds']//60} min or it is",f"skipped. {left} approvals left."],("WARN","❓ LIVE BUY needs your OK")),
            buttons=[[("✅ Approve",f"appr:{aid}:yes"),("❌ Reject",f"appr:{aid}:no")]])
        return "pending"
    def _execute(s,size,meta,sig_id,plan_fn):
        r=s.buy(size,meta,sig_id)
        if not r:return None
        pid,px=r;plan=plan_fn(px,meta);s.db.x("update b_positions set meta=? where id=?",(json.dumps(dict(meta,**plan)),pid));return pid
    def process_approval(s,price_now,plan_fn,valid_fn):
        a=s.db.kv_get("live:approval")
        if not a:return
        if a["status"]=="pending" and time.time()>a["exp"]:
            s.db.kv_set("live:approval",None);s.signal(WSOL,"refused","approval timed out",a["size"]);s.n.send(term("live --approve",["no answer in time, skipped"],("WARN","approval expired")));return
        if a["status"]=="no":s.db.kv_set("live:approval",None);s.signal(WSOL,"refused","you rejected it",a["size"]);return
        if a["status"]=="yes":
            s.db.kv_set("live:approval",None)
            if not valid_fn(price_now,a["meta"]):s.signal(WSOL,"refused","price moved too far while waiting",a["size"]);s.n.send(term("live --approve",["price moved too far while","waiting; skipped"],("WARN","not bought")));return
            pid=s._execute(a["size"],a["meta"],a["sig"],plan_fn)
            if pid:s.db.kv_set("live:approved_count",(s.db.kv_get("live:approved_count") or 0)+1)
    # ---- housekeeping (main loop, every ~30 s) ---------------------------------------------------------
    def tick(s,exit_fn=None):
        if s.db.kv_get("live:panic_sell"):
            s.db.kv_set("live:panic_sell",None);pos=s.position()
            if pos:s.exit(pos["id"],"panic","/panic: sold at market")
        w=s.db.kv_get("live:withdraw")
        if w:s.db.kv_set("live:withdraw",None);s.do_withdraw()
        now=time.time()
        if not s.db.kv_get("live:armed") and not s.position():return
        if now-s.last_rec>=60:s.last_rec=now;s.reconcile()
        if now-s.last_mark>=300:s.last_mark=now;s.mark()
    def reconcile(s):
        """Our records vs the real wallet. A gap disarms and alerts; a deposit is welcomed."""
        try:b=s.balances()
        except Exception as e:log.warning("reconcile: %s",e);return
        rec=s.kget("cash")
        if rec is not None and b["usdc"]<rec-max(0.05,rec*0.01):
            s.n.send(term("live --reconcile",[kv("expected",fusd(rec)),kv("wallet",fusd(b["usdc"])),SEP,"USDC is missing. Live","trading disarmed."],("ERR","🔴 WALLET MISMATCH")));s.disarm("wallet mismatch",quiet=True)
        elif rec is not None and b["usdc"]>rec+0.05 and not s.position():s.kset("cash",b["usdc"])
        pos=s.position()
        if pos and b["sol"]<pos["tokens"]*0.98:
            s.n.send(term("live --reconcile",[kv("position",f"{pos['tokens']:.5f} SOL"),kv("wallet",f"{b['sol']:.5f} SOL"),SEP,"SOL is missing. Live","trading disarmed."],("ERR","🔴 WALLET MISMATCH")));s.disarm("wallet mismatch",quiet=True)
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
            s.kset("shutdown",True);s.disarm("6% drawdown switch",quiet=True)
            s.n.send(term("live --safeguard",[kv("peak",fusd(peak)),kv("equity",fusd(eq)),SEP,"Trend Rider goes back to","paper until you review it."],("ERR","🔴 LIVE DRAWDOWN SWITCH")))
    # ---- withdraw ----------------------------------------------------------------------------------------
    def do_withdraw(s):
        rows=[]
        try:
            if s.position():raise LiveError("close the position first (/panic sells it)")
            b=s.balances()
            if b["usdc"]>0.01:
                try:r=signer("withdraw",kind="usdc");s.send(r["tx"],r.get("last_valid"));rows.append(f"sent {r['amount']/1e6:.2f} USDC home")
                except LiveError as e:
                    if "no USDC account" not in str(e):raise
                    left=b["usdc"]
                    while left>0.1:
                        part=min(left,19.0);s.swap(USDC,WSOL,int(part*1e6));left-=part
                    rows+=["swapped USDC to SOL first","(home has no USDC account)"]
            r=signer("withdraw",kind="sol");s.send(r["tx"],r.get("last_valid"));rows.append(f"sent {r['amount']/1e9:.5f} SOL home")
            s.kset("cash",0);s.n.send(term("live --withdraw",rows+[kv("to",r["home"][:4]+"…"+r["home"][-4:])],("OK","💸 withdrawn to your wallet")))
        except Exception as e:
            s.n.send(term("live --withdraw",rows+wrap(str(e)[:200]),("ERR","withdraw stopped")))
    # ---- self-test: proves the whole chain on a real Jupiter swap, without money ------------------------
    def selftest(s):
        rows=[];ok=True
        def step(good,text):
            nonlocal ok;rows.extend(wrap(text,("✓ " if good else "✗ ")));ok=ok and good
        try:st=signer("status",timeout=10);step(True,"signer running");step(True,"wallet "+st["wallet"][:4]+"…"+st["wallet"][-4:])
        except LiveError as e:step(False,str(e));return term("live --selftest",rows,("ERR","self-test failed"))
        step(bool(st.get("home")),"home set" if st.get("home") else "home not set (/sethome)")
        try:b=s.balances();step(True,f"RPC ok: {b['sol']:.4f} SOL, ${b['usdc']:.2f} USDC")
        except Exception as e:step(False,f"RPC failed ({type(e).__name__})");b=dict(sol=0,usdc=0)
        try:
            q=jup_quote_full(USDC,WSOL,1_000_000,s.c["slippage_bps"]);step(True,f"quote: $1 -> {int(q['outAmount'])/1e9:.5f} SOL")
            tx,lv=jup_swap_tx(q,st["wallet"],s.c["priority_max_lamports"]);step(True,"Jupiter built a swap tx")
            try:
                r=signer("check_swap",tx=tx)
                for x in r.get("steps",[]):step(True,x)
            except LiveError as e:
                msg=str(e);funded=b["usdc"]>=1
                if "simulation failed" in msg and not funded:rows.append("✓ guard checks passed; the");rows.append("  simulation failed as");rows.append("  expected: wallet unfunded")
                else:step(False,"signer refused: "+msg)
        except Exception as e:step(False,f"Jupiter: {type(e).__name__} {str(e)[:60]}")
        rows+=[SEP,"no money moved"]
        return term("live --selftest",rows,("OK","✅ chain works") if ok else ("WARN","self-test found problems"))
    def status_rows(s):
        rows=[]
        try:st=signer("status",timeout=10)
        except LiveError as e:return wrap(str(e))
        rows+=[kv("wallet",st["wallet"][:6]+"…"+st["wallet"][-4:]),kv("home",(st["home"][:4]+"…"+st["home"][-4:]) if st.get("home") else "not set")]
        try:b=s.balances();rows+=[kv("SOL",f"{b['sol']:.4f}"),kv("USDC",f"${b['usdc']:.2f}")]
        except Exception as e:rows.append(kv("balance",f"failed ({type(e).__name__})"))
        L=st["limits"];left=max(0,s.c["approvals_required"]-(s.db.kv_get("live:approved_count") or 0))
        rows+=[SEP,kv("live","🔓 ARMED" if s.armed() else "🔒 off"),kv("approvals",f"{left} left"),kv("today",f"${st['today_usd']:.0f} / {st['today_swaps']} swaps"),
               kv("limits",f"${L['max_swap_usd']:.0f}/swap ${L['max_day_usd']:.0f}/day")]
        pos=s.position()
        if pos:rows+=[SEP,kv("position",f"{pos['tokens']:.4f} SOL"),kv("cost",fusd(pos["cost"]))]
        return rows
