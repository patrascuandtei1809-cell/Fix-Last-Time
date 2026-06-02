"""Put the `trading/` package dir on sys.path so tests can import the research /
validation modules directly (`import validate_candidates`, `import research`)."""
import os
import sys

TRADING_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TRADING_DIR not in sys.path:
    sys.path.insert(0, TRADING_DIR)
