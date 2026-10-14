#!/usr/bin/env python3
"""ZETRAX signer: the only process that can read the live wallet's private key. Runs as its own user (copybot-signer),
installed root-owned outside /opt/copybot so the bot cannot change its code. The bot talks to it over a Unix socket
and can only ask it to:
  pubkey / status                -> wallet address, home address, limits, today's usage
  check_swap(tx)                 -> run every check + a simulation, never signs (used by /live selftest)
  sign_swap(tx)                  -> sign a Jupiter swap between SOL and USDC that passes every check below
  set_home(address)              -> set the owner's home address ONCE (changing it needs the server console)
  withdraw(kind)                 -> build + sign a transfer of the wallet's SOL or USDC to the home address only
A swap is signed only if: 1 signer = this wallet; every top-level program is ComputeBudget, System (transfer only),
Token (sync/close only), Associated Token or Jupiter v6; priority fee under the cap; the simulation succeeds; the
wallet's USDC accounts keep this wallet as owner and get no delegate; SOL and USDC move in opposite directions; the USDC
leg is within the per-swap and daily limits; and the wallet loses no more value than max_loss_pct of the trade plus fees,
priced by the signer's own SOL price. So even a fully compromised bot can only swap SOL<->USDC inside this wallet,
within the limits, or send funds home."""
import os,sys,json,time,socket,struct,base64,pwd,logging,threading,urllib.request
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import solwire as W
logging.basicConfig(level=logging.INFO,format="%(asctime)s signer %(levelname)s %(message)s");log=logging.getLogger("signer")
STATE_DIR=os.environ.get("SIGNER_STATE","/var/lib/copybot-signer");SOCK=os.environ.get("SIGNER_SOCK","/run/copybot-signer/signer.sock")
CONF=os.environ.get("SIGNER_CONF","/etc/copybot-signer.json");CLIENT_USER=os.environ.get("SIGNER_CLIENT","copybot")
DEFAULTS=dict(max_swap_usd=20.0,max_day_usd=100.0,max_day_swaps=10,max_loss_pct=0.025,max_priority_lamports=300000,fee_allow_sol=0.003)
COMPUTE_OK={2,3,4};TOKEN_OK={9,17};SYSTEM_OK={2}
LOCK=threading.Lock()
class Refused(Exception):pass
def conf():
    try:c=json.load(open(CONF))
    except Exception:c={}
    return dict(DEFAULTS,**{k:v for k,v in c.items() if k in DEFAULTS})
def keypair():
    p=os.path.join(STATE_DIR,"wallet.json")
    if not os.path.exists(p):
        kp=W.new_keypair();fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w") as f:json.dump(list(kp),f)
        log.info("created new wallet %s",W.b58e(kp[32:]))
    kp=bytes(json.load(open(p)));assert len(kp)==64
    return kp
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
    return (s.get("day_usd",0.0),s.get("day_swaps",0)) if s.get("day")==d else (0.0,0)
# ---- network -----------------------------------------------------------------------------
def rpc(method,params):
    k=os.environ.get("HELIUS_API_KEY","").strip()
    u=os.environ.get("SIGNER_RPC") or ("https://mainnet.helius-rpc.com/?api-key="+k)
    r=json.load(urllib.request.urlopen(urllib.request.Request(u,json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode(),{"content-type":"application/json"}),timeout=20))
    if "error" in r:raise Refused(f"{method}: {r['error'].get('message')}")
    return r["result"]
def sol_price():
    h={"user-agent":"copybot-signer"};k=os.environ.get("JUPITER_API_KEY","").strip()
    if k:h["x-api-key"]=k
    base=os.environ.get("JUP_BASE","https://api.jup.ag")
    r=json.load(urllib.request.urlopen(urllib.request.Request(base+"/price/v3?ids="+W.b58e(W.WSOL),headers=h),timeout=15))
    p=float(r[W.b58e(W.WSOL)]["usdPrice"])
    if not 1<p<100000:raise Refused("SOL price looks wrong")
    return p
def token_accounts(owner,mint):
    r=rpc("getTokenAccountsByOwner",[W.b58e(owner),{"mint":W.b58e(mint)},{"encoding":"base64","commitment":"confirmed"}])
    return [x["pubkey"] for x in r["value"]]
def balances(addrs,acc_infos):
    """lamports of the wallet + token amounts by mint, from account infos aligned with addrs (None = no account)."""
    sol=0;usdc=0;wsol=0;toks=[]
    for i,(a,x) in enumerate(zip(addrs,acc_infos)):
        if not x:continue
        data=base64.b64decode(x["data"][0]) if isinstance(x.get("data"),list) else b""
        if i==0:sol=x["lamports"];continue
        t=W.token_account(data)
        if not t:continue
        if t["mint"]==W.USDC:usdc+=t["amount"]
        elif t["mint"]==W.WSOL:wsol+=t["amount"]
        toks.append(t)
    return sol+wsol,usdc,toks
