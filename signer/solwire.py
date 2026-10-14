"""Minimal Solana wire format: base58, transaction parsing (legacy and v0), signing, and building the few simple
transactions the signer creates itself (SOL / USDC transfer to the owner's home address). Standard library + cryptography."""
import struct,json,os
B58="123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
def b58e(b):
    n=int.from_bytes(b,"big");s=""
    while n:n,r=divmod(n,58);s=B58[r]+s
    return "1"*(len(b)-len(b.lstrip(b"\0")))+s
def b58d(s):
    n=0
    for ch in s:
        i=B58.find(ch)
        if i<0:raise ValueError("not base58")
        n=n*58+i
    b=n.to_bytes((n.bit_length()+7)//8,"big") if n else b""
    return b"\0"*(len(s)-len(s.lstrip("1")))+b
def pk(s):
    b=b58d(s)
    if len(b)!=32:raise ValueError("not a 32-byte address")
    return b
SYSTEM=pk("11111111111111111111111111111111");TOKEN=pk("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN22=pk("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");ATA=pk("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
COMPUTE=pk("ComputeBudget111111111111111111111111111111");JUPITER=pk("JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4")
USDC=pk("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v");WSOL=pk("So11111111111111111111111111111111111111112")
def cu16_read(b,i):
    v=0;sh=0
    while True:
        x=b[i];i+=1;v|=(x&0x7f)<<sh;sh+=7
        if not x&0x80:return v,i
def cu16(n):
    out=bytearray()
    while True:
        x=n&0x7f;n>>=7
        if n:out.append(x|0x80)
        else:out.append(x);return bytes(out)
class Tx:
    """Parsed transaction. `msg` is the exact signed message bytes; program ids must be static keys (Solana rule)."""
    def __init__(s,raw):
        s.raw=bytes(raw);n,i=cu16_read(s.raw,0);s.nsig=n;s.sig_off=i;i+=64*n;s.msg_off=i;m=s.raw[i:];s.msg=m;j=0
        s.version=None
        if m[0]&0x80:
            s.version=m[0]&0x7f;j=1
            if s.version!=0:raise ValueError(f"unsupported transaction version {s.version}")
        s.n_req,s.n_ro_signed,s.n_ro_unsigned=m[j],m[j+1],m[j+2];j+=3
        nk,j=cu16_read(m,j);s.keys=[m[j+32*k:j+32*k+32] for k in range(nk)];j+=32*nk
        s.blockhash=m[j:j+32];j+=32
        ni,j=cu16_read(m,j);s.ix=[]
        for _ in range(ni):
            p=m[j];j+=1;na,j=cu16_read(m,j);acc=list(m[j:j+na]);j+=na;nd,j=cu16_read(m,j);data=m[j:j+nd];j+=nd
            s.ix.append(dict(program_index=p,accounts=acc,data=bytes(data)))
        s.lookups=[]
        if s.version==0:
            nl,j=cu16_read(m,j)
            for _ in range(nl):
                key=m[j:j+32];j+=32;nw,j=cu16_read(m,j);w=list(m[j:j+nw]);j+=nw;nr,j=cu16_read(m,j);r=list(m[j:j+nr]);j+=nr
                s.lookups.append(dict(key=key,writable=w,readonly=r))
        if j!=len(m):raise ValueError("trailing bytes in message")
    def program(s,ix):
        if ix["program_index"]>=len(s.keys):raise ValueError("program id not in static keys")
        return s.keys[ix["program_index"]]
    def signed(s,signature):
        """Return wire bytes with signature slot 0 filled."""
        return s.raw[:s.sig_off]+signature+s.raw[s.sig_off+64:]
def sign_message(secret64,msg):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.from_private_bytes(bytes(secret64[:32])).sign(msg)
def verify(pub,sig,msg):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:Ed25519PublicKey.from_public_bytes(pub).verify(sig,msg);return True
    except Exception:return False
def new_keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization as S
    k=Ed25519PrivateKey.generate()
    sec=k.private_bytes(S.Encoding.Raw,S.PrivateFormat.Raw,S.NoEncryption());pub=k.public_key().public_bytes(S.Encoding.Raw,S.PublicFormat.Raw)
    return sec+pub          # Solana CLI keypair layout: 32-byte seed + 32-byte public key
def legacy_tx(keys,n_req,n_ro_signed,n_ro_unsigned,blockhash,ixs):
    """keys: ordered [signers(writable, then readonly), writable non-signers, readonly non-signers]; ixs: (prog_idx, [acc idx], data)."""
    m=bytes([n_req,n_ro_signed,n_ro_unsigned])+cu16(len(keys))+b"".join(keys)+blockhash+cu16(len(ixs))
    for p,acc,data in ixs:m+=bytes([p])+cu16(len(acc))+bytes(acc)+cu16(len(data))+data
    return cu16(n_req)+b"\0"*64*n_req+m
def sol_transfer(frm,to,lamports,blockhash):
    return legacy_tx([frm,to,SYSTEM],1,0,1,blockhash,[(2,[0,1],struct.pack("<IQ",2,lamports))])
def usdc_transfer(owner,src,dst,amount,blockhash,decimals=6):
    return legacy_tx([owner,src,dst,USDC,TOKEN],1,0,2,blockhash,[(4,[1,3,2,0],bytes([12])+struct.pack("<QB",amount,decimals))])
_P=2**255-19;_D=(-121665*pow(121666,_P-2,_P))%_P;_I=pow(2,(_P-1)//4,_P)
def on_curve(b):
    """True if 32 bytes are a valid ed25519 point, i.e. an address someone can hold a key for (not a program address)."""
    y=int.from_bytes(b,"little")&((1<<255)-1)
    if len(b)!=32 or y>=_P:return False
    u=(y*y-1)%_P;v=(_D*y*y+1)%_P;x2=u*pow(v,_P-2,_P)%_P
    if x2==0:return True
    x=pow(x2,(_P+3)//8,_P)
    if (x*x-x2)%_P!=0:x=x*_I%_P
    return (x*x-x2)%_P==0
def token_account(data):
    """SPL token account layout -> dict(mint, owner, amount, delegate)."""
    if len(data)<165:return None
    mint,owner=data[0:32],data[32:64];amount=struct.unpack("<Q",data[64:72])[0];dtag=struct.unpack("<I",data[72:76])[0]
    return dict(mint=mint,owner=owner,amount=amount,delegate=data[76:108] if dtag else None,state=data[108])
