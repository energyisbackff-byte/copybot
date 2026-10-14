#!/usr/bin/env python3
"""Set @ZetSolBot's chat menu button to the current dashboard URL (Telegram Mini App)."""
import os,json,sys,urllib.request,urllib.parse
H=os.path.dirname(os.path.abspath(__file__));base=open(os.path.join(H,"url.txt")).read().strip()
tok=os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
if not tok: sys.exit("no TELEGRAM_BOT_TOKEN")
url=f"{base}/?t={open(os.path.join(H,'token.txt')).read().strip()}"
chat=os.environ.get("TELEGRAM_CHAT_ID","").strip()
if not chat:
    try:
        import sqlite3;r=sqlite3.connect(f"file:{os.path.join(H,'..','bot.db')}?mode=ro",uri=True).execute("select v from kv where k='tg_chat_id'").fetchone()
        chat=json.loads(r[0]) if r else ""
    except Exception: chat=""
if not chat: sys.exit("no chat id yet: send /start to the bot first")
btn=json.dumps({"type":"web_app","text":"Dashboard","web_app":{"url":url}})
r=json.load(urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/setChatMenuButton?"+urllib.parse.urlencode({"chat_id":chat,"menu_button":btn})))
print("setChatMenuButton:",r.get("ok"),r.get("description",""))
