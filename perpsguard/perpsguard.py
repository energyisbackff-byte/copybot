#!/usr/bin/env python3
"""ZETRAX perps guard: the only process that holds the Hyperliquid API ("agent") key. It runs as its own user
(copybot-perps), its code is installed root-owned outside /opt/copybot, and it has its own Python environment with
the official hyperliquid-python-sdk, which does all the signing (no home-made cryptography).
Why it is safe even if the bot itself were taken over:
  - an agent key can place and cancel orders for YOUR Hyperliquid account, but Hyperliquid itself does not let an
    agent key withdraw or transfer money. Only your own wallet (the "master") can move money out.
  - the network (testnet or mainnet) is fixed in /etc/copybot-perpsguard.json, which only root can change. Default:
    testnet. The bot can not switch to real money by itself.
  - opening orders: only the listed coins; at most max_order_usd notional; leverage at most max_leverage; at most
    max_orders_day and max_day_usd per UTC day; only after the master address was set (once).
  - closing orders (reduce-only), cancels and close_all are ALWAYS allowed, so a stop or /panic can never be blocked.
The bot talks to it over a Unix socket and can only ask: status, address, set_master (once), leverage, order,
cancel, close_all, positions, fills, selftest (testnet only: a post-only order far from the price, then a cancel)."""
import os,sys,json,time,socket,struct,pwd,logging,threading,math
logging.basicConfig(level=logging.INFO,format="%(asctime)s perpsguard %(levelname)s %(message)s");log=logging.getLogger("perpsguard")
STATE_DIR=os.environ.get("PERPS_STATE","/var/lib/copybot-perps");SOCK=os.environ.get("PERPS_SOCK","/run/copybot-perps/guard.sock")
CONF=os.environ.get("PERPS_CONF","/etc/copybot-perpsguard.json");CLIENT_USER=os.environ.get("PERPS_CLIENT","copybot")
DEFAULTS=dict(network="test",max_order_usd=25.0,max_leverage=3,max_orders_day=20,max_day_usd=100.0,coins=["XRP","DOGE","LINK","UNI","ATOM","SOL"])
URL={"test":"https://api.hyperliquid-testnet.xyz","main":"https://api.hyperliquid.xyz"}
LOCK=threading.Lock()
class Refused(Exception):pass
def conf():
    try:c=json.load(open(CONF))
    except Exception:c={}
    c=dict(DEFAULTS,**{k:v for k,v in c.items() if k in DEFAULTS})
    if c["network"] not in URL:c["network"]="test"
    c["max_leverage"]=int(max(1,min(50,c["max_leverage"])));return c
def state(update=None):
    p=os.path.join(STATE_DIR,"state.json")
    try:s=json.load(open(p))
    except Exception:s={}
    if update is not None:
        s.update(update);tmp=p+".tmp"
        with open(tmp,"w") as f:json.dump(s,f)
        os.chmod(tmp,0o600);os.replace(tmp,p)
    return s
def today(s):
    d=time.strftime("%Y-%m-%d",time.gmtime())
    return (s.get("day_usd",0.0),s.get("day_orders",0)) if s.get("day")==d else (0.0,0)
def is_address(a):
    a=str(a or "")
    return len(a)==42 and a.startswith("0x") and all(ch in "0123456789abcdefABCDEF" for ch in a[2:])
# ---- the exchange adapter: every call to the official SDK lives here (tests replace this class) ------------------
class HL:
    def __init__(s,key_hex,network,master):
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
        s.wallet=Account.from_key(key_hex);s.url=URL[network];s.master=master
        s.info=Info(s.url,skip_ws=True);s.ex=Exchange(s.wallet,s.url,account_address=master) if master else None
    def address(s):return s.wallet.address
    def meta(s):
        return {u["name"]:dict(max_lev=int(u.get("maxLeverage") or 1),sz_dec=int(u.get("szDecimals") or 0)) for u in s.info.meta()["universe"]}
    def mids(s):return {k:float(v) for k,v in s.info.all_mids().items()}
    def user(s):return s.info.user_state(s.master)
    def open_orders(s):return s.info.frontend_open_orders(s.master)
    def fills(s,since_ms):return s.info.user_fills_by_time(s.master,int(since_ms))
    def leverage(s,coin,lev):return s.ex.update_leverage(int(lev),coin,False)
    def orders(s,reqs,grouping="na"):return s.ex.bulk_orders(reqs,grouping=grouping)
    def cancel(s,coin,oid):return s.ex.cancel(coin,int(oid))
    def approved(s):
        """Is this agent approved by the master account? (Hyperliquid: 'extraAgents' of the master)."""
        try:
            r=s.info.post("/info",{"type":"extraAgents","user":s.master})
            return any(str(x.get("address","")).lower()==s.wallet.address.lower() for x in (r or []))
        except Exception as e:log.warning("approval check: %s",e);return None
