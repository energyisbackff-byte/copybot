"""Real money for the coin books: Narrative Rider, Trend basket, copy and consensus (one real book, 'spot-live').
Built, and OFF until YOU switch a book on. You can switch it off any time:
  /real                      every real-money switch in one place (Trend Rider, perps and these four books)
  /real on BOOK              normal: every SAFETY check + that book's paper bar (30+ closed trades, PF >= 1.3 with fees
                             counted 3 times). The first `approvals_required` real buys still wait for your tap.
  /real trial BOOK           your override while the bar is not reached yet: SAFETY checks only, at most
                             `trial_max_usd` per buy, EVERY buy waits for your tap
  /real off [BOOK|all]       off. Open real positions keep following their paper position's exits.
A switch also goes off by itself after `arm_days`, on any restart or /update, on /panic, a wallet mismatch and the
drawdown switch. Then nothing new is bought; sells continue.
HOW IT TRADES: it mirrors the paper book. The paper book decides (every gate, the narrative, the risk charter); when it
buys, the real wallet buys the same coin with the same share of its money (capped); when the paper book sells part or
all (stop, first target, trailing stop, time-stop, trader sold), the real wallet sells the same share. Sells are never
blocked: not by the switch, approvals, limits, missing prices or /panic (which sells everything).
THE WALLET: its own wallet and guard (signer.py in spot mode, service copybot-signer-spot). The key never reaches the
bot. The guard signs only USDC <-> coin swaps inside its wallet, buys only of coins without mint/freeze authority or
dangerous Token-2022 extensions, within its own caps ($10 per buy, $50 a day by default), or transfers to your home
address (your Phantom, set once with /wallet link). Raising the caps is only possible on the server console."""
import os,json,time,random,base64,logging,threading,datetime as dt
from tgfmt import term,kv,usd as fusd,SEP,wrap,esc
import live as LV
from live import LiveError,Unknown,jup_quote_full,jup_swap_tx,tx_sig,short,ticks
from chain import USDC,jup_prices,decimals
log=logging.getLogger("bot");BOOK="spot-live";P="spot-live:"
BOOKS=("narrative","trend-basket","copy","consensus")
ALIAS={"narrative":"narrative","rider":"narrative","narrative-rider":"narrative","basket":"trend-basket","trend-basket":"trend-basket",
       "copy":"copy","consensus":"consensus"}
NAMES={"narrative":"Narrative Rider","trend-basket":"Trend basket","copy":"copy","consensus":"consensus"}
SOCK=os.environ.get("SIGNER_SPOT_SOCK","/run/copybot-signer-spot/signer.sock")
TOKEN="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";TOKEN22="TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
DEF=dict(approvals_required=10,slippage_bps=300,priority_max_lamports=100000,sol_reserve=0.02,min_usdc_to_arm=5.0,daily_loss_limit_pct=0.03,
         max_drawdown_pct=0.10,approval_seconds=90,arm_days=7,trial_max_usd=5.0,max_impact=0.02,min_buy_usd=1.0,max_send_seconds=120,
         selftest_max_age_h=24,test_withdraw_usdc=1.0,test_withdraw_sol=0.002,check_seconds=3,
         max_buy_age_s={"copy":90,"consensus":90,"narrative":600,"trend-basket":21600},
         selftest_coin="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")    # BONK: a liquid coin for the self-test (nothing is bought)
def conf(cfg):
    c=dict(DEF,**((cfg or {}).get("spot_live") or {}));c["max_buy_age_s"]=dict(DEF["max_buy_age_s"],**(c.get("max_buy_age_s") or {}));return c
def signer(op,timeout=40,**k):return LV.signer(op,timeout=timeout,sock=SOCK,**k)
def code6():return f"{random.SystemRandom().randrange(10**6):06d}"
# ---- the switches (read by the paper hook from any thread) -------------------------------------------------------
def arm_map(db):return db.kv_get(P+"armed") or {}
def book_on(db,book):
    a=arm_map(db).get(book)
    if not a or a.get("boot")!=db.kv_get("boot_id") or time.time()>=a.get("until",0):return None
    if db.kv_get("panic") or db.kv_get(P+"mismatch") or db.kv_get(P+"shutdown"):return None
    return a
def _open_for(db,book,ppid):
    r=db.q("select id,mint,tokens,cost,opened,meta from b_positions where book=? and status='open' and trader=? and json_extract(meta,'$.paper_pid')=?",(BOOK,book,ppid))
    return dict(id=r[0][0],book=book,mint=r[0][1],tokens=r[0][2],cost=r[0][3],opened=r[0][4],meta=json.loads(r[0][5] or "{}")) if r else None
def on_paper(db,book,kind,pid,mint,size=None,equity=None,frac=None,reason=None,note=None):
    """Called by the paper book right after a paper buy or sell (any thread). Only queues work for the real-money
    worker; never raises and never slows the paper book."""
    try:
        if book not in BOOKS:return
        now=time.time()
        if kind=="buy":
            if not book_on(db,book):return
            db.ins("spot_intents",ts=now,book=book,kind="buy",mint=mint,paper_pid=pid,frac=(size/equity) if size and equity else 0.0,reason="entry",note="",status="new")
            return
        if (frac or 0)>=0.97:
            db.x("update spot_intents set status='cancelled',result='paper sold before the real buy',done_ts=? where kind='buy' and status in('new','approval') and book=? and paper_pid=?",(now,book,pid))
        if _open_for(db,book,pid):
            db.ins("spot_intents",ts=now,book=book,kind="sell",mint=mint,paper_pid=pid,frac=frac or 1.0,reason=reason or "exit",note=note or "exit",status="new")
    except Exception:log.exception("spot-live hook (the paper book is unaffected)")
