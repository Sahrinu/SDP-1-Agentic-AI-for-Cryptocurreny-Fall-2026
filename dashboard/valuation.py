"""One read-only market quote per minute; valuation never authorizes an order."""
from datetime import datetime, timezone
import threading


def _quote():
    from market_data.live import reference_quote
    return reference_quote()


class PortfolioValuation:
    def __init__(self, store, *, get_quote=_quote, interval=60, clock=None):
        self.store = store
        self.get_quote = get_quote
        self.interval = interval
        self.clock = clock or (lambda:datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last_attempt = None
        self._error = None

    def refresh(self):
        with self._fetch_lock:
            try:
                quote = self.get_quote()
                if not self._stop.is_set():
                    self.record(quote)
            except Exception as exc:
                with self._lock:
                    self._last_attempt = self.clock().isoformat()
                    self._error = type(exc).__name__

    def record(self, quote):
        """Also called after a paper decision, using its existing quote without a new API call."""
        with self._lock:
            try:
                self.store.value(quote, now=self.clock(), record=True)
                self._error = None
            except Exception as exc:
                # A reporting failure must never change or obscure an already committed trade.
                self._error = type(exc).__name__
            self._last_attempt = self.clock().isoformat()

    def snapshot(self):
        with self._lock:
            value = self.store.value(now=self.clock())
            age = value['price_age_seconds']
            status = ('unavailable' if self._error else 'waiting') if age is None else (
                'stale' if self._error or age > 180 else 'live')
            return dict(value, status=status, last_attempt=self._last_attempt,
                        refresh_seconds=self.interval, error=self._error,
                        price_basis='Binance completed one-minute BTC/USDT close; paper USD assumes 1 USDT = 1 USD')

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='paper-valuation', daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