def keyfile():
    """The agent key: created once on this server, never leaves it (0600, guard user only)."""
    p=os.path.join(STATE_DIR,"agent.json")
    if not os.path.exists(p):
        from eth_account import Account
        a=Account.create();fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w") as f:json.dump({"key":a.key.hex(),"address":a.address},f)
        log.info("created new agent key %s",a.address)
    return json.load(open(p))
_hl={}
def hl(c,s,make=None):
    k=(c["network"],s.get("master"))
    if k not in _hl:
        kf=keyfile();_hl.clear();_hl[k]=(make or HL)(kf["key"],c["network"],s.get("master"))
    return _hl[k]
def rnd_px(px,sz_dec,is_perp=True):
    """Hyperliquid price rule: at most 5 significant figures and at most (6 - szDecimals) decimals."""
    if px<=0:raise Refused("bad price")
    d=max(0,6-sz_dec);p=float(f"{px:.5g}");return round(p,d)
def rnd_sz(sz,sz_dec):return math.floor(sz*10**sz_dec)/10**sz_dec
# ---- the checks ---------------------------------------------------------------------------------------------
def check_order(req,c,s,meta,mids):
    """-> list of SDK order requests (validated) and the opening notional in USD (0 for reduce-only)."""
    coin=req.get("coin");side=req.get("side")
    if coin not in c["coins"]:raise Refused(f"{coin} is not an allowed coin")
    if coin not in meta:raise Refused(f"{coin} is not listed on Hyperliquid")
    if side not in("buy","sell"):raise Refused("side must be buy or sell")
    reduce=bool(req.get("reduce_only"));m=meta[coin];mid=mids.get(coin)
    if not mid:raise Refused(f"no price for {coin}")
    sz=rnd_sz(float(req.get("sz") or 0),m["sz_dec"])
    if sz<=0:raise Refused("size rounds to 0")
    kind=req.get("kind","ioc")
    if kind=="ioc":
        px=float(req.get("px") or mid*(1.005 if side=="buy" else 0.995))
        if abs(px/mid-1)>0.05:raise Refused(f"limit price {px} is more than 5% from the market {mid}")
        ot={"limit":{"tif":"Ioc"}}
    elif kind=="alo":px=float(req["px"]);ot={"limit":{"tif":"Alo"}}
    elif kind in("tp","sl"):
        if not reduce:raise Refused("take-profit and stop orders must be reduce-only")
        tp=float(req["trigger"])
        # a stop is a market order when triggered; its limit is the worst price accepted (5% past the trigger), so a
        # gap still closes the position. A take-profit triggers a limit order AT the target price.
        px=tp*(0.95 if side=="sell" else 1.05) if kind=="sl" else tp
        ot={"trigger":{"triggerPx":rnd_px(tp,m["sz_dec"]),"isMarket":kind=="sl","tpsl":kind}}
    else:raise Refused("unknown order kind")
    o=dict(coin=coin,is_buy=side=="buy",sz=sz,limit_px=rnd_px(px,m["sz_dec"]),order_type=ot,reduce_only=reduce)
    notional=0.0 if reduce else sz*mid
    if not reduce:
        if not s.get("master"):raise Refused("no master address set yet (/perps setmaster)")
        if notional>c["max_order_usd"]:raise Refused(f"order ${notional:.2f} over the ${c['max_order_usd']:.0f} per-order limit")
    return [o],notional