class Intent(dict):__getattr__=dict.get
class SpotLive:
    def __init__(s,db,hel,notify,cfg):
        s.db,s.hel,s.n,s.cfg=db,hel,notify,cfg;s.c=conf(cfg);s._wallet=None;s.last_rec=0;s.last_mark=0;s.last_pending=0
    def k(s,n,d=None):return s.db.kv_get(P+n,d)
    def ks(s,n,v):s.db.kv_set(P+n,v)
    def wallet(s):
        if not s._wallet:s._wallet=signer("pubkey",timeout=10)["wallet"]
        return s._wallet
    def balances(s):
        w=s.wallet();lam=s.hel.rpc("getBalance",[w,{"commitment":"confirmed"}])["value"];toks={}
        for prog in(TOKEN,TOKEN22):
            for x in s.hel.rpc("getTokenAccountsByOwner",[w,{"programId":prog},{"encoding":"jsonParsed","commitment":"confirmed"}])["value"]:
                i=x["account"]["data"]["parsed"]["info"];toks[i["mint"]]=toks.get(i["mint"],0.0)+float(i["tokenAmount"]["uiAmountString"] or 0)
        return dict(sol=lam/1e9,usdc=toks.pop(USDC,0.0),tokens=toks)
    def positions(s):
        return [dict(id=i,book=b,mint=m,tokens=t,cost=c,opened=o,meta=json.loads(mt or "{}")) for i,b,m,t,c,o,mt in
                s.db.q("select id,trader,mint,tokens,cost,opened,meta from b_positions where book=? and status='open'",(BOOK,))]
    def equity(s,b=None):
        b=b or s.balances();pos=s.positions();px=jup_prices([p["mint"] for p in pos]) if pos else {}
        return b["usdc"]+sum(p["tokens"]*px[p["mint"]] if px.get(p["mint"]) else p["cost"] for p in pos)
    def signal(s,book,mint,decision,reason,size=None):
        try:s.db.ins("b_signals",ts=time.time(),book=BOOK,source=book,mint=mint,side="buy",decision=decision,reason=reason,size_usd=size,features="{}")
        except Exception as e:log.warning("signal log: %s",e)
    # ---- readiness -----------------------------------------------------------------------------------------------
    def checks(s,book=None):
        out=dict(safety=[],strategy=[]);sa=out["safety"]
        try:st=signer("status",timeout=10);sa.append((True,"coin wallet guard running"))
        except LiveError as e:st=None;sa.append((False,"coin guard: "+str(e)))
        if st:
            home=st.get("home");sa.append((bool(home),"home address set" if home else "home not set: /wallet link YOUR_PHANTOM"))
            tw=s.k("test_withdraw") or {};lt=s.db.kv_get("live:test_withdraw") or {}
            try:th=LV.signer("status",timeout=10).get("home")
            except LiveError:th=None
            proven=bool(tw.get("confirmed")) or bool(home and home==th and lt.get("confirmed"))
            sa.append((proven,"home proven by a test withdrawal" if proven else "test withdrawal: fund, then /real testwithdraw"))
            try:
                b=s.balances()
                sa.append((b["usdc"]>=s.c["min_usdc_to_arm"],f"coin wallet USDC ${b['usdc']:.2f} (need ${s.c['min_usdc_to_arm']:.0f}+)"))
                sa.append((b["sol"]>=s.c["sol_reserve"],f"fee SOL {b['sol']:.4f} (need {s.c['sol_reserve']})"))
            except Exception as e:sa.append((False,f"balance check failed ({type(e).__name__})"))
        t=s.k("selftest") or {};fresh=t.get("ts",0)>time.time()-s.c["selftest_max_age_h"]*3600;good=bool(t.get("ok") and t.get("full"))
        sa.append((good and fresh,"self-test passed (last 24h)" if good and fresh else "self-test older than 24h: /real selftest" if good else "self-test: fund, then /real selftest"))
        sa.append((not s.db.kv_get("panic"),"no panic stop" if not s.db.kv_get("panic") else "panic stop is on (/panic off)"))
        sa.append((not s.k("mismatch"),"wallet matches records" if not s.k("mismatch") else "wallet mismatch: /real clear"))
        sa.append((not s.k("shutdown"),"drawdown switch not tripped" if not s.k("shutdown") else "drawdown switch tripped: /real clear"))
        if book:
            import ops
            bq=ops.bar(s.db,book);out["strategy"].append((bq["ok"],f"paper bar: {ops.bar_text(bq)} (need PF 1.3)"))
        return out
    def checklist(s,ch,mode="arm"):
        return ["SAFETY:"]+ticks(ch["safety"])+(["STRATEGY (/real on):"]+ticks(ch["strategy"]) if ch["strategy"] else [])
    # ---- switching on and off ---------------------------------------------------------------------------------------
    def arm(s,book,code=None,mode="arm"):
        word="on" if mode=="arm" else "trial";name=f"/real {word} {book}";ch=s.checks(book)
        safe=all(ok for ok,_ in ch["safety"]);strat=all(ok for ok,_ in ch["strategy"]);rows=[kv("book",NAMES[book])]+s.checklist(ch)
        if code is None:
            if not safe:return term("real "+word,rows+[SEP,"fix the ✗ SAFETY items first"],("WARN","cannot turn on yet"))
            if mode=="arm" and not strat:return term("real on",rows+[SEP]+wrap(f"not proven yet. /real trial {book} = tiny size, every buy waits for your tap"),("WARN","cannot turn on yet"))
            c=code6();s.ks("arm_code",dict(code=c,exp=time.time()+120,mode=mode,book=book))
            warn=wrap(f"TRIAL: not proven, real money can be lost. Max {fusd(s.c['trial_max_usd'])} per buy, every buy needs your tap.") if mode=="trial" else ["real money will trade."]
            return term("real "+word,rows+[SEP]+warn+[f"off again after {s.c['arm_days']} days.",SEP,"confirm within 2 min:",f"{name} {c}"],("WARN","⚠ confirm with code"))
        a=s.k("arm_code") or {}
        if code!=a.get("code") or a.get("book")!=book or a.get("mode")!=mode or time.time()>a.get("exp",0):return term("real "+word,["code wrong or expired",f"send {name} again"],("ERR","not turned on"))
        s.ks("arm_code",None)
        if not safe or (mode=="arm" and not strat):return term("real "+word,["a check failed meanwhile:"]+rows,("ERR","not turned on"))
        b=s.balances();eq=s.equity(b)
        if s.k("start") is None:s.ks("start",eq);s.ks("peak",eq)
        if not s.positions():s.ks("cash",b["usdc"])
        s.ks("last_equity",eq);m=arm_map(s.db);until=time.time()+s.c["arm_days"]*86400
        m[book]=dict(ts=time.time(),boot=s.db.kv_get("boot_id"),until=until,mode=mode);s.ks("armed",m)
        left=max(0,s.c["approvals_required"]-(s.k("approved_count") or 0))
        return term("real "+word,[kv("book",NAMES[book]),kv("mode","TRIAL (capped)" if mode=="trial" else "ON"),kv("wallet",short(s.wallet())),kv("money",fusd(eq)),
            kv("approve","every buy" if mode=="trial" else f"next {left} buys"),kv("until",time.strftime("%d %b %H:%M UTC",time.gmtime(until))),SEP]+
            wrap(f"{NAMES[book]} now mirrors its paper trades with real money. Off: /real off {book} · stop all: /panic"),("OK","🔓 REAL MONEY "+("TRIAL" if mode=="trial" else "ON")))
    def off(s,book=None,why=None,quiet=True):
        m=arm_map(s.db);was=bool(m.get(book)) if book else bool(m)
        if book:m.pop(book,None)
        else:m={}
        s.ks("armed",m)
        a=s.k("approval")
        if a and (not book or a.get("book")==book):s.ks("approval",None)
        q="update spot_intents set status='cancelled',result='real money switched off',done_ts=? where kind='buy' and status in('new','approval')"
        s.db.x(q+(" and book=?" if book else ""),(time.time(),book) if book else (time.time(),))
        if was and not quiet:s.n.send(term("real --off",wrap(why or "switched off")+[SEP]+wrap("no new real buys. open real positions keep following their exits. on again: /real"),("WARN","🔒 REAL MONEY OFF")),tier="critical")
        return was
    def blocked(s):
        if s.k("pending_tx"):return "a transaction is still being confirmed"
        day=dt.datetime.utcnow().date().isoformat();ds=s.k("day_start") or {}
        if ds.get("day")==day and s.k("last_equity",ds["eq"])<ds["eq"]*(1-s.c["daily_loss_limit_pct"]):return "daily loss limit hit"
        return None
    # ---- sending (same tracking as the Trend Rider wallet: every transaction is settled before the next) ------------
    def send(s,tx_b64,last_valid=None,timeout=None):return LV.Live.send(s,tx_b64,last_valid,timeout)
    def tx_deltas(s,sig,mint):
        try:t=s.hel.rpc("getTransaction",[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0,"commitment":"confirmed"}])
        except Exception as e:log.warning("getTransaction %s: %s",sig[:10],e);return None
        if not t or not t.get("meta"):return None
        m=t["meta"];w=s.wallet()
        def tok(lst,mt):return sum(int(x["uiTokenAmount"]["amount"]) for x in (lst or []) if x.get("owner")==w and x.get("mint")==mt)
        dec=next((x["uiTokenAmount"].get("decimals") for x in (m.get("postTokenBalances") or [])+(m.get("preTokenBalances") or []) if x.get("mint")==mint),None)
        return dict(d_usdc=(tok(m.get("postTokenBalances"),USDC)-tok(m.get("preTokenBalances"),USDC))/1e6,d_tok=tok(m.get("postTokenBalances"),mint)-tok(m.get("preTokenBalances"),mint),dec=dec)
    def swap(s,inm,outm,amount_raw,kind,ctx,mint):
        """Quote -> Jupiter tx -> the coin guard checks + signs -> tracked -> sent -> confirmed -> exact change."""
        if s.k("pending_tx"):
            r=s.resolve_pending()
            if r=="landed":raise LiveError("an earlier transaction landed meanwhile; records updated, deciding again")
        b0=s.balances();q=jup_quote_full(inm,outm,amount_raw,s.c["slippage_bps"]);imp=float(q.get("priceImpactPct") or 0)
        if kind=="buy" and imp>s.c["max_impact"]:raise LiveError(f"price impact {imp:.2%} too high")
        tx,lv=jup_swap_tx(q,s.wallet(),s.c["priority_max_lamports"]);r=signer("sign_swap",tx=tx,mint=mint);t0=time.time();sig=tx_sig(r["tx"])
        s.ks("pending_tx",dict(sig=sig,tx=r["tx"],kind=kind,lv=lv,ts=t0,ctx=ctx,impact=imp,mint=mint))
        try:s.send(r["tx"],lv)
        except Unknown:raise
        except LiveError:s.ks("pending_tx",None);raise
        d=s.tx_deltas(sig,mint)
        if d is None or d.get("dec") is None:
            time.sleep(2);b1=s.balances();dec=decimals(s.hel,mint)
            d=dict(d_usdc=b1["usdc"]-b0["usdc"],d_tok=int(round((b1["tokens"].get(mint,0)-b0["tokens"].get(mint,0))*10**dec)),dec=dec)
        s.ks("pending_tx",None)
        return dict(d,sig=sig,impact=imp,seconds=time.time()-t0)
    def resolve_pending(s):
        p=s.k("pending_tx")
        if not p:return None
        st=s.hel.rpc("getSignatureStatuses",[[p["sig"]],{"searchTransactionHistory":True}])["value"][0]
        if st and st.get("confirmationStatus") in("confirmed","finalized"):
            if st.get("err"):s.ks("pending_tx",None);return "failed"
            s._finalize(p);return "landed"
        if p.get("lv") and s.hel.rpc("getBlockHeight",[{"commitment":"confirmed"}])>p["lv"]:
            st=s.hel.rpc("getSignatureStatuses",[[p["sig"]],{"searchTransactionHistory":True}])["value"][0]
            if st and st.get("confirmationStatus") in("confirmed","finalized") and not st.get("err"):s._finalize(p);return "landed"
            s.ks("pending_tx",None);return "expired"
        try:s.send(p["tx"],p.get("lv"))
        except Unknown:raise
        except LiveError:s.ks("pending_tx",None);return "expired"
        s._finalize(p);return "landed"
    def _finalize(s,p):
        d=s.tx_deltas(p["sig"],p["mint"]) or dict(d_usdc=0,d_tok=0,dec=None);s.ks("pending_tx",None);ctx=p.get("ctx") or {}
        r=dict(d,sig=p["sig"],impact=p.get("impact",0),seconds=time.time()-p["ts"])
        if p["kind"]=="buy" and r["d_tok"]>0:s._record_buy(ctx.get("book"),ctx.get("paper_pid"),p["mint"],r,ctx.get("mode"))
        elif p["kind"]=="sell":
            pos=next((x for x in s.positions() if x["id"]==ctx.get("pos_id")),None)
            if pos:s._record_sell(pos,r,ctx.get("amt",pos["tokens"]),ctx.get("frac",1.0),ctx.get("reason","exit"),ctx.get("note","exit"))
        s.n.send(term("real --recover",[kv("tx",p["sig"][:10]+"…"),kv("kind",p["kind"]),kv("USDC",f"{r['d_usdc']:+.2f}"),SEP,"an earlier transaction did","land; records updated"],("WARN","🔁 recovered pending tx")))
    # ---- the worker: sells first, then approvals, then buys ------------------------------------------------------------
    def intents(s):
        cols=("id","ts","book","kind","mint","paper_pid","frac","reason","note","status","tries","next_ts","size")
        return [Intent(zip(cols,r)) for r in s.db.q("select "+",".join(cols)+" from spot_intents where status in('new','retry','approval') order by case kind when 'sell' then 0 else 1 end,id")]
    def done(s,it,status,result):s.db.x("update spot_intents set status=?,result=?,done_ts=? where id=?",(status,str(result)[:200],time.time(),it.id))
    def tick(s,now=None):
        now=now or time.time()
        if s.k("pending_tx") and now-s.last_pending>=20:
            s.last_pending=now
            try:r=s.resolve_pending();r and log.info("spot pending tx resolved: %s",r)
            except Exception as e:log.warning("spot pending tx still open: %s",e)
        m=arm_map(s.db);gone=[b for b,a in m.items() if now>=a.get("until",0)]
        for b in gone:s.off(b);s.n.send(term("real --expired",wrap(f"{NAMES.get(b,b)}: real money switched off after {s.c['arm_days']} days. renew: /real {'trial' if m[b].get('mode')=='trial' else 'on'} {b}"),("WARN","🔒 real money off")))
        if s.k("panic_sell"):s.panic_tick()
        w=s.k("withdraw")
        if w:s.ks("withdraw",None);s.do_withdraw(test=(w=="test"))
        for it in s.intents():
            try:
                if it.kind=="sell":s.do_sell(it,now)
                elif it.status=="approval":continue
                else:s.do_buy(it,now)
            except Exception:log.exception("spot intent %s (continuing)",it.id)
        s.process_approval(now)
        if not arm_map(s.db) and not s.positions():return
        if now-s.last_rec>=60:s.last_rec=now;s.reconcile()
        if now-s.last_mark>=300:s.last_mark=now;s.mark()
    # ---- buys -----------------------------------------------------------------------------------------------------
    def needs_approval(s,a):return a.get("mode")=="trial" or (s.k("approved_count") or 0)<s.c["approvals_required"]
    def do_buy(s,it,now):
        a=book_on(s.db,it.book)
        if not a:return s.done(it,"cancelled","real money is off for this book")
        if now-it.ts>s.c["max_buy_age_s"].get(it.book,120):s.signal(it.book,it.mint,"refused","too late: the paper buy is old");return s.done(it,"cancelled","too late")
        if not s.db.q("select 1 from b_positions where id=? and status='open'",(it.paper_pid,)):return s.done(it,"cancelled","the paper position is already closed")
        if s.k("approval"):return                                   # one question at a time; this buy waits (and may expire)
        why=s.blocked()
        if why:s.signal(it.book,it.mint,"refused",why);return s.done(it,"refused",why)
        if any(p["mint"]==it.mint for p in s.positions()):s.signal(it.book,it.mint,"refused","the real wallet already holds this coin");return s.done(it,"refused","already holding this coin")
        b=s.balances();eq=s.equity(b);cap=signer("status",timeout=10)["limits"]["max_swap_usd"]
        if a.get("mode")=="trial":cap=min(cap,s.c["trial_max_usd"])
        size=min((it.frac or 0)*eq,cap,b["usdc"]-0.05)
        if size<s.c["min_buy_usd"]:s.signal(it.book,it.mint,"refused",f"real size ${max(size,0):.2f} too small",size);return s.done(it,"refused",f"size ${max(size,0):.2f} too small")
        s.db.x("update spot_intents set size=? where id=?",(size,it.id))
        if s.needs_approval(a):
            aid=f"{random.SystemRandom().randrange(16**6):06x}";s.ks("approval",dict(id=aid,intent=it.id,book=it.book,mint=it.mint,size=size,exp=now+s.c["approval_seconds"],status="pending"))
            s.db.x("update spot_intents set status='approval' where id=?",(it.id,))
            left=s.c["approvals_required"]-(s.k("approved_count") or 0)
            s.n.send(term("real --approve",[kv("book",NAMES[it.book]),kv("mode","TRIAL (not proven)" if a.get("mode")=="trial" else "ON"),kv("buy",fusd(size)),kv("coin",short(it.mint)),SEP]+
                wrap(f"the paper book just bought this coin. tap within {s.c['approval_seconds']} s or it is skipped."+("" if a.get("mode")=="trial" else f" {left} approvals left.")),
                ("WARN","❓ REAL BUY needs your OK"),f"<code>{esc(it.mint)}</code>"),buttons=[[("✅ Approve",f"sappr:{aid}:yes"),("❌ Reject",f"sappr:{aid}:no")]])
            return
        s.buy(it,size,a.get("mode"))
    def process_approval(s,now):
        a=s.k("approval")
        if not a:return
        it=next((x for x in s.intents() if x.id==a["intent"]),None)
        if a["status"]=="pending" and now>a["exp"]:
            s.ks("approval",None);it and s.done(it,"cancelled","approval timed out");s.signal(a["book"],a["mint"],"refused","approval timed out",a["size"])
            s.n.send(term("real --approve",["no answer in time, skipped"],("WARN","approval expired")));return
        if a["status"]=="no":s.ks("approval",None);it and s.done(it,"cancelled","you rejected it");s.signal(a["book"],a["mint"],"refused","you rejected it",a["size"]);return
        if a["status"]=="yes":
            s.ks("approval",None)
            if not it:return
            arm=book_on(s.db,it.book)
            if not arm:return s.done(it,"cancelled","real money switched off meanwhile")
            if not s.db.q("select 1 from b_positions where id=? and status='open'",(it.paper_pid,)):return s.done(it,"cancelled","the paper position closed meanwhile")
            if s.buy(it,a["size"],arm.get("mode")):s.ks("approved_count",(s.k("approved_count") or 0)+1)
    def buy(s,it,size,mode):
        try:r=s.swap(USDC,it.mint,int(size*1e6),"buy",dict(book=it.book,paper_pid=it.paper_pid,intent=it.id,mode=mode),it.mint)
        except Exception as e:
            s.done(it,"failed",e);s.signal(it.book,it.mint,"refused",f"real buy failed: {str(e)[:80]}",size)
            s.n.send(term("real buy",[kv("book",NAMES[it.book])]+wrap(str(e)[:200])+[SEP,"nothing bought. /real for","the state"],("ERR","🔴 REAL BUY failed")));return None
        s.done(it,"done","bought")                  # first: a crash while recording can never lead to a second buy
        if r["d_tok"]<=0 or r["d_usdc"]>=0:
            s.done(it,"failed","unexpected result");s.off(why="unexpected buy result: check the wallet",quiet=False);return None
        return s._record_buy(it.book,it.paper_pid,it.mint,r,mode)
    def _record_buy(s,book,ppid,mint,r,mode):
        dec=r.get("dec") if r.get("dec") is not None else decimals(s.hel,mint)
        got=r["d_tok"]/10**dec;spent=-r["d_usdc"];px=spent/got if got else 0;now=time.time()
        pid=s.db.ins("b_positions",book=BOOK,trader=book,mint=mint,tokens=got,cost=spent,opened=now,status="open",open_sig=r["sig"],
                     meta=json.dumps(dict(paper_pid=ppid,src=book,entry=px,mode=mode,dec=dec)))
        s.db.ins("b_fills",ts=now,book=BOOK,sig=r["sig"],trader=book,mint=mint,side="buy",status="filled",reason="mirror of paper buy",fill_ts=now,our_px=px,impact=r.get("impact"),size_usd=spent,live=1,txsig=r["sig"])
        s.db.ins("b_trades",ts=now,book=BOOK,trader=book,mint=mint,side="buy",usd=spent,tokens=got,price=px,pnl=0,sig=r["sig"],note=f"REAL {book} impact {r.get('impact',0):.2%}")
        s.ks("cash",(s.k("cash") or 0)-spent);s.signal(book,mint,"filled","real buy",spent)
        s.n.send(term("real buy",[kv("book",NAMES.get(book,book)),kv("mode","REAL MONEY"+(" · TRIAL" if mode=="trial" else "")),SEP,kv("spent",fusd(spent)),kv("price",f"${px:.6g}"),
            kv("tx",r["sig"][:10]+"…"),kv("time",f"{r.get('seconds',0):.0f}s"),SEP]+wrap("exits follow the paper position (stop, targets, trailing)"),("OK","🟢 REAL BUY filled"),f"<code>{esc(mint)}</code>"))
        return pid
    # ---- sells: never blocked --------------------------------------------------------------------------------------
    def do_sell(s,it,now):
        pos=_open_for(s.db,it.book,it.paper_pid)
        if not pos:return s.done(it,"done","no real position left")
        if now<(it.next_ts or 0):return
        frac=1.0 if (it.frac or 1)>=0.97 else it.frac
        try:
            b=s.balances();have=b["tokens"].get(pos["mint"],0.0);amt=min(pos["tokens"]*frac,have)
            if amt<=0:raise LiveError("no coins of this position in the wallet")
            dec=pos["meta"].get("dec");dec=decimals(s.hel,pos["mint"]) if dec is None else dec
            r=s.swap(pos["mint"],USDC,int(amt*10**dec),"sell",dict(pos_id=pos["id"],amt=amt,frac=frac,reason=it.reason,note=it.note),pos["mint"])
        except Exception as e:
            tries=(it.tries or 0)+1;s.db.x("update spot_intents set status='retry',tries=?,next_ts=?,result=? where id=?",(tries,now+60,str(e)[:200],it.id))
            if tries==1 or tries%10==0:
                s.n.send(term("real sell",[kv("book",NAMES.get(it.book,it.book)),kv("why",it.note),kv("tries",tries)]+wrap(str(e)[:160])+[SEP,"position kept and watched;","retrying every minute"],("ERR","🔴 REAL SELL failed"),f"<code>{esc(pos['mint'])}</code>"),tier="critical")
            return
        s.done(it,"done","sold");s._record_sell(pos,r,amt,frac,it.reason,it.note)   # done first: never a second sell
    def _record_sell(s,pos,r,amt,frac,reason,note):
        back=r["d_usdc"];part=min(1.0,amt/pos["tokens"]) if pos["tokens"] else 1.0;closed=frac>=0.97 or part>=0.97
        basis=pos["cost"]*(1.0 if closed else part);pnl=back-basis;px=back/amt if amt else 0;now=time.time()
        if closed:s.db.x("update b_positions set tokens=0,cost=0,status='closed',closed=?,realized=realized+? where id=?",(now,pnl,pos["id"]))
        else:s.db.x("update b_positions set tokens=?,cost=?,realized=realized+? where id=?",(pos["tokens"]-amt,pos["cost"]-basis,pnl,pos["id"]))
        s.db.ins("b_fills",ts=now,book=BOOK,sig=r["sig"],trader=pos["book"],mint=pos["mint"],side="sell",status="filled",reason=reason,fill_ts=now,our_px=px,impact=r.get("impact"),size_usd=back,live=1,txsig=r["sig"])
        s.db.ins("b_trades",ts=now,book=BOOK,trader=pos["book"],mint=pos["mint"],side="sell",usd=back,tokens=amt,price=px,pnl=pnl,sig=r["sig"],note=f"REAL {note}")
        s.ks("cash",(s.k("cash") or 0)+back)
        s.n.send(term("real sell",[kv("book",NAMES.get(pos["book"],pos["book"])),kv("mode","REAL MONEY"),SEP,kv("why",note),kv("portion","all" if closed else f"{part:.0%}"),kv("got",fusd(back)),
            kv("pnl",fusd(pnl,True)),kv("tx",r["sig"][:10]+"…")],("OK",("🟢" if pnl>=0 else "🔴")+" REAL SELL"),f"<code>{esc(pos['mint'])}</code>"))
    def panic_tick(s):
        pos=s.positions()
        if not pos:
            s.ks("panic_sell",None);s.n.send(term("panic",["coin wallet: no real","position left"],("OK","🛑 panic: coin wallet flat")));return
        busy={(b,p) for b,p in s.db.q("select book,paper_pid from spot_intents where kind='sell' and status in('new','retry')")}
        for p in pos:
            if (p["book"],p["meta"].get("paper_pid")) not in busy:
                s.db.ins("spot_intents",ts=time.time(),book=p["book"],kind="sell",mint=p["mint"],paper_pid=p["meta"].get("paper_pid"),frac=1.0,reason="panic",note="/panic: sold at market",status="new")
    # ---- records vs the wallet --------------------------------------------------------------------------------------
    def reconcile(s):
        if s.k("pending_tx"):
            try:s.resolve_pending()
            except Exception as e:log.warning("spot reconcile: pending tx unknown (%s)",e);return
        try:b=s.balances()
        except Exception as e:log.warning("spot reconcile: %s",e);return
        rec=s.k("cash");why=None
        if rec is not None and b["usdc"]<rec-max(0.05,rec*0.01):why=f"USDC: expected {fusd(rec)}, wallet {fusd(b['usdc'])}"
        elif rec is None or b["usdc"]>rec+0.05:s.ks("cash",b["usdc"])          # a deposit
        need={}
        for p in s.positions():need[p["mint"]]=need.get(p["mint"],0)+p["tokens"]
        for m,n in need.items():
            if b["tokens"].get(m,0)<n*0.98:why=f"coin {short(m)}: records {n:.6g}, wallet {b['tokens'].get(m,0):.6g}";break
        if why and not s.k("mismatch"):
            s.ks("mismatch",dict(ts=time.time(),detail=why));s.off(why="wallet mismatch")
            s.n.send(term("real --reconcile",wrap(why)+[SEP]+wrap("real money switched OFF for every coin book. Nothing new is bought; sells continue. Review the wallet, then /real clear"),("ERR","🔴 COIN WALLET MISMATCH")),tier="critical")
    def mark(s):
        try:b=s.balances();eq=s.equity(b)
        except Exception as e:log.warning("spot mark: %s",e);return
        s.ks("last_equity",eq);s.db.ins("b_equity",ts=time.time(),book=BOOK,equity=eq,cash=b["usdc"])
        peak=max(s.k("peak") or eq,eq);s.ks("peak",peak);day=dt.datetime.utcnow().date().isoformat()
        if (s.k("day_start") or {}).get("day")!=day:s.ks("day_start",{"day":day,"eq":eq})
        if eq<peak*(1-s.c["max_drawdown_pct"]) and not s.k("shutdown"):
            s.ks("shutdown",True);s.off(why="drawdown switch")
            s.n.send(term("real --safeguard",[kv("peak",fusd(peak)),kv("money",fusd(eq)),SEP]+wrap("real money switched OFF for every coin book; paper continues. review, then /real clear"),("ERR","🔴 COIN WALLET DRAWDOWN SWITCH")),tier="critical")
    def clear(s,code=None):
        mm=s.k("mismatch");sd=s.k("shutdown")
        if not mm and not sd:return term("real clear",["nothing to clear"],("OK","all clear"))
        if code is None:
            c=code6();s.ks("clear_code",dict(code=c,exp=time.time()+120))
            return term("real clear",([kv("mismatch",mm.get("detail","?"))] if mm else [])+([kv("drawdown","switch tripped")] if sd else [])+[SEP]+wrap("records are reset to what the coin wallet holds now. real money stays OFF.")+[SEP,f"confirm: /real clear {c}"],("WARN","confirm with code"))
        a=s.k("clear_code") or {}
        if code!=a.get("code") or time.time()>a.get("exp",0):return term("real clear",["code wrong or expired"],("ERR","not cleared"))
        s.ks("clear_code",None);b=s.balances()
        for p in s.positions():
            have=b["tokens"].get(p["mint"],0.0)
            if have<p["tokens"]*0.98:s.db.x("update b_positions set tokens=? where id=?",(have,p["id"])) if have>0 else s.db.x("update b_positions set tokens=0,status='closed',closed=? where id=?",(time.time(),p["id"]))
        eq=s.equity(b);s.ks("cash",b["usdc"]);s.ks("last_equity",eq);s.ks("peak",eq);s.ks("shutdown",None);s.ks("mismatch",None)
        s.ks("day_start",{"day":dt.datetime.utcnow().date().isoformat(),"eq":eq})
        return term("real clear",[kv("USDC",fusd(b["usdc"])),kv("money",fusd(eq)),SEP,"records match the wallet.","still off: /real"],("OK","cleared"))
    # ---- withdraw + self-test ---------------------------------------------------------------------------------------
    def do_withdraw(s,test=False):
        rows=[]
        try:
            if s.k("pending_tx"):s.resolve_pending()
            b=s.balances()
            if test:
                if b["usdc"]>=s.c["test_withdraw_usdc"]:kind,amt="usdc",int(s.c["test_withdraw_usdc"]*1e6)
                elif b["sol"]>=s.c["test_withdraw_sol"]+0.004:kind,amt="sol",int(s.c["test_withdraw_sol"]*1e9)
                else:raise LiveError(f"fund the coin wallet first (needs ${s.c['test_withdraw_usdc']:.0f} USDC or {s.c['test_withdraw_sol']+0.004:.3f} SOL)")
                r=signer("withdraw",kind=kind,amount=amt);sig=s.send(r["tx"],r.get("last_valid"))
                s.ks("test_withdraw",dict(ts=time.time(),kind=kind,amount=amt,sig=sig,home=r["home"],confirmed=False))
                if kind=="usdc" and s.k("cash") is not None:s.ks("cash",max(0,s.k("cash")-amt/1e6))
                what=f"{amt/1e6:.2f} USDC" if kind=="usdc" else f"{amt/1e9:.4f} SOL"
                s.n.send(term("real testwithdraw",[kv("sent",what),kv("to",short(r["home"])),kv("tx",sig[:10]+"…"),SEP,"check Phantom, then send:","/real received"],("OK","💸 test withdrawal sent")));return
            if s.positions():raise LiveError("real coin positions are open: /panic sells them first")
            if b["usdc"]>0.01:r=signer("withdraw",kind="usdc");s.send(r["tx"],r.get("last_valid"));rows.append(f"sent {r['amount']/1e6:.2f} USDC home")
            r=signer("withdraw",kind="sol");s.send(r["tx"],r.get("last_valid"));rows.append(f"sent {r['amount']/1e9:.5f} SOL home")
            s.ks("cash",0);s.n.send(term("real withdraw",rows+[kv("to",short(r["home"]))],("OK","💸 coin wallet withdrawn")))
        except Exception as e:s.n.send(term("real withdraw",rows+wrap(str(e)[:200]),("ERR","withdraw stopped")))
    def selftest(s):
        import solwire as W
        rows=[];ok=True;full=True
        def step(good,text):
            nonlocal ok;rows.extend(wrap(text,("✓ " if good else "✗ ")));ok=ok and good
        try:st=signer("status",timeout=10);step(True,"coin guard running");step(True,"wallet "+short(st["wallet"]))
        except LiveError as e:
            step(False,str(e));s.ks("selftest",dict(ts=time.time(),ok=False,full=False));return term("real selftest",rows,("ERR","self-test failed"))
        step(bool(st.get("home")),"home set" if st.get("home") else "home not set (/wallet link)")
        try:b=s.balances();step(True,f"RPC ok: {b['sol']:.4f} SOL, ${b['usdc']:.2f} USDC")
        except Exception as e:step(False,f"RPC failed ({type(e).__name__})");b=dict(sol=0,usdc=0,tokens={})
        coin=s.c["selftest_coin"]
        try:
            q=jup_quote_full(USDC,coin,1_000_000,s.c["slippage_bps"]);tx,lv=jup_swap_tx(q,st["wallet"],s.c["priority_max_lamports"])
            signer("check_static",tx=tx);step(True,"buy: a real Jupiter coin swap passes the instruction checks")
            if b["usdc"]>=1 and b["sol"]>=0.005:signer("check_swap",tx=tx,mint=coin);step(True,"buy: simulation, coin checks and limits ok")
            else:full=False;rows.extend(wrap("buy: simulation skipped (coin wallet not funded yet)","· "))
        except Exception as e:step(False,f"buy: {str(e)[:90]}")
        bh=bytes(32);me=W.pk(st["wallet"]);stranger=bytes(range(1,33));memo=W.pk("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")
        for name,raw in(("SOL sent to a stranger",W.sol_transfer(me,stranger,1000,bh)),("unknown program",W.legacy_tx([me,memo],1,0,1,bh,[(1,[],b"zetrax")])),("someone else pays",W.sol_transfer(stranger,me,1000,bh))):
            try:signer("check_static",tx=base64.b64encode(raw).decode());step(False,f"guard ACCEPTED '{name}'")
            except LiveError:step(True,f"refuses {name}")
        s.ks("selftest",dict(ts=time.time(),ok=ok,full=ok and full))
        rows+=[SEP,"no money moved"]+([] if full else wrap("fund the coin wallet and run it again for the full test"))
        return term("real selftest",rows,("OK","✅ coin wallet works"+("" if full else " (unfunded part)")) if ok else ("WARN","self-test found problems"))
