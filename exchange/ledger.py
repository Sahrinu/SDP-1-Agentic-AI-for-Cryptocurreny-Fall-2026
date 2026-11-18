"""Persistent live settlement for the Agent Exchange.

Each live forecast is recorded with its reference price and every active agent's
p_up. When its target time (+24h) has passed, the forecast is settled with the
BTC price at the target time: the bets are paid out with the same rules as
exchange/engine.py, and the new coins are saved. The next live forecast starts
from those saved coins, so agents that were right more often get more say.

Rules:
- forecasts are settled oldest first, never out of order
- a forecast is settled only once (settling again returns the saved result)
- the target price must be a completed one-minute close at or before the target
  time and at most 2 minutes before it; ties count as down
- an agent seen for the first time starts with the normal starting coins
- abstaining agents are not part of the forecast and do not bet
"""
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3

from exchange.engine import Exchange

MAX_TARGET_GAP = timedelta(minutes=2)


def _aware(value):
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if stamp.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return stamp.astimezone(timezone.utc)


def _price(value):
    if isinstance(value, bool):
        raise ValueError("Invalid price")
    price = float(value)
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid price")
    return price


class LiveLedger:

    def __init__(self, path, start_coins=100.0):
        self.path = str(path)
        self.start_coins = float(start_coins)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS coins (
                agent TEXT PRIMARY KEY, coins REAL NOT NULL, updated_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS forecasts (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, target_time TEXT NOT NULL,
                reference_price REAL NOT NULL, forecasts TEXT NOT NULL,
                exchange_p REAL, settled INTEGER NOT NULL DEFAULT 0,
                target_price REAL, target_bar_close_time TEXT, went_up INTEGER,
                settled_at TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS settlements (
                forecast_id TEXT NOT NULL, agent TEXT NOT NULL, p_up REAL NOT NULL,
                coins_before REAL NOT NULL, change REAL NOT NULL, coins_after REAL NOT NULL,
                PRIMARY KEY (forecast_id, agent))""")

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    # ---------- coins ----------

    def coins(self):
        with self._db() as db:
            return {a: c for a, c in db.execute("SELECT agent, coins FROM coins ORDER BY agent")}

    def exchange_for(self, agents):
        """An Exchange for these agents, starting from their saved coins."""
        ex = Exchange(list(agents), start_coins=self.start_coins)
        saved = self.coins()
        for agent in ex.coins:
            if agent in saved:
                ex.coins[agent] = saved[agent]
        return ex

    # ---------- recording ----------

    def record(self, forecast):
        """Save a live forecast so it can be settled after its target time."""
        if forecast.get("mode") != "live":
            raise ValueError("Only live forecasts are recorded")
        forecast_id = str(forecast["id"])
        created = _aware(forecast["created_at"])
        target = _aware(forecast["contract"]["target_time"])
        if target - created != timedelta(hours=24):
            raise ValueError("Live forecasts settle exactly 24 hours after the decision")
        reference = _price(forecast["quote"]["price"])
        active = {}
        for agent in forecast.get("agents", []):
            p = agent.get("p_up")
            if p is None or not agent.get("audit", {}).get("passed"):
                continue
            p = float(p)
            if not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError("Invalid agent probability")
            active[str(agent["agent"])] = p
        exchange_p = (forecast.get("methods") or {}).get("exchange")
        with self._db() as db:
            db.execute("""INSERT OR IGNORE INTO forecasts
                (id, created_at, target_time, reference_price, forecasts, exchange_p)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (forecast_id, created.isoformat(), target.isoformat(), reference,
                 json.dumps(active, sort_keys=True), exchange_p))
        return active

    # ---------- settlement ----------

    def pending(self):
        with self._db() as db:
            rows = db.execute("""SELECT id, created_at, target_time FROM forecasts
                WHERE settled = 0 ORDER BY created_at, id""").fetchall()
        return [{"id": r[0], "created_at": r[1], "target_time": r[2]} for r in rows]

    def due(self, now=None):
        instant = _aware(now or datetime.now(timezone.utc))
        return [f for f in self.pending() if _aware(f["target_time"]) <= instant]

    def settle(self, forecast_id, target_quote, *, now=None):
        """Settle one forecast with the BTC price at its target time."""
        instant = _aware(now or datetime.now(timezone.utc))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT created_at, target_time, reference_price, forecasts, settled
                FROM forecasts WHERE id = ?""", (forecast_id,)).fetchone()
            if row is None:
                raise KeyError("Unknown forecast " + str(forecast_id))
            created, target_text, reference, saved, settled = row
            if settled:
                return self.result(forecast_id, db=db)
            target = _aware(target_text)
            if target > instant:
                raise ValueError("Forecast is not due until " + target_text)
            earlier = db.execute("""SELECT COUNT(*) FROM forecasts WHERE settled = 0
                AND (created_at < ? OR (created_at = ? AND id < ?))""",
                (created, created, forecast_id)).fetchone()[0]
            if earlier:
                raise ValueError("Settle older forecasts first")
            price = _price(target_quote["price"])
            bar_close = _aware(target_quote["bar_close_time"])
            if not target - MAX_TARGET_GAP <= bar_close <= target:
                raise ValueError("Target price must be a completed close within 2 minutes before the target time")

            went_up = price > reference          # ties count as down
            forecasts = json.loads(saved)
            stored = {a: c for a, c in db.execute("SELECT agent, coins FROM coins")}
            ex = Exchange(list(forecasts), start_coins=self.start_coins)
            for agent in ex.coins:
                if agent in stored:
                    ex.coins[agent] = stored[agent]
            before = dict(ex.coins)
            changes = ex.settle(forecasts, went_up)

            for agent, p in forecasts.items():
                db.execute("""INSERT INTO coins (agent, coins, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(agent) DO UPDATE SET coins = excluded.coins,
                    updated_at = excluded.updated_at""",
                    (agent, ex.coins[agent], instant.isoformat()))
                db.execute("""INSERT INTO settlements
                    (forecast_id, agent, p_up, coins_before, change, coins_after)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (forecast_id, agent, p, before[agent], changes[agent], ex.coins[agent]))
            db.execute("""UPDATE forecasts SET settled = 1, target_price = ?,
                target_bar_close_time = ?, went_up = ?, settled_at = ? WHERE id = ?""",
                (price, bar_close.isoformat(), int(went_up), instant.isoformat(), forecast_id))
            return self.result(forecast_id, db=db)

    def settle_due(self, get_target_quote, *, now=None):
        """Settle every due forecast, oldest first. Stops at the first price failure.

        get_target_quote(target_time) must return a dict with price and
        bar_close_time for the completed one-minute close at or before target_time.
        """
        settled, problem = [], None
        for item in self.due(now):
            try:
                quote = get_target_quote(_aware(item["target_time"]))
                settled.append(self.settle(item["id"], quote, now=now))
            except Exception as exc:
                problem = {"forecast_id": item["id"], "reason": type(exc).__name__ + ": " + str(exc)[:200]}
                break
        return {"settled": settled, "problem": problem}

    # ---------- reading results ----------

    def result(self, forecast_id, *, db=None):
        own = db is None
        db = db or self._db()
        try:
            row = db.execute("""SELECT id, created_at, target_time, reference_price, exchange_p,
                settled, target_price, target_bar_close_time, went_up, settled_at
                FROM forecasts WHERE id = ?""", (forecast_id,)).fetchone()
            bets = db.execute("""SELECT agent, p_up, coins_before, change, coins_after
                FROM settlements WHERE forecast_id = ? ORDER BY agent""", (forecast_id,)).fetchall()
        finally:
            if own:
                db.close()
        keys = ["id", "created_at", "target_time", "reference_price", "exchange_p", "settled",
                "target_price", "target_bar_close_time", "went_up", "settled_at"]
        out = dict(zip(keys, row))
        out["settled"] = bool(out["settled"])
        out["bets"] = [dict(zip(["agent", "p_up", "coins_before", "change", "coins_after"], b)) for b in bets]
        return out

    def summary(self):
        """Small status block for the dashboard."""
        with self._db() as db:
            settled = db.execute("SELECT COUNT(*) FROM forecasts WHERE settled = 1").fetchone()[0]
            pending = db.execute("SELECT COUNT(*) FROM forecasts WHERE settled = 0").fetchone()[0]
            scored = db.execute("""SELECT exchange_p, went_up FROM forecasts
                WHERE settled = 1 AND exchange_p IS NOT NULL""").fetchall()
            last = db.execute("""SELECT id FROM forecasts WHERE settled = 1
                ORDER BY settled_at DESC LIMIT 1""").fetchone()
        brier = sum((p - y) ** 2 for p, y in scored) / len(scored) if scored else None
        return {"settled": settled, "pending": pending, "coins": self.coins(),
                "start_coins": self.start_coins, "exchange_brier": brier,
                "last_settled": self.result(last[0]) if last else None}