def handle(req,make=None):
    c=conf();s=state();op=req.get("op")
    if op=="address":return dict(agent=keyfile()["address"],network=c["network"])
    if op=="status":
        du,no=today(s);out=dict(agent=keyfile()["address"],network=c["network"],master=s.get("master"),limits={k:c[k] for k in c},today_usd=du,today_orders=no,
                                selftest=s.get("selftest"))
        if s.get("master"):
            try:
                h=hl(c,s,make);u=h.user();out["account_value"]=float(((u or {}).get("marginSummary") or {}).get("accountValue") or 0)
                out["positions"]=[dict(coin=p["position"]["coin"],szi=float(p["position"]["szi"]),entry=float(p["position"].get("entryPx") or 0),
                    lev=(p["position"].get("leverage") or {}).get("value"),liq=float(p["position"].get("liquidationPx") or 0) if p["position"].get("liquidationPx") else None,
                    upnl=float(p["position"].get("unrealizedPnl") or 0)) for p in (u or {}).get("assetPositions") or []]
                out["open_orders"]=[dict(coin=o["coin"],oid=o["oid"],side=o.get("side"),sz=float(o.get("sz") or 0),px=float(o.get("limitPx") or 0),
                    trigger=float(o["triggerPx"]) if o.get("triggerPx") else None,reduce_only=o.get("reduceOnly"),kind=o.get("orderType")) for o in h.open_orders() or []]
                out["approved"]=h.approved()
            except Exception as e:out["error"]=f"{type(e).__name__}: {str(e)[:160]}"
        return out
    if op=="set_master":
        with LOCK:
            if s.get("master"):raise Refused("master is already set; change it on the server: copybot perps-master ADDRESS")
            a=req.get("address","").strip()
            if not is_address(a):raise Refused("that is not an EVM address (0x + 40 hex characters)")
            if a.lower()==keyfile()["address"].lower():raise Refused("the master must be YOUR wallet, not the agent")
            state(dict(master=a));_hl.clear();log.info("master set to %s",a);return dict(ok=True,master=a)
    if not s.get("master") and op not in("selftest",):raise Refused("no master address set yet (/perps setmaster)")
    h=hl(c,s,make)
    if op=="positions":
        return dict(positions=handle(dict(op="status"),make).get("positions",[]))
    if op=="fills":return dict(fills=h.fills(int(req.get("since_ms") or 0)))
    if op=="leverage":
        lev=int(req.get("lev") or 0);coin=req.get("coin")
        if coin not in c["coins"]:raise Refused(f"{coin} is not an allowed coin")
        if lev<1 or lev>c["max_leverage"]:raise Refused(f"leverage {lev}x is over the guard's {c['max_leverage']}x limit (raise it on the server: /etc/copybot-perpsguard.json)")
        mx=h.meta().get(coin,{}).get("max_lev",1)
        if lev>mx:raise Refused(f"{coin} allows at most {mx}x")
        return dict(ok=True,result=h.leverage(coin,lev))
    if op=="order":
        meta=h.meta();mids=h.mids();orders=[];total=0.0
        for r in req.get("orders") or []:
            o,n=check_order(r,c,s,meta,mids);orders+=o;total+=n
        if not orders:raise Refused("no orders")
        with LOCK:
            s=state();du,no=today(s)
            if total>0:
                if du+total>c["max_day_usd"]:raise Refused(f"daily limit: ${du:.2f} opened today, ${c['max_day_usd']:.0f} max")
                if no+1>c["max_orders_day"]:raise Refused(f"daily limit: {no} opening orders today")
                u=h.user() or {};av=float(((u.get("marginSummary") or {}).get("accountValue")) or 0)
                held=sum(abs(float(p["position"]["szi"]))*mids.get(p["position"]["coin"],0) for p in u.get("assetPositions") or [])
                if av<=0:raise Refused("the Hyperliquid account has no money (nothing to trade with)")
                if held+total>c["max_leverage"]*av:raise Refused(f"total exposure ${held+total:.2f} would be over {c['max_leverage']}x the account (${av:.2f})")
                lev=int(req.get("lev") or 1)
                if lev>c["max_leverage"]:raise Refused(f"leverage {lev}x is over the guard's {c['max_leverage']}x limit (raise it on the server: /etc/copybot-perpsguard.json)")
                for coin in {o["coin"] for o in orders if not o["reduce_only"]}:h.leverage(coin,min(lev,meta[coin]["max_lev"]))   # isolated margin, at most the guard's cap
            res=h.orders(orders,req.get("grouping","na"))
            if total>0:state(dict(day=time.strftime("%Y-%m-%d",time.gmtime()),day_usd=du+total,day_orders=no+1))
        log.info("orders %s total opening $%.2f -> %s",[(o["coin"],o["is_buy"],o["sz"],o["reduce_only"]) for o in orders],total,str(res)[:200])
        return dict(ok=True,result=res,opening_usd=total)
    if op=="cancel":return dict(ok=True,result=h.cancel(req["coin"],req["oid"]))
    if op=="close_all":
        st=handle(dict(op="status"),make);meta=h.meta();mids=h.mids();out=[]
        for o in st.get("open_orders") or []:
            try:out.append(("cancel",o["coin"],h.cancel(o["coin"],o["oid"])))
            except Exception as e:out.append(("cancel failed",o["coin"],str(e)[:80]))
        for p in st.get("positions") or []:
            if not p["szi"]:continue
            side="sell" if p["szi"]>0 else "buy";mid=mids.get(p["coin"]) or p["entry"]
            o=dict(coin=p["coin"],is_buy=side=="buy",sz=abs(p["szi"]),limit_px=rnd_px(mid*(1.05 if side=="buy" else 0.95),meta.get(p["coin"],{}).get("sz_dec",0)),
                   order_type={"limit":{"tif":"Ioc"}},reduce_only=True)
            try:out.append(("close",p["coin"],h.orders([o])))
            except Exception as e:out.append(("close failed",p["coin"],str(e)[:80]))
        log.warning("close_all: %s",str(out)[:300]);return dict(ok=True,result=out)
    if op=="selftest":
        if c["network"]!="test":raise Refused("selftest runs on testnet only (the network is mainnet)")
        if not s.get("master"):raise Refused("set the master address first (/perps setmaster)")
        coin=req.get("coin") or "SOL";meta=h.meta();mids=h.mids();m=meta[coin];mid=mids[coin]
        steps=[f"agent {h.address()[:8]}…, master {s['master'][:8]}…, testnet"]
        ap=h.approved();steps.append("agent approved by the master" if ap else "agent NOT approved yet: approve it in the Hyperliquid app (API page)" if ap is False else "could not read the approval")
        sz=rnd_sz(max(11/mid,10**-m["sz_dec"]),m["sz_dec"]);px=rnd_px(mid*0.7,m["sz_dec"])
        r=h.orders([dict(coin=coin,is_buy=True,sz=sz,limit_px=px,order_type={"limit":{"tif":"Alo"}},reduce_only=False)])
        oid=None
        try:oid=r["response"]["data"]["statuses"][0]["resting"]["oid"]
        except Exception:pass
        if not oid:raise Refused("test order was not accepted: "+str(r)[:200])
        steps.append(f"test order placed far below the price (oid {oid})")
        cr=h.cancel(coin,oid);steps.append("test order cancelled" if str(cr).find("success")>=0 else "cancel answer: "+str(cr)[:120])
        ok=bool(ap) and str(cr).find("success")>=0;state(dict(selftest=dict(ts=time.time(),ok=ok,steps=steps)))
        return dict(ok=ok,steps=steps)
    raise Refused("unknown op")
