"""Live execution STUB. Deliberately disabled. No private keys are ever loaded here."""
import os
class LiveTradingDisabled(RuntimeError): pass
def execute_swap(cfg, *a, **k):
    here=os.path.dirname(os.path.abspath(__file__))
    if not (cfg.get("live_trading") is True and os.path.exists(os.path.join(here,"LIVE_CONFIRM"))):
        raise LiveTradingDisabled("Live trading is disabled (config live_trading=false and/or no LIVE_CONFIRM file).")
    raise LiveTradingDisabled("Live execution is not implemented in this build. Paper mode only.")
