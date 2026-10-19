"""The coin guard (signer.py in SPOT mode) against a fake chain: no network, no real key. Run: python3 tests/test_signer_spot.py
Checks: honest USDC->coin buys and coin->USDC sells are signed; buys of dangerous coins (mint/freeze authority, Token-2022
hook, fee, permanent delegate) are refused; caps, value and SOL checks; sells are never blocked by the buy limits, a
missing price or the coin's flags; a rigged sell route is refused; the SOL/USDC guard still refuses Token-2022."""
import sys,os,json,base64,struct,threading,time,tempfile,socket,hashlib
from http.server import HTTPServer,BaseHTTPRequestHandler
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,os.path.join(R,"signer"))
D=tempfile.mkdtemp();PORT=18997
os.environ.update(SIGNER_MODE="spot",SIGNER_STATE=D,SIGNER_SOCK=os.path.join(D,"s.sock"),SIGNER_CONF=os.path.join(D,"conf.json"),
    SIGNER_RPC=f"http://127.0.0.1:{PORT}/rpc",JUP_BASE=f"http://127.0.0.1:{PORT}",SIGNER_CLIENT="root")
import solwire as W,signer as S
kp=S.keypair();WALLET=kp[32:];WB=W.b58e(WALLET)
def addr(tag):return hashlib.sha256(tag.encode()).digest()
COIN=addr("coin");CB=W.b58e(COIN);USDC_ACC=W.ata(WALLET,W.USDC);ATTACKER=W.new_keypair()[32:]
def tok_data(mint,owner,amount,delegate=None):
    return mint+owner+struct.pack("<Q",amount)+(struct.pack("<I",1)+delegate if delegate else struct.pack("<I",0)+b"\0"*32)+bytes([1])+b"\0"*56
def mint_data(mint_auth=False,freeze=False,dec=6,ext=None):
    d=(struct.pack("<I",1)+addr("ma") if mint_auth else struct.pack("<I",0)+b"\0"*32)+struct.pack("<Q",10**15)+bytes([dec,1])+\
      (struct.pack("<I",1)+addr("fa") if freeze else struct.pack("<I",0)+b"\0"*32)
    if ext is None:return d
    d=d+b"\0"*(165-len(d))+bytes([1])
    for t,v in ext:d+=struct.pack("<HH",t,len(v))+v
    return d
SC0=dict(pre_sol=50_000_000,post_sol=47_960_000,pre_usdc=30_000_000,post_usdc=20_000_000,pre_tok=0,post_tok=1_000_000_000,px=0.0099,
         mi=dict(),t22=False,delegate=None)
SC=dict(SC0)
def coin_acc():return W.ata(WALLET,COIN,W.TOKEN22 if SC["t22"] else W.TOKEN)
def acc(lam,data,owner="x"):return {"lamports":lam,"data":[base64.b64encode(data).decode(),"base64"],"owner":owner}
def view(a,post):
    if a==WB:return acc(SC["post_sol" if post else "pre_sol"],b"")
    if a==W.b58e(USDC_ACC):return acc(2039280,tok_data(W.USDC,WALLET,SC["post_usdc" if post else "pre_usdc"],SC["delegate"] if post else None))
    if a==W.b58e(coin_acc()):
        amt=SC["post_tok" if post else "pre_tok"]
        return acc(2039280,tok_data(COIN,WALLET,amt)+(b"\0"*10 if SC["t22"] else b"")) if (amt or post) else None
    return None
class H(BaseHTTPRequestHandler):
    def log_message(s,*a):pass
    def do_GET(s):
        body=json.dumps({CB:{"usdPrice":SC["px"]}} if SC["px"] else {}).encode();s.send_response(200);s.end_headers();s.wfile.write(body)
    def do_POST(s):
        req=json.loads(s.rfile.read(int(s.headers["content-length"])));m=req["method"];p=req["params"]
        if m=="getAccountInfo":
            res={"value":acc(1461600,mint_data(**SC["mi"]),W.b58e(W.TOKEN22 if SC["t22"] else W.TOKEN))} if p[0]==CB else {"value":None}
        elif m=="getTokenAccountsByOwner":res={"value":[{"pubkey":W.b58e(USDC_ACC)}] if W.pk(p[1]["mint"])==W.USDC else []}
        elif m=="getMultipleAccounts":res={"value":[view(a,False) for a in p[0]]}
        elif m=="simulateTransaction":res={"value":{"err":None,"unitsConsumed":150000,"accounts":[view(a,True) for a in p[1]["accounts"]["addresses"]]}}
        else:res=None
        body=json.dumps({"jsonrpc":"2.0","id":1,"result":res}).encode();s.send_response(200);s.end_headers();s.wfile.write(body)
srv=HTTPServer(("127.0.0.1",PORT),H);threading.Thread(target=srv.serve_forever,daemon=True).start()
def jup_coin(token_prog=None,extra=None):
    """A v0 transaction shaped like a Jupiter USDC<->coin swap: create the coin account (idempotent), swap."""
    tp=token_prog or W.TOKEN;ca=W.ata(WALLET,COIN,tp)
    keys=[WALLET,ca,USDC_ACC,COIN,W.USDC,W.COMPUTE,W.ATA,W.SYSTEM,tp,W.JUPITER,ATTACKER];i=keys.index
    ixs=[(i(W.COMPUTE),[],bytes([2])+struct.pack("<I",600_000)),(i(W.COMPUTE),[],bytes([3])+struct.pack("<Q",50_000)),
         (i(W.ATA),[0,1,0,3,i(W.SYSTEM),i(tp)],b"\x01"),(i(W.JUPITER),[0,1,2,3,4,len(keys)],bytes(range(30)))]+(extra(keys) if extra else [])
    m=bytes([0x80,1,0,len(keys)-4])+W.cu16(len(keys))+b"".join(keys)+addr("bh")+W.cu16(len(ixs))
    for p,a,d in ixs:m+=bytes([p])+W.cu16(len(a))+bytes(a)+W.cu16(len(d))+d
    m+=W.cu16(1)+addr("alt")+W.cu16(1)+bytes([0])+W.cu16(0)
    return W.cu16(1)+b"\0"*64+m
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
def sign(tx=None,mint=CB,**sc):
    SC.clear();SC.update(SC0);SC.update(sc);return call("sign_swap",tx=b64(tx or jup_coin(W.TOKEN22 if SC["t22"] else None)),mint=mint)