# ---- one place for every real-money switch ---------------------------------------------------------------------------
def trend_state(db):
    a=db.kv_get("live:armed") or {}
    if not a or a.get("boot")!=db.kv_get("boot_id") or time.time()>=a.get("until",0) or db.kv_get("panic"):return "OFF"
    return "TRIAL" if a.get("mode")=="trial" else "ON"
def perps_state(db):
    a=db.kv_get("perps-live:armed") or {}
    if not a or a.get("boot")!=db.kv_get("boot_id") or time.time()>=a.get("until",0) or db.kv_get("panic") or db.kv_get("perps-live:mismatch"):return "OFF"
    return "TRIAL" if a.get("mode")=="trial" else "ON"
def overview(db):
    rows=["every switch starts OFF and","goes off on restart, /panic,","after 7 days or a mismatch.",SEP,"book            real money"]
    rows.append(f"{'Trend Rider':<16}{trend_state(db)}")
    for b in BOOKS:a=book_on(db,b);rows.append(f"{NAMES[b]:<16}{('TRIAL' if a.get('mode')=='trial' else 'ON') if a else 'OFF'}")
    rows.append(f"{'perps':<16}{perps_state(db)}")
    rows+=[f"{'perps-grok':<16}paper only",SEP,"switch on (checks + code):","/real on BOOK","/real trial BOOK (capped,","  every buy needs your tap)",
           "switch off:","/real off BOOK | /real off all","BOOK: trend narrative basket","  copy consensus perps",SEP,"wallets: /wallet",
           "coin wallet test: /real selftest","stop + sell all: /panic"]
    return term("real",rows,("OK","🔐 real money: switches"))
