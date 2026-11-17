import asyncio
from datetime import datetime, timedelta, timezone

from energymix import energyzero
from energymix.config import Config
from energymix.engine import Engine
from energymix.prices import PriceSlot
from energymix.store import Store

UTC = timezone.utc
MARKUP = 0.1357  # gemeten op 17 nov: Tibber-totaal - EnergyZero incl. btw


def tibber_today(day_start, spot):
    """Kwartierprijzen van Tibber: beurs + opslag, met wat ruis per kwartier."""
    out = []
    for i in range(96):
        start = day_start + timedelta(minutes=15 * i)
        hour = start.replace(minute=0)
        out.append(PriceSlot(start, start + timedelta(minutes=15), spot[hour] + MARKUP + (0.003 if i % 2 else -0.003)))
    return out


def ez_response(day_start, hours):
    spot = {day_start + timedelta(hours=h): 0.10 + 0.005 * (h % 24) for h in range(hours)}
    prices = [{"price": p, "timestamp": f"{h:%Y-%m-%d %H:%M:%S}+00:00",
               "start": f"{h:%Y-%m-%d %H:%M:%S}+00:00", "end": f"{h + timedelta(hours=1):%Y-%m-%d %H:%M:%S}+00:00"}
              for h, p in spot.items()]
    return spot, {"context": {}, "response": {"prices": prices}}


DAY = datetime(2026, 11, 16, 23, 0, tzinfo=UTC)  # 17 nov 00:00 lokaal


def test_markup_learned_from_today_and_applied_to_tomorrow():
    spot, resp = ez_response(DAY, 48)
    tibber = tibber_today(DAY, spot)
    parsed = energyzero.parse_response(resp)
    assert len(parsed) == 48
    assert abs(energyzero.tibber_markup(tibber, parsed) - MARKUP) < 1e-6
    extra = energyzero.provisional_slots(tibber, parsed)
    assert len(extra) == 24
    assert extra[0].start == DAY + timedelta(hours=24)
    assert extra[0].level == energyzero.PROVISIONAL
    assert abs(extra[0].price - (spot[DAY + timedelta(hours=24)] + MARKUP)) < 1e-4


def test_no_provisional_when_markup_unstable_or_too_little_overlap():
    spot, resp = ez_response(DAY, 48)
    parsed = energyzero.parse_response(resp)
    assert energyzero.provisional_slots(tibber_today(DAY, spot)[:16], parsed) == []  # 4 uur overlap
    wild = [PriceSlot(s.start, s.end, s.price + (0.1 if s.start.hour == 10 else 0)) for s in tibber_today(DAY, spot)]
    assert energyzero.provisional_slots(wild, parsed) == []


class FakeHA:
    def __init__(self, resp):
        self.resp = resp
        self.connected = asyncio.Event()
        self.connected.set()
        self.calls = []

    async def command(self, payload):
        self.calls.append(payload)
        if payload["type"] == "config_entries/get":
            return [{"entry_id": "ez1", "state": "loaded"}]
        return self.resp


def test_engine_uses_energyzero_until_tibber_has_tomorrow(tmp_path, monkeypatch):
    spot, resp = ez_response(DAY, 48)
    today = tibber_today(DAY, spot)
    e = Engine(Config(timezone="Europe/Amsterdam", tibber_token="x"), None, Store(tmp_path / "t.db"))
    e.ha = FakeHA(resp)
    answers = [today]

    async def fake_fetch(*_a):
        return answers[-1]

    import energymix.engine as eng
    monkeypatch.setattr(eng, "fetch_prices", fake_fetch)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            t = datetime(2026, 11, 17, 12, 10, tzinfo=UTC)  # 13:10 lokaal
            return t.astimezone(tz) if tz else t.replace(tzinfo=None)

    monkeypatch.setattr(eng, "datetime", Clock)
    monkeypatch.setattr(eng.time, "time", lambda: Clock.now(UTC).timestamp())

    asyncio.run(e.refresh_prices(force=True))
    assert len(e.prices) == 96 + 24
    assert e.prices[-1].level == energyzero.PROVISIONAL
    assert e.provisional_since is not None
    # nog steeds op Tibber wachten: elke minuut opnieuw
    assert e._price_retry_seconds(Clock.now(UTC)) == 60

    # Tibber heeft morgen: voorlopige prijzen weg
    tomorrow = [PriceSlot(s.start + timedelta(days=1), s.end + timedelta(days=1), s.price) for s in today]
    answers.append(today + tomorrow)
    asyncio.run(e.refresh_prices(force=True))
    assert len(e.prices) == 192
    assert not any(p.level == energyzero.PROVISIONAL for p in e.prices)
    assert e.provisional_since is None


def test_engine_without_energyzero_integration(tmp_path, monkeypatch):
    spot, _ = ez_response(DAY, 48)
    e = Engine(Config(timezone="Europe/Amsterdam", tibber_token="x"), None, Store(tmp_path / "t.db"))

    class NoEZ(FakeHA):
        async def command(self, payload):
            return []

    e.ha = NoEZ(None)
    assert asyncio.run(e._energyzero_tomorrow(tibber_today(DAY, spot))) == []
    assert e._ez_entry == ""
