"""Signer tests against a fake chain (no network, no real key). Run: python3 tests/test_signer.py
Checks every refusal rule, the entry/exit split (exits are never blocked by buy limits) and withdrawals."""
import sys,os,json,base64,struct,threading,time,tempfile,socket,hashlib
from http.server import HTTPServer,BaseHTTPRequestHandler
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,os.path.join(R,"signer"))
D=tempfile.mkdtemp();PORT=18999
os.environ.update(SIGNER_STATE=D,SIGNER_SOCK=os.path.join(D,"s.sock"),SIGNER_CONF=os.path.join(D,"conf.json"),
    SIGNER_RPC=f"http://127.0.0.1:{PORT}/rpc",JUP_BASE=f"http://127.0.0.1:{PORT}",SIGNER_CLIENT="root")
import solwire as W,signer as S
kp=S.keypair();WALLET=kp[32:];WB=W.b58e(WALLET)
def addr(tag):return hashlib.sha256(tag.encode()).digest()
USDC_ACC=W.ata(WALLET,W.USDC);WSOL_ACC=W.ata(WALLET,W.WSOL);ATTACKER=W.new_keypair()[32:]
def tok_data(mint,owner,amount,delegate=None):
    return mint+owner+struct.pack("<Q",amount)+(struct.pack("<I",1)+delegate if delegate else struct.pack("<I",0)+b"\0"*32)+bytes([1])+b"\0"*56
SC0=dict(pre_sol=1_000_000_000,pre_usdc=30_000_000,post_sol=1_066_000_000,post_usdc=20_000_000,delegate=None,owner=WALLET,err=None,px=150.0,home_usdc=False)
SC=dict(SC0)
def acc(lam,data):return {"lamports":lam,"data":[base64.b64encode(data).decode(),"base64"],"owner":"x"}
def view(a,post):
    if a==WB:return acc(SC["post_sol" if post else "pre_sol"],b"")
    if a==W.b58e(USDC_ACC):return acc(2039280,tok_data(W.USDC,SC["owner"] if post else WALLET,SC["post_usdc" if post else "pre_usdc"],SC["delegate"] if post else None))
    return None
class H(BaseHTTPRequestHandler):
    def log_message(s,*a):pass
    def do_GET(s):
        body=json.dumps({W.b58e(W.WSOL):{"usdPrice":SC["px"]}}).encode();s.send_response(200);s.end_headers();s.wfile.write(body)
    def do_POST(s):
        req=json.loads(s.rfile.read(int(s.headers["content-length"])));m=req["method"];p=req["params"]
        if m=="getTokenAccountsByOwner":res={"value":[{"pubkey":W.b58e(USDC_ACC)}] if p[0]==WB and W.pk(p[1]["mint"])==W.USDC else []}
        elif m=="getMultipleAccounts":res={"value":[view(a,False) for a in p[0]]}
        elif m=="simulateTransaction":
            res={"value":{"err":SC["err"],"accounts":None}} if SC["err"] else {"value":{"err":None,"unitsConsumed":120000,"accounts":[view(a,True) for a in p[1]["accounts"]["addresses"]]}}
        elif m=="getLatestBlockhash":res={"value":{"blockhash":W.b58e(addr("bh")),"lastValidBlockHeight":1000}}
        elif m=="getBalance":res={"value":SC["pre_sol"]}
        else:res=None
        body=json.dumps({"jsonrpc":"2.0","id":1,"result":res}).encode();s.send_response(200);s.end_headers();s.wfile.write(body)
srv=HTTPServer(("127.0.0.1",PORT),H);threading.Thread(target=srv.serve_forever,daemon=True).start()
def jup_like(extra=None,signers=1,payer=None,cu_price=50_000,cu_limit=600_000,wsol=None,close_to=None,ata_owner=None):
    """A v0 transaction shaped like a real Jupiter SOL<->USDC swap: create wSOL account, wrap, sync, swap, close."""
    keys=[payer or WALLET]+([addr("cosigner")] if signers==2 else [])+[wsol or WSOL_ACC,USDC_ACC,W.WSOL,W.USDC,W.COMPUTE,W.ATA,W.SYSTEM,W.TOKEN,W.JUPITER,ATTACKER]
    i=keys.index;o=signers-1
    ixs=[(i(W.COMPUTE),[],bytes([2])+struct.pack("<I",cu_limit)),(i(W.COMPUTE),[],bytes([3])+struct.pack("<Q",cu_price)),
         (i(W.ATA),[0,1+o,i(ata_owner) if ata_owner else 0,3+o,i(W.SYSTEM),i(W.TOKEN)],b"\x01"),(i(W.SYSTEM),[0,1+o],struct.pack("<IQ",2,66_000_000)),(i(W.TOKEN),[1+o],bytes([17])),
         (i(W.JUPITER),[0,1+o,2+o,3+o,4+o,len(keys),len(keys)+1],bytes(range(30))),(i(W.TOKEN),[1+o,i(close_to) if close_to else 0,0],bytes([9]))]+(extra(keys) if extra else [])
    m=bytes([0x80,signers,0,len(keys)-signers-3])+W.cu16(len(keys))+b"".join(keys)+addr("bh")+W.cu16(len(ixs))
    for p,a,d in ixs:m+=bytes([p])+W.cu16(len(a))+bytes(a)+W.cu16(len(d))+d
    m+=W.cu16(1)+addr("alt")+W.cu16(2)+bytes([0,1])+W.cu16(1)+bytes([2])        # one lookup table
    return W.cu16(signers)+b"\0"*64*signers+m
