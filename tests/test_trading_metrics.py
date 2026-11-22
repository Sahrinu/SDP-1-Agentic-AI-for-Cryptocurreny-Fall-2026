import math

import pytest

from experiments.trading_metrics import trading_metrics


def test_metrics_match_hand_calculation():
    # Start 100 -> 110 -> 99 -> 121: returns +10%, -10%, +22.22%
    trades = [{"quantity_btc": "0.5", "execution_price_usd": "100", "fee_usd": "0.05"},
              {"quantity_btc": "0.5", "execution_price_usd": "120", "fee_usd": "0.06"}]
    m = trading_metrics([110, 99, 121], trades, 100)
    r = [0.10, -0.10, 121 / 99 - 1]
    mean = sum(r) / 3
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / 2)
    assert m["sharpe_annualised"] == pytest.approx(math.sqrt(365) * mean / sd)
    assert m["cumulative_return_pct"] == pytest.approx(21.0)
    assert m["max_drawdown_pct"] == pytest.approx(10.0)          # 110 -> 99
    assert m["traded_value_usd"] == pytest.approx(110.0)         # 50 + 60
    assert m["turnover"] == pytest.approx(110.0 / ((110 + 99 + 121) / 3))
    assert m["fees_paid_usd"] == pytest.approx(0.11)
    assert m["trades"] == 2 and m["days"] == 3


def test_drawdown_counts_a_loss_on_the_first_day_against_starting_cash():
    m = trading_metrics([90, 95], [], 100)
    assert m["max_drawdown_pct"] == pytest.approx(10.0)
    assert m["turnover"] == 0 and m["fees_paid_usd"] == 0


def test_sharpe_is_undefined_for_flat_or_single_day_curves():
    assert trading_metrics([100, 100, 100], [], 100)["sharpe_annualised"] is None
    assert trading_metrics([101], [], 100)["sharpe_annualised"] is None


def test_accepts_equity_rows_and_trade_objects():
    from execution.paper_trading import Trade
    trade = Trade("2026-10-02T00:00:00+00:00", "BUY", "0.01", "100000", "100050", "1.0005",
                  "8998.4995", "0.01", "0")
    m = trading_metrics([{"equity_usd": "9998.9995"}, {"equity_usd": "10100"}], [trade], "10000")
    assert m["traded_value_usd"] == pytest.approx(1000.5)
    assert m["days"] == 2


@pytest.mark.parametrize("equity,cash", [([], 100), ([0], 100), ([100], 0), ([float("nan")], 100)])
def test_rejects_invalid_inputs(equity, cash):
    with pytest.raises(ValueError):
        trading_metrics(equity, [], cash)
