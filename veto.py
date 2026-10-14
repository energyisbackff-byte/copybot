"""Token safety veto for memecoin buys (paper books). Pure checks on public on-chain data via Helius RPC.
A coin is refused if: more coins can be minted, holders can be frozen, it uses Token-2022 transfer fees/hooks,
or its 10 biggest real holders (pools, program-owned accounts and burn addresses excluded) own too much."""
import time,logging
log=logging.getLogger("bot")
TOKEN="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";TOKEN22="TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
BURN={"1nc1nerator11111111111111111111111111111111","11111111111111111111111111111111"}
ALLOW={"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v","Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe5BenwNYB","So11111111111111111111111111111111111111112"}
B58="123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
def b58decode(s):
    n=0
    for ch in s: n=n*58+B58.index(ch)
    b=n.to_bytes((n.bit_length()+7)//8,"big") if n else b""
    return b"\0"*(len(s)-len(s.lstrip("1")))+b
# ed25519: an address "on the curve" can have a private key (a person's wallet); "off the curve" is a program-derived
# address (pool vaults, bonding curves, program authorities) that no one holds a key for.
_P=2**255-19;_D=(-121665*pow(121666,_P-2,_P))%_P;_I=pow(2,(_P-1)//4,_P)
def on_curve(addr):
    try:b=b58decode(addr) if isinstance(addr,str) else bytes(addr)
    except ValueError:return False
    if len(b)!=32:return False
    y=int.from_bytes(b,"little")&((1<<255)-1)
    if y>=_P:return False
    u=(y*y-1)%_P;v=(_D*y*y+1)%_P;x2=u*pow(v,_P-2,_P)%_P
    if x2==0:return True
    x=pow(x2,(_P+3)//8,_P)
    if (x*x-x2)%_P!=0:x=x*_I%_P
    return (x*x-x2)%_P==0
_cache={}
def check(hel,mint,max_top10=0.30,ttl=600):
    """-> (ok, reason, info). Network errors fail closed (refuse) so a broken feed can't wave a coin through."""
    if mint in ALLOW:return True,"allow-listed",{}
    hit=_cache.get(mint)
    if hit and time.time()-hit[0]<ttl:return hit[1]
    res=_check(hel,mint,max_top10);_cache[mint]=(time.time(),res);return res
def _check(hel,mint,max_top10):
    info={}
    try:
        acc=hel.rpc("getAccountInfo",[mint,{"encoding":"jsonParsed"}])["value"]
        if not acc:return False,"safety veto: mint not found",info
        prog=acc.get("owner");p=(acc.get("data") or {}).get("parsed",{}).get("info",{})
        info.update(program="token-2022" if prog==TOKEN22 else "token" if prog==TOKEN else prog,mint_auth=p.get("mintAuthority"),freeze_auth=p.get("freezeAuthority"))
        if prog not in(TOKEN,TOKEN22):return False,"safety veto: not an SPL token",info
        if p.get("mintAuthority"):return False,"safety veto: creator can still mint",info
        if p.get("freezeAuthority"):return False,"safety veto: holders can be frozen",info
        if prog==TOKEN22:
            for ext in p.get("extensions") or []:
                e=ext.get("extension","");st=ext.get("state") or {}
                if e=="transferHook" and st.get("programId"):return False,"safety veto: transfer hook",info
                if e=="transferFeeConfig":
                    fee=max(int((st.get("newerTransferFee") or {}).get("transferFeeBasisPoints",0)),int((st.get("olderTransferFee") or {}).get("transferFeeBasisPoints",0)))
                    if fee>0:return False,f"safety veto: {fee/100:g}% transfer fee",info
                if e in("permanentDelegate","nonTransferable","pausableConfig"):return False,f"safety veto: {e}",info
        supply=float(p.get("supply") or 0)
        if supply<=0:return False,"safety veto: zero supply",info
        big=hel.rpc("getTokenLargestAccounts",[mint])["value"][:20]
        accs=hel.rpc("getMultipleAccounts",[[b["address"] for b in big],{"encoding":"jsonParsed"}])["value"]
        reals=[]   # balances of real holders only: pools, bonding curves, program accounts and burns are skipped
        for b,a in zip(big,accs):
            owner=(((a or {}).get("data") or {}).get("parsed") or {}).get("info",{}).get("owner")
            if owner and owner not in BURN and on_curve(owner):reals.append(float(b.get("amount") or 0))
        share=sum(sorted(reals,reverse=True)[:10])/supply;info["top10"]=round(share,3)
        if share>max_top10:return False,f"safety veto: top 10 holders own {share:.0%}",info
        return True,"passed",info
    except Exception as e:
        log.warning("veto %s: %s",mint[:8],e);return False,f"safety veto: check failed ({type(e).__name__})",info
