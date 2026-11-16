"""Offline Student 5 demonstration. Every market value here is synthetic."""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.world_economy import forecast
from market_data.macro import align_daily
from risk.gateway import execute_guarded
from risk.durable_gateway import RiskGateway, RiskPolicy
from execution.paper_trading import PaperTrader


def main(out=None):
    dates = pd.bdate_range("2026-10-19", "2026-11-13")
    macro = align_daily({
        "dxy": pd.Series([102 - i * 0.1 for i in range(len(dates))], index=dates),
        "treasury_10y": pd.Series([4.3 - i * 0.01 for i in range(len(dates))], index=dates),
    }, "2026-10-19", "2026-11-15")
    result = {"synthetic": True, "forecast": forecast("2026-11-15", {"macro": macro}),
              "weekend": macro.tail(3).reset_index().astype(str).to_dict("records"), "risk_checks": []}
    out = Path(out) if out else Path(__file__).resolve().parents[1] / "outputs" / "student5_demo" / uuid.uuid4().hex
    out.mkdir(parents=True, exist_ok=False)
    trader = PaperTrader(initial_cash="10000")
    policy = RiskPolicy('.30','.10')  # synthetic thresholds only
    state = out / 'risk_state.sqlite3'
    RiskGateway.initialize_state(state, policy=policy, trader=trader, actor='synthetic-demo',
        reason='Initialize a new synthetic scenario', timestamp=datetime(2026,11,15,tzinfo=timezone.utc),
        previous_forecast='.50')
    gateway = RiskGateway(trader, policy, state_path=state)
    result['risk_policy'] = {'max_forecast_swing':'.30','max_cumulative_loss':'.10',
                             'source':'synthetic demo values, not production policy'}
    for day, price, action, probability, fraction in [
            (16, "50000", "BUY", '.60', '.10'),
            (17, "50000", "BUY", '.61', '.90'),
            (18, "80000", "HOLD", '.95', '.10')]:
        approval, trade = execute_guarded(
            trader, action, price, probability_up=probability, requested_buy_fraction=fraction,
            timestamp=datetime(2026, 11, day, tzinfo=timezone.utc),
        )
        result["risk_checks"].append({"requested": action, "synthetic_forecast":probability,
                                      "approval": approval.to_dict(), "outcome":trader.last_risk_outcome,
                                      "traded": trade is not None, "account": trader.summary(price)})
    result['risk_audit'] = gateway.audit_events()
    gateway.close()

    # A flat account can be recreated for this isolated restart scenario.
    # This does not implement restoration of an account with trades.
    restart_state = out / 'restart_risk_state.sqlite3'
    flat = PaperTrader(initial_cash='10000')
    RiskGateway.initialize_state(restart_state, policy=policy, trader=flat,
        actor='synthetic-demo', reason='Initialize isolated restart scenario',
        timestamp=datetime(2026, 11, 15, tzinfo=timezone.utc), previous_forecast='.50')
    first = RiskGateway(flat, policy, state_path=restart_state)
    triggered = first.execute('BUY', probability_up='.80', btc_price_usd='50000',
        buy_fraction='.10', timestamp=datetime(2026, 11, 16, tzinfo=timezone.utc))
    first.close()
    restarted = PaperTrader(initial_cash='10000')
    reopened = RiskGateway(restarted, policy, state_path=restart_state)
    blocked = reopened.execute('BUY', probability_up='.55', btc_price_usd='50000',
        buy_fraction='.10', timestamp=datetime(2026, 11, 17, tzinfo=timezone.utc))
    reopened.reset(actor='synthetic-operator', reason='Reviewed synthetic halt',
        timestamp=datetime(2026, 11, 18, tzinfo=timezone.utc), previous_forecast='.55')
    after_reset = reopened.execute('BUY', probability_up='.56', btc_price_usd='50000',
        buy_fraction='.10', timestamp=datetime(2026, 11, 19, tzinfo=timezone.utc))
    result['restart_and_reset'] = {
        'note': 'Flat synthetic account recreated explicitly; not account recovery',
        'trigger': triggered, 'after_restart': blocked, 'after_reset': after_reset,
        'audit': reopened.audit_events(),
    }
    reopened.close()
    with (out / 'student5_demo.json').open('x',encoding='utf-8') as output:
        json.dump(result, output, indent=2)
    print(json.dumps(result, indent=2))
    print(f"Saved {out}")
    return result


if __name__ == "__main__":
    main()