# ---- the checks ------------------------------------------------------------------------------
def static_checks(tx,wallet,c):
    if tx.nsig!=1 or tx.n_req!=1:raise Refused("transaction must have exactly one signer")
    if tx.keys[0]!=wallet:raise Refused("fee payer / signer is not this wallet")
    unit_limit=1_400_000;unit_price=0
    for ix in tx.ix:
        p=tx.program(ix);d=ix["data"]
        if p==W.COMPUTE:
            if not d or d[0] not in COMPUTE_OK:raise Refused("compute budget instruction not allowed")
            if d[0]==2:unit_limit=struct.unpack("<I",d[1:5])[0]
            if d[0]==3:unit_price=struct.unpack("<Q",d[1:9])[0]
        elif p==W.SYSTEM:
            if len(d)<4 or struct.unpack("<I",d[:4])[0] not in SYSTEM_OK:raise Refused("system instruction not allowed")
        elif p==W.TOKEN:
            if not d or d[0] not in TOKEN_OK:raise Refused(f"token instruction {d[0] if d else '?'} not allowed")
        elif p==W.ATA:
            if d not in(b"",b"\x00",b"\x01"):raise Refused("associated token instruction not allowed")
        elif p==W.JUPITER:pass
        else:raise Refused("program not allowed: "+W.b58e(p))
    fee=unit_price*unit_limit//1_000_000
    if fee>c["max_priority_lamports"]:raise Refused(f"priority fee {fee} lamports over the cap")
    return fee
def simulate_swap(tx,wallet,c):
    """Simulate and measure the wallet's SOL/USDC change. Returns dict or raises Refused."""
    addrs=[W.b58e(wallet)]+token_accounts(wallet,W.USDC)+token_accounts(wallet,W.WSOL)
    pre=rpc("getMultipleAccounts",[addrs,{"encoding":"base64","commitment":"confirmed"}])["value"]
    sim=rpc("simulateTransaction",[base64.b64encode(tx.raw).decode(),{"encoding":"base64","sigVerify":False,"replaceRecentBlockhash":False,
        "commitment":"confirmed","accounts":{"encoding":"base64","addresses":addrs}}])["value"]
    if sim.get("err"):raise Refused(f"simulation failed: {json.dumps(sim['err'])[:160]}")
    s0,u0,_=balances(addrs,pre);s1,u1,post=balances(addrs,sim.get("accounts") or [])
    for t in post:
        if t["owner"]!=wallet:raise Refused("a token account would change owner")
        if t["delegate"]:raise Refused("a token account would get a delegate")
    return dict(d_sol=(s1-s0)/1e9,d_usdc=(u1-u0)/1e6,units=sim.get("unitsConsumed"))
def value_checks(sim,c,px):
    ds,du=sim["d_sol"],sim["d_usdc"]
    if not((ds>0 and du<0) or (ds<0 and du>0)):raise Refused(f"not a SOL/USDC swap (SOL {ds:+.6f}, USDC {du:+.4f})")
    trade=abs(du)
    if trade>c["max_swap_usd"]:raise Refused(f"swap ${trade:.2f} over the ${c['max_swap_usd']:.0f} limit")
    loss=-(du+ds*px)
    if loss>c["max_loss_pct"]*trade+c["fee_allow_sol"]*px:raise Refused(f"wallet would lose ${loss:.3f} on a ${trade:.2f} swap")
    return trade,loss
