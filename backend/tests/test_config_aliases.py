import os

from backend.config import get_producthunt_token


def test_producthunt_env_alias_resolution(monkeypatch):
    monkeypatch.delenv("PHUNT", raising=False)
    monkeypatch.delenv("PRODUCTHUNT_DEVELOPER_TOKEN", raising=False)
    monkeypatch.setenv("PRODUCT_HUNT", "token_from_alias")
    assert get_producthunt_token() == "token_from_alias"


def test_producthunt_prefers_primary(monkeypatch):
    monkeypatch.setenv("PHUNT", "token_primary")
    monkeypatch.setenv("PRODUCT_HUNT", "token_alias")
    assert get_producthunt_token() == "token_primary"