def call(op,**k):
    s=socket.socket(socket.AF_UNIX);s.connect(os.environ["SIGNER_SOCK"]);s.sendall(json.dumps(dict(op=op,**k)).encode()+b"\n")
    buf=b""
    while not buf.endswith(b"\n"):
        c=s.recv(65536)
        if not c:break
        buf+=c
    return json.loads(buf)
threading.Thread(target=S.serve,daemon=True).start();time.sleep(0.5)
b64=lambda raw:base64.b64encode(raw).decode();res={}
def reset_day():S.state(dict(day="2000-01-01",day_usd=0,day_swaps=0,day_sells=0,day_fee=0))
def sign(tx=None,**sc):
    SC.clear();SC.update(SC0);SC.update(sc);r=call("sign_swap",tx=b64(tx or jup_like()));return r
def refused(name,tx=None,expect="",**sc):
    r=sign(tx,**sc);ok=(not r.get("ok")) and expect.lower() in r.get("error","").lower();res["refuses "+name]=ok
    if not ok:print("  ",name,"->",r)
# ---- parsing and derivations
t=W.Tx(jup_like());res["parse v0 with lookup table"]=t.version==0 and len(t.ix)==7 and len(t.lookups)==1 and t.keys[0]==WALLET
res["ATA is a program address (off curve)"]=not W.on_curve(USDC_ACC) and USDC_ACC!=WSOL_ACC
# ---- honest swaps
r=sign();tx=W.Tx(base64.b64decode(r["tx"])) if r.get("ok") else None
res["honest buy signed, signature verifies"]=bool(tx) and r["side"]=="buy" and W.verify(WALLET,tx.raw[1:65],tx.msg)
c=call("check_swap",tx=b64(jup_like()));res["check_swap does not sign"]=c.get("ok") and "tx" not in c
res["check_static works without simulation"]=call("check_static",tx=b64(jup_like())).get("ok")
# ---- instruction rules
refused("token Approve (delegate)",jup_like(lambda k:[(k.index(W.TOKEN),[2,0,0],bytes([4])+struct.pack("<Q",10**9))]),"token instruction 4")
refused("token Transfer out",jup_like(lambda k:[(k.index(W.TOKEN),[2,1,0],bytes([3])+struct.pack("<Q",10**9))]),"token instruction 3")
refused("unknown program",jup_like(lambda k:[(k.index(W.USDC),[0],b"\x00")]),"program not allowed")
refused("system CreateAccount",jup_like(lambda k:[(k.index(W.SYSTEM),[0,1],struct.pack("<IQQ",0,1,1)+b"\0"*32)]),"system instruction")
refused("SOL sent to a stranger",jup_like(lambda k:[(k.index(W.SYSTEM),[0,k.index(ATTACKER)],struct.pack("<IQ",2,1_000_000))]),"not this wallet's")
refused("SOL sent via a lookup-table account",jup_like(lambda k:[(k.index(W.SYSTEM),[0,len(k)],struct.pack("<IQ",2,1_000_000))]),"not this wallet's")
refused("wSOL account closed to a stranger",jup_like(close_to=ATTACKER),"closed to someone else")
refused("token account created for a stranger",jup_like(ata_owner=ATTACKER),"for someone else")
tmp=addr("temp-wsol");r=sign(jup_like(wsol=tmp))
res["wrap into a temp account closed back to the wallet is allowed"]=r.get("ok") is True
if not r.get("ok"):print("   temp ->",r)
refused("second signer",jup_like(signers=2),"one signer")
refused("someone else pays",jup_like(payer=ATTACKER),"not this wallet")
refused("priority fee over cap",jup_like(cu_price=10_000_000,cu_limit=1_000_000),"priority fee")
# ---- simulation rules
refused("simulation error",None,"simulation failed",err={"InstructionError":[5,"Custom"]})
refused("USDC account gets a delegate",None,"delegate",delegate=ATTACKER)
refused("USDC account changes owner",None,"owner",owner=ATTACKER)
refused("SOL drained to someone else",None,"lose",post_sol=500_000_000,post_usdc=31_000_000)
refused("USDC and SOL both drop",None,"not a sol/usdc swap",post_sol=990_000_000,post_usdc=29_000_000)
refused("bad price (pays $10 for $5 of SOL)",None,"lose",post_sol=1_033_000_000,post_usdc=20_000_000)
refused("buy over $20",None,"per-swap limit",pre_usdc=60_000_000,post_usdc=30_000_000,post_sol=1_199_000_000)
# ---- entries vs exits
reset_day()
for k in range(12):last=sign()
res["daily buy limit enforced (10 buys)"]=(not last.get("ok")) and "daily buy limit" in last.get("error","")
SELL=dict(pre_sol=1_000_000_000,post_sol=800_000_000,pre_usdc=0,post_usdc=29_900_000)      # sell 0.2 SOL for $29.90
r=sign(**SELL);res["sell still works after the buy limit is used up"]=r.get("ok") and r.get("side")=="sell"
res["sell larger than $20 allowed (position grew)"]=r.get("ok") and r.get("trade_usd",0)>20
st=S.state();res["sells do not use the buy allowance"]=st["day_swaps"]==10 and st["day_sells"]==1
refused("sell that leaves no SOL for fees",None,"for fees",pre_sol=1_000_000_000,post_sol=1_000_000,pre_usdc=0,post_usdc=149_000_000)
refused("sell at a bad price",None,"lose",pre_sol=1_000_000_000,post_sol=800_000_000,pre_usdc=0,post_usdc=25_000_000)
S.state(dict(day_sells=30));refused("sell sanity cap (30 a day)",None,"sanity cap",**SELL)
S.state(dict(day=time.strftime("%Y-%m-%d",time.gmtime()),day_usd=0,day_swaps=0,day_sells=0,day_fee=10_000_000));refused("buy when the daily fee budget is used",None,"fee budget")
r=sign(**SELL);res["sell allowed even when the fee budget is used"]=r.get("ok") is True
reset_day()
# ---- home address and withdrawals
pda=next(addr(f"p{i}") for i in range(100) if not W.on_curve(addr(f"p{i}")))
res["home refuses a program address"]="program address" in call("set_home",address=W.b58e(pda)).get("error","")
res["home refuses the bot wallet itself"]="different wallet" in call("set_home",address=WB).get("error","")
HOME=W.new_keypair()[32:];r=call("set_home",address=W.b58e(HOME));res["home set once"]=r.get("ok")
res["home cannot be changed from the bot"]="already set" in call("set_home",address=W.b58e(W.new_keypair()[32:])).get("error","")
SC.clear();SC.update(SC0)
r=call("withdraw",kind="sol");t=W.Tx(base64.b64decode(r["tx"]))
res["withdraw SOL goes only to home"]=r.get("ok") and t.keys[1]==HOME and struct.unpack("<IQ",t.ix[0]["data"])==(2,SC["pre_sol"]-5000) and W.verify(WALLET,t.raw[1:65],t.msg)
r=call("withdraw",kind="sol",amount=2_000_000);t=W.Tx(base64.b64decode(r["tx"]))
res["test withdraw sends only the amount asked"]=r.get("ok") and struct.unpack("<IQ",t.ix[0]["data"])==(2,2_000_000) and t.keys[1]==HOME
r=call("withdraw",kind="usdc");t=W.Tx(base64.b64decode(r["tx"]))
home_ata=W.ata(HOME,W.USDC)
res["withdraw USDC creates home's USDC account if missing"]=r.get("ok") and t.program(t.ix[0])==W.ATA and t.keys[t.ix[0]["accounts"][2]]==HOME and t.keys[t.ix[0]["accounts"][1]]==home_ata
res["withdraw USDC transfers everything to home's account"]=r.get("ok") and t.ix[1]["data"][0]==12 and t.keys[t.ix[1]["accounts"][2]]==home_ata and struct.unpack("<Q",t.ix[1]["data"][1:9])[0]==SC["pre_usdc"]
r=call("withdraw",kind="usdc",amount=1_000_000);t=W.Tx(base64.b64decode(r["tx"]))
res["test withdraw USDC: $1 only"]=r.get("ok") and struct.unpack("<Q",t.ix[1]["data"][1:9])[0]==1_000_000
res["withdraw refuses a bad amount"]="bad amount" in call("withdraw",kind="sol",amount=-5).get("error","")
st=call("status");res["status shows limits and today's sells"]=st.get("limits",{}).get("max_swap_usd")==20.0 and "today_sells" in st
res["key file is private"]=oct(os.stat(os.path.join(D,"wallet.json")).st_mode)[-3:]=="600"
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