def handle(req,kp):
    wallet=kp[32:];c=conf();op=req.get("op")
    if op=="pubkey":return dict(wallet=W.b58e(wallet))
    if op=="status":
        s=state();du,ns=today(s);return dict(wallet=W.b58e(wallet),home=s.get("home"),limits=c,today_usd=du,today_swaps=ns)
    if op in("check_swap","sign_swap"):
        tx=W.Tx(base64.b64decode(req["tx"]));steps=[];fee=static_checks(tx,wallet,c);steps.append(f"programs and signer ok, priority fee {fee} lamports")
        px=sol_price();sim=simulate_swap(tx,wallet,c);steps.append(f"simulation ok: SOL {sim['d_sol']:+.6f}, USDC {sim['d_usdc']:+.4f}")
        trade,loss=value_checks(sim,c,px);steps.append(f"value ok: ${trade:.2f} swap, ${loss:.4f} cost at SOL ${px:.2f}")
        with LOCK:
            s=state();du,ns=today(s)
            if du+trade>c["max_day_usd"] or ns+1>c["max_day_swaps"]:raise Refused(f"daily limit: ${du:.2f} in {ns} swaps already today")
            if op=="check_swap":return dict(ok=True,steps=steps)
            sig=W.sign_message(kp,tx.msg)
            state(dict(day=time.strftime("%Y-%m-%d",time.gmtime()),day_usd=du+trade,day_swaps=ns+1))
        log.info("signed swap $%.2f (SOL %+.6f USDC %+.4f)",trade,sim["d_sol"],sim["d_usdc"])
        return dict(ok=True,tx=base64.b64encode(tx.signed(sig)).decode(),steps=steps,trade_usd=trade,d_sol=sim["d_sol"],d_usdc=sim["d_usdc"])
    if op=="set_home":
        with LOCK:
            s=state()
            if s.get("home"):raise Refused("home address is already set; change it on the server: copybot sethome ADDRESS")
            a=req.get("address","").strip();b=W.pk(a)
            if not W.on_curve(b):raise Refused("that is a program address, not a wallet; use your Phantom address")
            if b==wallet:raise Refused("home must be a different wallet (your Phantom)")
            state(dict(home=a));log.info("home address set to %s",a);return dict(ok=True,home=a)
    if op=="withdraw":
        s=state();home=s.get("home")
        if not home:raise Refused("no home address set (/sethome)")
        bh=rpc("getLatestBlockhash",[{"commitment":"confirmed"}])["value"];blockhash=W.pk(bh["blockhash"])
        if req.get("kind")=="usdc":
            src=token_accounts(wallet,W.USDC)
            dst=[a for a in token_accounts(W.pk(home),W.USDC)]
            if not src:raise Refused("wallet has no USDC account")
            if not dst:raise Refused("your home wallet has no USDC account yet; withdraw SOL instead")
            info=rpc("getMultipleAccounts",[[src[0]],{"encoding":"base64"}])["value"][0];amt=W.token_account(base64.b64decode(info["data"][0]))["amount"]
            if amt<=0:raise Refused("no USDC to withdraw")
            raw=W.usdc_transfer(wallet,W.pk(src[0]),W.pk(dst[0]),amt,blockhash)
        else:
            lam=rpc("getBalance",[W.b58e(wallet),{"commitment":"confirmed"}])["value"]
            amt=lam-5000            # everything but the network fee; an empty wallet account is allowed
            if amt<=0:raise Refused("no SOL to withdraw")
            raw=W.sol_transfer(wallet,W.pk(home),amt,blockhash)
        tx=W.Tx(raw);signed=tx.signed(W.sign_message(kp,tx.msg))
        log.info("signed withdraw %s %s to home",req.get("kind","sol"),amt)
        return dict(ok=True,tx=base64.b64encode(signed).decode(),amount=amt,kind=req.get("kind","sol"),home=home,last_valid=bh["lastValidBlockHeight"])
    raise Refused("unknown op")
def serve():
    os.makedirs(STATE_DIR,exist_ok=True);kp=keypair()
    try:client_uid=pwd.getpwnam(CLIENT_USER).pw_uid
    except KeyError:client_uid=None
    if os.path.exists(SOCK):os.unlink(SOCK)
    srv=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);srv.bind(SOCK);os.chmod(SOCK,0o660);srv.listen(8)
    log.info("signer ready, wallet %s",W.b58e(kp[32:]))
    while True:
        con,_=srv.accept()
        try:
            con.settimeout(30);pid,uid,gid=struct.unpack("3i",con.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid not in(0,client_uid,os.getuid()):con.sendall(b'{"ok":false,"error":"not allowed"}\n');continue
            buf=b""
            while not buf.endswith(b"\n") and len(buf)<1_000_000:
                chunk=con.recv(65536)
                if not chunk:break
                buf+=chunk
            req=json.loads(buf)
            try:out=dict(handle(req,kp));out.setdefault("ok",True)
            except Refused as e:out=dict(ok=False,error=str(e));log.warning("refused %s: %s",req.get("op"),e)
            except Exception as e:out=dict(ok=False,error=f"{type(e).__name__}: {str(e)[:160]}");log.exception("error in %s",req.get("op"))
            con.sendall(json.dumps(out).encode()+b"\n")
        except Exception as e:log.warning("connection error: %s",e)
        finally:con.close()
if __name__=="__main__":
    if len(sys.argv)>1 and sys.argv[1]=="address":print(W.b58e(keypair()[32:]))
    elif len(sys.argv)>2 and sys.argv[1]=="sethome":
        b=W.pk(sys.argv[2])
        if not W.on_curve(b):sys.exit("that is a program address, not a wallet")
        state(dict(home=sys.argv[2]));print("home set to",sys.argv[2])
    elif len(sys.argv)>1 and sys.argv[1]=="export":print(W.b58e(keypair()))
    else:serve()