def _quiet():
    class Q:
        token=""
        def send(s,*a,**k):pass
    return Q()
def cmd(db,text,cfg,notify=None):
    """/real ... -> reply text."""
    from chain import Helius
    a=text.split()[1:];n=notify or _quiet()
    if not a:return overview(db)
    sub=a[0].lower();arg=[x.lower() for x in a[1:]]
    code=next((x for x in arg if x.isdigit() and len(x)==6),None);names=[x for x in arg if not x.isdigit()]
    target=names[0] if names else None
    try:
        if sub in("on","arm","trial"):
            mode="trial" if sub=="trial" else "arm"
            if target in("trend","trend-rider","sol"):
                return LV.Live(db,Helius(),n,cfg).arm(code,mode=mode)
            if target=="perps":
                import perps_live as PL
                return PL.PerpsLive(db,cfg,n).arm(code,mode=mode,sizing="charter")
            if target not in ALIAS:return term("real",["which book?","/real on narrative | basket |","copy | consensus | trend |","perps"],("WARN","name the book"))
            return SpotLive(db,Helius(),n,cfg).arm(ALIAS[target],code,mode)
        if sub=="off":
            if target in(None,"all"):
                db.kv_set("live:armed",None);db.kv_set("live:approval",None);db.kv_set("perps-live:armed",None);db.kv_set("perps-live:approval",None)
                SpotLive(db,None,n,cfg).off()
                return term("real off",["ALL real money is OFF:","Trend Rider, perps and the","coin books. Open positions","keep their exits.",SEP,"sell everything: /panic"],("OK","🔒 all real money off"))
            if target in("trend","trend-rider","sol"):db.kv_set("live:armed",None);db.kv_set("live:approval",None);return term("real off",["Trend Rider real money OFF"],("OK","🔒 off"))
            if target=="perps":db.kv_set("perps-live:armed",None);db.kv_set("perps-live:approval",None);return term("real off",["real-money perps OFF; open","positions keep their stop","and targets on the exchange"],("OK","🔒 off"))
            if target not in ALIAS:return term("real off",["/real off BOOK | all"],("WARN","name the book"))
            SpotLive(db,None,n,cfg).off(ALIAS[target]);return term("real off",[f"{NAMES[ALIAS[target]]}: real money OFF","open real positions keep","following their exits"],("OK","🔒 off"))
        S=SpotLive(db,Helius(),n,cfg)
        if sub=="selftest":return S.selftest()
        if sub=="clear":return S.clear(code)
        if sub=="received":
            tw=S.k("test_withdraw") or {}
            if not tw.get("sig"):return term("real received",["no test withdrawal sent yet","/real testwithdraw"],("WARN","nothing to confirm"))
            S.ks("test_withdraw",dict(tw,confirmed=True,confirmed_ts=time.time()));return term("real received",["test withdrawal confirmed:","the coin wallet can pay","you back."],("OK","✅ home address works"))
        if sub in("testwithdraw","withdraw"):
            if any(book_on(db,b) for b in BOOKS):return term("real "+sub,["switch the coin books off","first: /real off all"],("WARN","real money is on"))
            key="tw_code" if sub=="testwithdraw" else "wd_code"
            if not code:
                c=code6();S.ks(key,dict(code=c,exp=time.time()+120))
                what=[f"sends ${S.c['test_withdraw_usdc']:.0f} USDC (or {S.c['test_withdraw_sol']} SOL)","to your home address to","prove it works."] if sub=="testwithdraw" else ["sends ALL USDC and SOL of","the coin wallet to your","home address."]
                return term("real "+sub,what+[SEP,"confirm within 2 min:",f"/real {sub} {c}"],("WARN","confirm with code"))
            w=S.k(key) or {}
            if code!=w.get("code") or time.time()>w.get("exp",0):return term("real "+sub,["code wrong or expired"],("ERR","not started"))
            S.ks(key,None);S.ks("withdraw","test" if sub=="testwithdraw" else True)
            return term("real "+sub,["starting within a few","seconds; result follows"],("OK","💸 queued"))
        return term("real",["unknown: /real "+sub,"send /real for the list"],("WARN","bad syntax"))
    except LiveError as e:return term("real "+sub,wrap(str(e)),("ERR","wallet guard"))
