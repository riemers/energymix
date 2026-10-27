from datetime import datetime, timedelta, timezone

from energymix.config import Config
from energymix.engine import Engine
from energymix.prices import PriceSlot
from energymix.store import Store

UTC = timezone.utc


def engine(tmp_path, until):
    e = Engine(Config(timezone="Europe/Amsterdam"), None, Store(tmp_path / "t.db"))
    start = until - timedelta(hours=24)
    e.prices = [PriceSlot(start + timedelta(hours=i), start + timedelta(hours=i + 1), 0.25) for i in range(24)]
    return e


def test_after_13_retries_every_5_minutes_instead_of_hourly(tmp_path):
    # 27 okt, wintertijd (UTC+1): prijzen tot middernacht, morgen nog niet binnen
    midnight = datetime(2026, 10, 27, 23, 0, tzinfo=UTC)
    e = engine(tmp_path, midnight)
    e.prices_fetched = datetime(2026, 10, 27, 12, 2, tzinfo=UTC).timestamp()  # 13:02 lokaal, nog niets
    assert e._price_retry_seconds(datetime(2026, 10, 27, 12, 5, tzinfo=UTC)) == 120
    assert e._price_retry_seconds(datetime(2026, 10, 27, 12, 10, tzinfo=UTC)) == 0


def test_before_13_hourly_and_none_when_tomorrow_known(tmp_path):
    midnight = datetime(2026, 10, 27, 23, 0, tzinfo=UTC)
    e = engine(tmp_path, midnight)
    e.prices_fetched = datetime(2026, 10, 27, 11, 0, tzinfo=UTC).timestamp()  # 12:00 lokaal
    assert e._price_retry_seconds(datetime(2026, 10, 27, 11, 30, tzinfo=UTC)) == 1800
    # 13:00 lokaal: niet meer wachten tot 14:00
    assert e._price_retry_seconds(datetime(2026, 10, 27, 12, 0, tzinfo=UTC)) == 0
    e = engine(tmp_path, midnight + timedelta(hours=24))
    assert e._price_retry_seconds(datetime(2026, 10, 27, 13, 0, tzinfo=UTC)) is None
