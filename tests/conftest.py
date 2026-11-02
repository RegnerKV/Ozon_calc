import pytest


@pytest.fixture(autouse=True)
def desktop_test_mode(monkeypatch):
    monkeypatch.setenv('OZON_PROFIT_LOCAL','1')
