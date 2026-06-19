"""Put the `trading/` package dir on sys.path so tests can import the research /
validation modules directly (`import validate_candidates`, `import research`)."""
import os
import sys
from unittest.mock import MagicMock

TRADING_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TRADING_DIR not in sys.path:
    sys.path.insert(0, TRADING_DIR)


def _ensure_binance_stub() -> None:
    """Allow `import bot` in unit tests when python-binance is not installed."""
    if "binance" in sys.modules:
        return
    _pkg = MagicMock()
    _client_mod = MagicMock()
    _client_mod.Client = MagicMock()
    _exceptions = MagicMock()
    _exceptions.BinanceAPIException = Exception
    _pkg.client = _client_mod
    _pkg.exceptions = _exceptions
    sys.modules["binance"] = _pkg
    sys.modules["binance.client"] = _client_mod
    sys.modules["binance.exceptions"] = _exceptions


_ensure_binance_stub()
