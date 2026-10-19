"""/report: every book's results since the bot started (paper and real money) as one Telegram message, plus the full
list of buys and sells as a CSV file (opens as a table on a phone). Read-only: it never changes anything."""
import csv,io,time,json
from tgfmt import term,kv,usd,SEP
BOOKS=(("copy","copy (paper)"),("consensus","consensus (paper)"),("trend","Trend Rider (paper)"),("narrative","Narrative Rider (paper)"),
       ("perps","perps (paper, charter)"),("perps-grok","perps-grok (paper, 8-19x)"),("trend-live","Trend Rider REAL"))
def _day(t):return time.strftime("%d %b %Y",time.gmtime(t)) if t else "–"
def book_rows(db,cfg,b,label):
    q=lambda s,a=():db.q(s,a)
    buys=q("select count(*) from b_trades where book=? and side='buy'",(b,))[0][0]
    sells=q("select count(*) from b_trades where book=? and side='sell'",(b,))[0][0]
    skips=q("select count(*) from b_signals where book=? and decision='refused'",(b,))[0][0]
    if not buys and not sells and not db.kv_get(b+":cash") and b!="copy":return []
    closed=q("select realized from b_positions where book=? and status='closed'",(b,))
    won=[r for (r,) in closed if (r or 0)>0];lost=[r for (r,) in closed if (r or 0)<=0]
    fees=q("select coalesce(sum(fee_usd),0) from b_fills where book=? and status='filled'",(b,))[0][0]
    op=q("select count(*) from b_positions where book=? and status='open'",(b,))[0][0]
    first=q("select min(ts) from b_trades where book=?",(b,))[0][0]
    start=((cfg.get("books") or {}).get(b) or {}).get("start_equity_usd") or (cfg.get("paper") or {}).get("start_equity_usd")
    if b=="trend-live":start=db.kv_get("trend-live:start")
    now=db.kv_get(b+":last_equity",db.kv_get(b+":cash"))
    rows=[label,kv("  since",_day(first) if first else "no trades yet",9)]
    if start is not None and now is not None:rows.append(kv("  money",f"{usd(start)} -> {usd(now)}",9))
    rows+=[kv("  buys",f"{buys}  sells {sells}  open {op}",9),kv("  won",f"{len(won)} trades {usd(sum(won),True)}",9),
           kv("  lost",f"{len(lost)} trades {usd(sum(lost),True)}",9),kv("  net",usd(sum(won)+sum(lost),True)+f" (fees {usd(fees)})",9),
           kv("  skipped",f"{skips} (safety checks)",9)]
    return rows
def text(db,cfg):
    rows=[]
    for b,label in BOOKS:
        r=book_rows(db,cfg,b,label)
        if r:rows+=r+[SEP]
    try:
        v1=db.q("select count(*),coalesce(sum(case when side='sell' then pnl else 0 end),0),min(ts) from trades where side in('buy','sell')")[0]
        if v1[0]:rows+=["old copy bot (before books)",kv("  since",_day(v1[2]),9),kv("  trades",str(v1[0]),9),kv("  net",usd(v1[1],True),9),SEP]
    except Exception:pass
    bt=db.kv_get("backtest:v2") or {};fb=db.kv_get("backtest:fast") or {}
    rows+=["backtests (old prices):"]
    if bt.get("coins"):rows.append(f"  v2: {'PASSED' if bt.get('passed') else 'failed'} SOL {bt['coins']['SOL']['all']['ret']:+.1%}")
    for c in fb.get("candidates",[]):rows.append(f"  {c['name'].split()[1]}: {'PASSED' if c['passed'] else 'failed'} SOL {c['coins']['SOL']['all']['ret']:+.1%}")
    v1b=db.kv_get("backtest:last") or {}
    if v1b.get("version")=="trend-rider v1":rows.append("  v1: failed")
    pb=db.kv_get("backtest:perps") or {}
    for m in("charter","grok"):
        x=pb.get(m) or {}
        if x.get("checks"):rows.append(f"  perps {m}: {'PASSED' if x.get('passed') else 'failed'} PF {x['pf']:.2f}" if x.get("pf") is not None else f"  perps {m}: failed")
    gd=__import__("risk").green_days(db)
    if gd["days"]:rows+=[SEP,f"green days: {gd['green']} of {gd['days']} ({gd['pct']:.0%})"]
    rows+=[SEP,"every buy and sell is in the","file below (zetrax-trades.csv)"]
    return term("report --all",rows,("OK","📊 Zetrax report"))
def trades_csv(db):
    out=io.StringIO();w=csv.writer(out)
    w.writerow(["time_utc","book","side","coin","usd","tokens","price","profit_usd","trader","note"])
    rows=[]
    for ts,b,tr,m,side,u,tok,px,pnl,note in db.q("select ts,book,trader,mint,side,usd,tokens,price,pnl,note from b_trades where side in('buy','sell')"):
        rows.append((ts,b,side,m,u,tok,px,pnl if side=="sell" else "",tr,note))
    try:
        for ts,tr,m,side,u,tok,px,pnl,note in db.q("select ts,trader,mint,side,usd,tokens,price,pnl,note from trades where side in('buy','sell')"):
            rows.append((ts,"copy-old",side,m,u,tok,px,pnl if side=="sell" else "",tr,note))
    except Exception:pass
    for ts,b,side,m,u,tok,px,pnl,tr,note in sorted(rows,key=lambda r:r[0] or 0):
        w.writerow([time.strftime("%Y-%m-%d %H:%M",time.gmtime(ts or 0)),b,side,"SOL" if m=="So11111111111111111111111111111111111111112" else m,
                    f"{u:.2f}" if u is not None else "",f"{tok:.6g}" if tok is not None else "",f"{px:.6g}" if px is not None else "",
                    f"{pnl:.2f}" if pnl not in("",None) else "",tr,note or ""])
    return out.getvalue().encode(),len(rows)
