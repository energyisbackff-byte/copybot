"""Telegram message formatting: hacker-terminal look (HTML parse_mode), sized for ~32 monospace chars (iPhone)."""
import html,re
W=32
SEP="─"*W
PAPER="PAPER / NO real trades"
def esc(v): return html.escape(str(v),quote=False)
def short(m,n=4): m=str(m or "");return m if len(m)<=2*n+1 else m[:n]+"…"+m[-n:]
def kv(k,v,w=8):
    import textwrap
    return "\n".join(textwrap.wrap(f"{k:<{w}}: {v}",W,subsequent_indent=" "*(w+2),break_long_words=True)) or f"{k:<{w}}:"
def usd(v,sign=False):
    try: return (("+" if v>=0 else "-")+f"${abs(v):,.2f}") if sign else f"${v:,.2f}"
    except Exception: return "–"
def term(cmd,rows,tag=None,extra=None):
    """cmd -> 'root@zetrax:~$ cmd' header; rows: plain-text lines (escaped here) in a <pre> block;
    tag: (level,text) -> '[OK] text'; extra: already-safe HTML appended after the block."""
    lines=[]
    if tag: lines+=[f"[{tag[0]}] {tag[1]}",SEP]
    lines+=[str(r) for r in rows if r is not None]
    body="\n".join(lines)
    if len(body)>3400: body=body[:3400]+"\n… (truncated)"
    out=f"<b>root@zetrax:~$</b> <code>{esc(cmd)}</code>\n<pre>{esc(body)}</pre>"
    if extra: out+="\n"+extra
    return out
def plain(h):
    """HTML -> plain text for logs/CLI."""
    return html.unescape(re.sub(r"<[^>]+>","",h))
COMMANDS=[("status","equity, positions, traders"),("books","every book's money and state"),
    ("signals","last gate decisions and why"),("book","book NAME on|off (pause buys)"),("dashboard","open web dashboard"),
    ("version","print running commit"),("update","git pull && restart"),
    ("keys","ls api keys (masked)"),("setkey","setkey NAME VALUE (msg wiped)"),
    ("health","uptime, heartbeat, last update"),
    ("rollback","restore last code snapshot"),("help","man zetrax")]
