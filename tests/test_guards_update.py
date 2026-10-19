"""The wallet guards can't change through /update. Static checks on the update scripts, then a simulated update run in
a temp folder (fake systemctl/journalctl, no network, no real system files): /update installs the bot's code, leaves a
newer guard version waiting for the console, rolls back when the bot is unhealthy and aborts on broken code.
Run: python3 tests/test_guards_update.py"""
import sys,os,re,json,shutil,subprocess,tempfile,time
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);os.chdir(R)
res={}
rd=lambda p:open(os.path.join(R,p)).read()
AU=rd("system/copybot-app-update.sh");SU=rd("system/copybot-selfupdate");LIB=rd("system/copybot-lib.sh");CLI=rd("system/copybot-cli")
UP=rd("update.sh");INS=rd("install.sh")
def fn(src,name):
    m=re.search(r"^"+name+r"\(\)\{.*?(?=^cb_\w+\(\)\{|\Z)",src,re.S|re.M);return m.group(0) if m else ""
# ---- static: who may install what ---------------------------------------------------------------------------------
job=SU.split("<<'JOB'")[1].split("\nJOB\n")[0]
res["selfupdate: hands the download to the frozen app-update.sh"]="app-update.sh" in job and 'bash "$AU" "$S" "$SHA"' in job
res["selfupdate: never runs a downloaded script (no update.sh, no bash $S/...)"]=not re.search(r"(?<!app-)update\.sh",job) and not re.search(r'bash "\$S/|\. "\$S/|source "\$S',job)
res["app-update: never sources or runs anything from the download as root"]=not re.search(r'(^|[;&|]\s*)(\.|source|bash|sh)\s+"?\$(SRC|NEW|PF)',AU,re.M)
res["app-update: never installs guards or system pieces"]=not re.search(r"cb_install_(guards|signer|signer_spot|perpsguard|system)",AU)
blk=AU.split("AS env APP=")[1].split("\n'\n")[0] if "AS env APP=" in AU else ""
outside=AU.replace(blk,"")
res["app-update: root never writes into /opt/copybot (copies run as the bot user)"]=bool(blk) and not re.search(r'(>|cp |install |mv |sed -i)[^\n]*\$APP',outside.split("echo \"== installing")[1].split("cb_perms")[0] if "echo \"== installing" in outside else "x>$APP")
res["app-update: python checks run as the bot user"]=all(re.search(r"AS [^\n]*"+re.escape(x),AU) for x in("pip install","py_compile","migrate_config.py","timeout 60"))
res["app-update: leaves newer guard/system code waiting (cb_stage_pending)"]='cb_stage_pending "$SRC" "$VERSION"' in AU
res["update.sh (one-time switch): installs no guard, hands over to app-update.sh"]=not re.search(r"cb_install_(guards|signer|signer_spot|perpsguard)",UP) and 'exec bash "$LIBDIR/app-update.sh"' in UP
res["install.sh: system pieces + guards from the console copy, no old CLI heredoc"]='cb_install_system "$SRC"' in INS and "<<CMD" not in INS and 'cb_install_signer "$SRC"' in INS
res["lib: units are never copied from the bot's folder"]="cp " not in fn(LIB,"cb_units") and "$APP" not in fn(LIB,"cb_units")
gm=fn(LIB,"cb_guard_map")
res["lib: the guard list covers signer, solwire, perps guard and their services"]=all(x in gm for x in("signer/signer.py","signer/solwire.py","perpsguard/perpsguard.py","copybot-signer.service","copybot-signer-spot.service","copybot-perpsguard.service"))
sm=fn(LIB,"cb_system_map")
res["lib: the system list covers updater, helpers, sudoers and the console command"]=all(x in sm for x in("copybot-app-update.sh","copybot-selfupdate","copybot-lib.sh","copybot-update.sudoers","system/copybot-cli","systemd/copybot.service"))
paths=[l.split("|")[0] for l in (gm+sm).splitlines() if "|" in l]
res["lib: every listed file exists in the repo"]=len(paths)>=20 and all(os.path.exists(os.path.join(R,p)) for p in paths)
res["lib: rollback/snapshot/clear run as the bot user, venv python never run as root"]=all("cb_as0" in fn(LIB,f) for f in("cb_snapshot","cb_restore","cb_clear_pending")) and ".venv/bin/python" not in LIB
res["lib: .env/token permissions refuse links (O_NOFOLLOW)"]="O_NOFOLLOW" in fn(LIB,"cb_perms")
res["console: pending, guard-update, system-update, each asks for the version code"]=all(x in CLI for x in(" pending)"," guard-update)"," system-update)")) and CLI.count("confirm_version \"")==2
res["console: guard-update installs from the root-only pending copy"]='cb_install_guards "$PEND/src"' in CLI and 'install -d -o root -g root -m 0700 "$PEND"' in LIB
sh=[p for p in("install.sh","update.sh","system/copybot-lib.sh","system/copybot-app-update.sh","system/copybot-selfupdate","system/copybot-cli","system/copybot-rollback.sh","system/copybot-watchdog.sh","system/copybot-rollback","system/copybot-setkey")]
res["bash -n: every shell script parses"]=all(subprocess.run(["bash","-n",p]).returncode==0 for p in sh)
# ---- main.py shows a waiting guard update ---------------------------------------------------------------------------
T=tempfile.mkdtemp();os.environ.update(HELIUS_API_KEY="x",COPYBOT_MEMORY=os.path.join(T,"memory"),TELEGRAM_BOT_TOKEN="",TELEGRAM_ALERTS_CHAT_ID="",COPYBOT_DB=os.path.join(T,"bot.db"))
import main
main.STATE=T
res["boot/health: nothing waiting -> no line"]=main.pending_rows()==[]
json.dump({"version":"a"*40,"ts":time.time(),"guards":["signer/signer.py"],"system":[]},open(os.path.join(T,"pending.json"),"w"))
pr=main.pending_rows();ht=main.health_text(plain=True)
res["boot/health: waiting guard update says 'copybot guard-update'"]=any("guard-update" in x for x in pr) and "guard-update" in ht and not any("system-update" in x for x in pr)
# ---- simulated update runs -------------------------------------------------------------------------------------
can=os.geteuid()==0 and all(shutil.which(x) for x in("flock","tar","runuser"))
if can:
    W=tempfile.mkdtemp();APP=os.path.join(W,"app");ST=os.path.join(W,"state");BK=os.path.join(W,"bk");LD=os.path.join(W,"lib");FB=os.path.join(W,"bin")
    for d in(ST,BK,LD,FB):os.makedirs(d)
    ign=shutil.ignore_patterns("__pycache__","*.db*","bot.log","memory",".git","deploy")
    shutil.copytree(R,APP,ignore=ign);os.makedirs(os.path.join(APP,".venv","bin"))
    os.symlink(sys.executable,os.path.join(APP,".venv","bin","python"))
    open(os.path.join(APP,".venv","bin","pip"),"w").write("#!/bin/sh\nexit 0\n");os.chmod(os.path.join(APP,".venv","bin","pip"),0o755)
    open(os.path.join(APP,"VERSION"),"w").write("0"*40+"\n");os.chmod(W,0o755)
    if subprocess.run(["id","copybot"],capture_output=True).returncode==0:subprocess.run(["chown","-R","copybot:copybot",APP])   # real bot user: it owns the app
    shutil.copy("system/copybot-lib.sh",os.path.join(LD,"lib.sh"));shutil.copy("system/copybot-app-update.sh",os.path.join(LD,"app-update.sh"))
    open(os.path.join(FB,"systemctl"),"w").write('#!/bin/bash\ncase "$1" in restart) [ "${FAKE_HB:-1}" = 1 ] && touch "$CB_APP/heartbeat";; show) echo;; esac\nexit 0\n')
    open(os.path.join(FB,"journalctl"),"w").write("#!/bin/sh\necho log line\n")
    for f in("systemctl","journalctl"):os.chmod(os.path.join(FB,f),0o755)
    ENV=dict(os.environ,CB_APP=APP,CB_STATE=ST,CB_BACKUPS=BK,CB_LIBDIR=LD,CB_TG_API="http://127.0.0.1:9",CB_SETTLE="0",CB_HEALTH_TIMEOUT="4",PATH=FB+":"+os.environ["PATH"])
    def src(tag,signer_change=False,broken=False):
        s=os.path.join(W,"src-"+tag);shutil.copytree(R,s,ignore=ign)
        open(os.path.join(s,"main.py"),"a").write("\n# build "+tag+"\n")
        if signer_change:open(os.path.join(s,"signer","signer.py"),"a").write("\n# guard change "+tag+"\n")
        if broken:open(os.path.join(s,"ops.py"),"a").write("\ndef broken(:\n")
        return s
    def run(s,ver,**e):
        p=subprocess.run(["bash",os.path.join(LD,"app-update.sh"),s,ver],env=dict(ENV,**e),capture_output=True,text=True,timeout=300)
        return p.stdout+p.stderr
    G="/usr/local/lib/copybot-signer/signer.py";g0=open(G).read() if os.path.exists(G) else None
    s1=src("one",signer_change=True);o=run(s1,"1"*40)
    st=json.load(open(os.path.join(ST,"update_status.json")))
    res["sim: healthy update installs the bot's code"]="RESULT: ok" in o and "# build one" in open(os.path.join(APP,"main.py")).read() and st["result"]=="ok"
    res["sim: VERSION written, config.default.yaml refreshed"]=open(os.path.join(APP,"VERSION")).read().strip()=="1"*40 and os.path.exists(os.path.join(APP,"config.default.yaml"))
    pj=json.load(open(os.path.join(ST,"pending.json")))
    res["sim: changed guard code waits for the console (pending.json lists signer.py)"]="signer/signer.py" in pj["guards"] and pj["version"]=="1"*40
    pd=os.path.join(ST,"pending");md=os.stat(pd).st_mode&0o777
    res["sim: pending copy is root-only (0700) and holds the guard source"]=md==0o700 and "guard change one" in open(os.path.join(pd,"src","signer","signer.py")).read()
    res["sim: the installed guard was not touched"]=(open(G).read() if os.path.exists(G) else None)==g0
    res["sim: the bot's reference copy of signer.py updated (not installed anywhere)"]="guard change one" in open(os.path.join(APP,"signer","signer.py")).read()
    s2=src("two");o=run(s2,"2"*40,FAKE_HB="0")
    res["sim: unhealthy update rolls back to the previous code"]="RESULT: rolled_back" in o and "# build one" in open(os.path.join(APP,"main.py")).read() and "# build two" not in open(os.path.join(APP,"main.py")).read()
    res["sim: rollback restores the VERSION"]=open(os.path.join(APP,"VERSION")).read().strip()=="1"*40 and json.load(open(os.path.join(ST,"update_status.json")))["result"]=="rolled_back"
    s3=src("three",broken=True);o=run(s3,"3"*40)
    res["sim: broken code aborts in pre-flight, nothing changed"]="RESULT: preflight_failed" in o and "# build three" not in open(os.path.join(APP,"main.py")).read()
    s4=src("four");o=run(s4,"bad version;rm -rf /")
    res["sim: a strange version string is refused"]="bad version" in o and "# build four" not in open(os.path.join(APP,"main.py")).read()
    # update.sh (what the old self-updater runs once): system pieces skipped in test mode, then the frozen installer
    s5=src("five");p=subprocess.run(["bash",os.path.join(s5,"update.sh")],env=dict(ENV,CB_TEST="1",COPYBOT_VERSION="5"*40),capture_output=True,text=True,timeout=300)
    res["sim: update.sh hands over to app-update.sh (one-time switch)"]="RESULT: ok" in p.stdout and "# build five" in open(os.path.join(APP,"main.py")).read()
    shutil.rmtree(W,ignore_errors=True)
else:print("(simulated runs skipped: need root, flock, tar, runuser)")
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
