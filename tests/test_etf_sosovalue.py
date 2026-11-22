import pandas as pd
import pytest

from market_data import etf_sosovalue as sv
from market_data.etf import import_flows

NOW = pd.Timestamp("2026-11-22T09:00:00Z")


def payload(rows):
    return {"code": 0, "message": "success", "data": rows}


def fake(rows):
    calls = []

    def get(url, headers):
        calls.append(headers)
        return payload(rows)
    return get, calls


def test_fetch_parses_rows_and_never_backdates_past_retrieval():
    get, calls = fake([
        {"date": "2026-11-21", "total_net_inflow": None},
        {"date": "2026-11-20", "total_net_inflow": 125.5e6},
        {"date": "2026-11-19", "total_net_inflow": -582193660.7},
    ])
    frame = sv.fetch("k", get=get, now=NOW).set_index("date")
    assert calls == [{"x-soso-api-key": "k"}]
    assert frame.loc["2026-11-19", "available_at"] == "2026-11-20T12:00:00+00:00"   # estimated publication
    assert frame.loc["2026-11-21", "available_at"] == NOW.isoformat()              # capped at retrieval
    assert not frame.loc["2026-11-21", "complete"] and frame.loc["2026-11-20", "complete"]
    assert (pd.to_datetime(frame.available_at) <= NOW).all()


def test_fetch_rejects_errors_and_missing_key():
    with pytest.raises(ValueError):
        sv.fetch(None, get=lambda u, h: payload([]))
    with pytest.raises(ValueError):
        sv.fetch("k", get=lambda u, h: {"code": 40101, "data": None})


def test_merge_keeps_reviewed_rows_and_fills_new_dates(tmp_path):
    farside = pd.DataFrame([
        {"date": "2026-11-19", "net_flow_usd": -582300000.0, "available_at": "2026-11-21T13:33:28+00:00",
         "complete": True, "source": "farside"},
        {"date": "2026-11-20", "net_flow_usd": None, "available_at": "2026-11-21T13:33:28+00:00",
         "complete": False, "source": "farside"},
    ])
    path = tmp_path / "etf_flows.csv"
    farside.to_csv(path, index=False)
    get, _ = fake([
        {"date": "2026-11-21", "total_net_inflow": None},
        {"date": "2026-11-20", "total_net_inflow": 125.5e6},
        {"date": "2026-11-19", "total_net_inflow": -582193660.7},
    ])
    merged, added = sv.update_csv(path, "k", get=get, now=NOW)
    assert added == 2                                            # 20 Nov complete, 21 Nov pending
    reviewed = merged[(merged.date == "2026-11-19")]
    assert len(reviewed) == 1 and reviewed.source.iloc[0] == "farside"   # never overwritten
    # a later complete version replaces the pending SoSoValue row
    get2, _ = fake([{"date": "2026-11-21", "total_net_inflow": 50e6}])
    merged2, _ = sv.update_csv(path, "k", get=get2, now=NOW + pd.Timedelta(days=1))
    rows21 = merged2[merged2.date == "2026-11-21"]
    assert len(rows21) == 1 and str(rows21.complete.iloc[0]).lower() == "true"
    # the merged file still passes the reviewed-data importer
    imported = import_flows(path, tmp_path / "out.csv")
    assert set(imported.date) == {"2026-11-19", "2026-11-20", "2026-11-21"}