def refused(name,expect="",tx=None,**sc):
    r=sign(tx,**sc);ok=(not r.get("ok")) and expect.lower() in r.get("error","").lower();res["refuses "+name]=ok
    if not ok:print("  ",name,"->",r)
SELL=dict(pre_tok=1_000_000_000,post_tok=0,pre_usdc=20_000_000,post_usdc=29_500_000,pre_sol=47_960_000,post_sol=49_990_000)
# ---- honest swaps
st=call("status");res["spot mode: own limits ($10 per buy, $50 a day)"]=st.get("mode")=="spot" and st["limits"]["max_swap_usd"]==10 and st["limits"]["max_day_usd"]==50
r=sign();tx=W.Tx(base64.b64decode(r["tx"])) if r.get("ok") else None
res["honest buy of a clean coin signed, signature verifies"]=bool(tx) and r["side"]=="buy" and r["d_tok"]==1_000_000_000 and W.verify(WALLET,tx.raw[1:65],tx.msg)
r=sign(**SELL);res["honest sell signed"]=r.get("ok") and r.get("side")=="sell"
r=sign(t22=True,mi=dict(ext=[(18,b"\0"*64)]));res["Token-2022 coin with harmless metadata: buy signed"]=r.get("ok") and r.get("side")=="buy"
res["check_swap does not sign"]=(lambda c:c.get("ok") and "tx" not in c)(call("check_swap",tx=b64(jup_coin()),mint=CB))
# ---- dangerous coins (buys only)
refused("coin whose creator can still mint","can still mint",mi=dict(mint_auth=True))
refused("coin that can be frozen","frozen",mi=dict(freeze=True))
refused("Token-2022 transfer hook","transfer hook",t22=True,mi=dict(ext=[(14,b"\0"*32+addr("hook"))]))
refused("Token-2022 transfer fee","transfer fee",t22=True,mi=dict(ext=[(1,b"\0"*88+struct.pack("<H",100)+b"\0"*16+struct.pack("<H",100))]))
refused("Token-2022 permanent delegate","permanent delegate",t22=True,mi=dict(ext=[(12,addr("pd"))]))
refused("Token-2022 pausable","pausable",t22=True,mi=dict(ext=[(26,addr("pa")+b"\0")]))
refused("Token-2022 accounts frozen by default","start frozen",t22=True,mi=dict(ext=[(6,b"\x02")]))
# ---- value, size and SOL
refused("buy over the $10 cap","per-swap limit",post_usdc=18_000_000,post_tok=1_200_000_000)
refused("buy far above the market price","worth",px=0.008)
refused("buy with no market price","no market price",px=None)
refused("swap that takes SOL from the wallet","take",post_sol=40_000_000)
refused("USDC leaves but no coin arrives","not a usdc/coin swap",post_tok=0)
refused("USDC account gets a delegate","delegate",delegate=ATTACKER)
refused("swap without naming the coin","name the coin",mint="")
refused("USDC named as the coin","usdc <-> coins only",mint=W.b58e(W.USDC))
refused("SOL sent to a stranger","not this wallet's",tx=jup_coin(extra=lambda k:[(k.index(W.SYSTEM),[0,k.index(ATTACKER)],struct.pack("<IQ",2,1_000_000))]))
refused("token Transfer out","token instruction 3",tx=jup_coin(extra=lambda k:[(k.index(W.TOKEN),[2,1,0],bytes([3])+struct.pack("<Q",10**6))]))
refused("rigged sell route (gets $2 for $9.90 of coins)","worth",**dict(SELL,post_usdc=22_000_000))
# ---- sells are never blocked
reset_day()
for i in range(5):sign()
refused("6th buy over the $50 daily limit","daily buy limit")
r=sign(**SELL);res["sell still signed after the daily buy limit is used up"]=r.get("ok") and r["side"]=="sell"
r=sign(px=None,**SELL);res["sell signed with no market price (exits never wait for data)"]=r.get("ok")
r=sign(mi=dict(freeze=True,mint_auth=True),**SELL);res["sell of a flagged coin still signed (only buys check the coin)"]=r.get("ok")
# ---- the SOL/USDC guard is unchanged: no Token-2022 program
S.MODE="sol"
try:S.static_checks(W.Tx(jup_coin(W.TOKEN22,extra=lambda k:[(k.index(W.TOKEN22),[1],bytes([17]))])),WALLET,S.conf());res["SOL/USDC guard still refuses Token-2022"]=False
except S.Refused:res["SOL/USDC guard still refuses Token-2022"]=True
S.MODE="spot"
res["install: the coin guard has its own service, user, wallet and limits"]=all(x in open(os.path.join(R,"signer","copybot-signer-spot.service")).read() for x in("SIGNER_MODE=spot","User=copybot-signer-spot","copybot-signer-spot")) and \
    json.load(open(os.path.join(R,"signer","limits-spot.json")))["max_swap_usd"]==10 and "cb_install_signer_spot" in open(os.path.join(R,"system","copybot-lib.sh")).read()
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");os._exit(1 if bad else 0)