def serve():
    os.makedirs(STATE_DIR,exist_ok=True);kf=keyfile()
    try:client_uid=pwd.getpwnam(CLIENT_USER).pw_uid
    except KeyError:client_uid=None
    if os.path.exists(SOCK):os.unlink(SOCK)
    srv=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);srv.bind(SOCK);os.chmod(SOCK,0o660);srv.listen(8)
    log.info("perps guard ready, agent %s, network %s",kf["address"],conf()["network"])
    while True:
        con,_=srv.accept()
        try:
            con.settimeout(60);pid,uid,gid=struct.unpack("3i",con.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid not in(0,client_uid,os.getuid()):con.sendall(b'{"ok":false,"error":"not allowed"}\n');continue
            buf=b""
            while not buf.endswith(b"\n") and len(buf)<1_000_000:
                chunk=con.recv(65536)
                if not chunk:break
                buf+=chunk
            req=json.loads(buf)
            try:out=dict(handle(req));out.setdefault("ok",True)
            except Refused as e:out=dict(ok=False,error=str(e));log.warning("refused %s: %s",req.get("op"),e)
            except Exception as e:out=dict(ok=False,error=f"{type(e).__name__}: {str(e)[:160]}");log.exception("error in %s",req.get("op"))
            con.sendall(json.dumps(out,default=str).encode()+b"\n")
        except Exception as e:log.warning("connection error: %s",e)
        finally:con.close()
if __name__=="__main__":
    if len(sys.argv)>1 and sys.argv[1]=="address":print(keyfile()["address"])
    elif len(sys.argv)>2 and sys.argv[1]=="master":
        if not is_address(sys.argv[2]):sys.exit("not an EVM address")
        state(dict(master=sys.argv[2]));print("master set to",sys.argv[2])
    else:serve()