def wallet_rows(db,cfg):
    """/wallet: all three wallets and how to link yours."""
    from chain import Helius
    rows=[];extra=[];h=None
    try:h=Helius()
    except Exception:pass
    for label,sock,key in(("1 Trend Rider (SOL)",None,"live"),("2 coin books",SOCK,"spot")):
        try:
            st=LV.signer("status",timeout=10,sock=sock);rows+=[label+":",kv("  wallet",short(st["wallet"]),9),kv("  home",short(st.get("home")) if st.get("home") else "not linked",9)]
            if h:
                try:
                    lam=h.rpc("getBalance",[st["wallet"],{"commitment":"confirmed"}])["value"]
                    r=h.rpc("getTokenAccountsByOwner",[st["wallet"],{"mint":USDC},{"encoding":"jsonParsed","commitment":"confirmed"}])["value"]
                    u=sum(float(x["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmountString"] or 0) for x in r)
                    rows.append(kv("  money",f"${u:.2f} + {lam/1e9:.3f} SOL",9))
                except Exception:rows.append(kv("  money","balance check failed",9))
            extra.append(f"{label}: <code>{esc(st['wallet'])}</code>")
        except LiveError:rows+=[label+":","  guard not running yet","  (/update installs it)"]
    pm=None
    try:
        import perps_live as PL;st=PL.guard("status",timeout=10);pm=st.get("master")
        rows+=["3 Hyperliquid (perps):",kv("  yours",(pm[:6]+"…"+pm[-4:]) if pm else "not linked",9),kv("  agent","approved" if st.get("approved") else "approve in the app",9)]
    except Exception:rows+=["3 Hyperliquid (perps):","  guard not running yet"]
    rows+=[SEP,"link your wallets (once):"]+wrap("/wallet link YOUR_PHANTOM (both Solana wallets pay back only to it)")+wrap("/wallet link 0xYOUR_HYPERLIQUID (perps)")+\
          [SEP]+wrap("fund later: send USDC + about 0.03 SOL to wallet 1 and/or 2 (full addresses below). Real money stays OFF until /real on.")
    return rows,"\n".join(extra)
def runner_thread(db_path,cfg,notify):
    c=conf(cfg)
    def job():
        from db import DB;from chain import Helius
        db=DB(db_path)
        try:hel=Helius()
        except Exception as e:log.warning("spot-live: no RPC (%s); real coin trading unavailable",e);return
        S=SpotLive(db,hel,notify,cfg);time.sleep(5)
        while True:
            try:
                if arm_map(db) or db.q("select 1 from spot_intents where status in('new','retry','approval') limit 1") or S.positions() or S.k("panic_sell") or S.k("withdraw") or S.k("pending_tx"):S.tick()
                db.kv_set(P+"alive",time.time())
            except Exception as e:
                log.exception("spot-live tick (continuing)")
                try:db.kv_set(P+"error",{"ts":time.time(),"msg":f"{type(e).__name__}: {str(e)[:160]}"})
                except Exception:pass
            time.sleep(c["check_seconds"])
    t=threading.Thread(target=job,daemon=True,name="spot-live");t.start();return t
