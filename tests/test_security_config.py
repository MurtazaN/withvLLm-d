"""Sanity tests for ``blue_lantern.backend.security.build_security_config``.

Verify that env vars override the lenient defaults and that the empty-
whitelist case disables the firewall while leaving rate-limit + auto-
ban active.
"""

import pytest

from blue_lantern.backend.security import DEFAULT_WHITELIST, build_security_config


_ENV_KEYS = (
    "BLUE_LANTERN_RATE_LIMIT",
    "BLUE_LANTERN_RATE_WINDOW",
    "BLUE_LANTERN_AUTO_BAN_THRESHOLD",
    "BLUE_LANTERN_AUTO_BAN_DURATION",
    "BLUE_LANTERN_IP_WHITELIST",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_defaults_are_lenient():
    cfg = build_security_config()
    assert cfg.rate_limit == 200
    assert cfg.rate_limit_window == 60
    assert cfg.auto_ban_threshold == 20
    assert cfg.auto_ban_duration == 3600
    assert cfg.enable_rate_limiting is True
    assert cfg.enable_ip_banning is True
    assert cfg.enforce_https is False


def test_default_whitelist_includes_loopback_and_rfc1918():
    cfg = build_security_config()
    assert "127.0.0.1" in cfg.whitelist
    assert "10.0.0.0/8" in cfg.whitelist
    assert "172.16.0.0/12" in cfg.whitelist
    assert "192.168.0.0/16" in cfg.whitelist
    # Sanity: the constant the code uses produces the same set.
    assert set(DEFAULT_WHITELIST.split(",")) == set(cfg.whitelist)


def test_env_overrides_numeric_knobs(monkeypatch):
    monkeypatch.setenv("BLUE_LANTERN_RATE_LIMIT", "10")
    monkeypatch.setenv("BLUE_LANTERN_RATE_WINDOW", "30")
    monkeypatch.setenv("BLUE_LANTERN_AUTO_BAN_THRESHOLD", "5")
    monkeypatch.setenv("BLUE_LANTERN_AUTO_BAN_DURATION", "120")
    cfg = build_security_config()
    assert cfg.rate_limit == 10
    assert cfg.rate_limit_window == 30
    assert cfg.auto_ban_threshold == 5
    assert cfg.auto_ban_duration == 120


def test_env_overrides_whitelist(monkeypatch):
    monkeypatch.setenv("BLUE_LANTERN_IP_WHITELIST", "127.0.0.1, 192.0.2.42")
    cfg = build_security_config()
    assert cfg.whitelist == ["127.0.0.1", "192.0.2.42"]


def test_empty_whitelist_disables_filter(monkeypatch):
    monkeypatch.setenv("BLUE_LANTERN_IP_WHITELIST", "")
    cfg = build_security_config()
    # Guard treats None / empty as "no whitelist applied".
    assert cfg.whitelist is None
    # Rate-limiting and banning still active.
    assert cfg.enable_rate_limiting is True
    assert cfg.enable_ip_banning is True
