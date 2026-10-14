#!/usr/bin/env python3
"""Set @ZetSolBot's chat menu button to the current dashboard URL (Telegram Mini App)."""
import os,json,sys,urllib.request,urllib.parse
H=os.path.dirname(os.path.abspath(__file__));base=open(os.path.join(H,"url.txt")).read().strip()
tok=os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
if not tok: sys.exit("no TELEGRAM_BOT_TOKEN")
url=f"{base}/?t={open(os.path.join(H,'token.txt')).read().strip()}"
chat=os.environ.get("TELEGRAM_CHAT_ID","8815474393")
btn=json.dumps({"type":"web_app","text":"Dashboard","web_app":{"url":url}})
r=json.load(urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/setChatMenuButton?"+urllib.parse.urlencode({"chat_id":chat,"menu_button":btn})))
print("setChatMenuButton:",r.get("ok"),r.get("description",""))
