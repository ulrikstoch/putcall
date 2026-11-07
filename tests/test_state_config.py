import json

import pytest

from xspbot.config import Config, ConfigError
from xspbot.state import BotState, StateStore


class TestState:
    def test_default_when_missing(self, tmp_path):
        assert StateStore(tmp_path / "s.json").load() == BotState()

    def test_roundtrip(self, tmp_path):
        s = StateStore(tmp_path / "s.json")
        s.save(BotState(last_trade_date="2026-11-10", done=False))
        assert s.load().last_trade_date == "2026-11-10"

    def test_mark_and_clear_trade_date(self, tmp_path):
        s = StateStore(tmp_path / "s.json")
        s.mark_trade_date("2026-11-10")
        assert s.load().last_trade_date == "2026-11-10"
        s.clear_trade_date("2026-11-09")  # anden dato -> uændret
        assert s.load().last_trade_date == "2026-11-10"
        s.clear_trade_date("2026-11-10")
        assert s.load().last_trade_date is None

    def test_done_persists_and_reset(self, tmp_path):
        s = StateStore(tmp_path / "s.json")
        s.mark_done("saldo 1500", "2026-11-10T15:01")
        assert StateStore(tmp_path / "s.json").load().done is True
        s.reset_done()
        assert s.load().done is False

    def test_unknown_keys_ignored_and_no_temp_files(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"done": True, "foo": 1}))
        s = StateStore(p)
        assert s.load().done is True
        s.mark_trade_date("2026-11-10")
        assert [x.name for x in tmp_path.iterdir()] == ["s.json"]


class TestConfig:
    def test_defaults_paper(self):
        c = Config.from_env({})
        assert c.ib_port == 4002 and not c.is_live and not c.dry_run
        assert c.fee_per_contract == 1.10 and c.target_usd == 1450

    def test_env_override(self):
        c = Config.from_env({"DRY_RUN": "1", "FEE_PER_CONTRACT": "0.9", "TIME_STOP": "14:45"})
        assert c.dry_run is True and c.fee_per_contract == 0.9
        assert c.time_stop.hour == 14 and c.time_stop.minute == 45

    def test_live_requires_explicit_env(self):
        c = Config.from_env({"IB_PORT": "4001"})
        assert c.is_live
        c2 = Config(ib_port=4001)
        with pytest.raises(ConfigError):
            c2.validate({})

    def test_bad_value(self):
        with pytest.raises(ConfigError):
            Config.from_env({"IB_PORT": "abc"})
