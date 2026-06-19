"""Build the existing live AlphaTrade runtime without importing Streamlit.

This module mirrors the effective dashboard startup configuration.  It does
not introduce strategy values: persisted risk settings, aggressive mode and
live settings remain the sources of truth.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class RuntimeBootstrapError(RuntimeError):
    """Fail-closed startup error for missing or inconsistent live config."""


@dataclass
class RuntimeConfig:
    active_symbols: List[str]
    strategy: str
    interval: str
    check_every: int
    threshold_percent: float
    initial_balance: float
    exchange_mode: str
    mexc_live_orders: bool
    use_scanner_symbols: bool
    aggressive_mode: str
    risk_settings: Any
    global_risk_settings: Any
    per_symbol_risk_settings: Dict[str, Any] = field(default_factory=dict)
    binance_client: Any = field(default=None, repr=False)
    telegram: Dict[str, Any] = field(default_factory=dict, repr=False)
    min_volume_multiple: float = 0.30


@dataclass
class RuntimeContext:
    config: RuntimeConfig
    bot: Any
    log_activity: Callable[[str, str], None]


def _apply_values(target: Any, values: Any, *, skip: set[str] | None = None) -> None:
    if not isinstance(values, dict):
        return
    ignored = skip or set()
    for key, value in values.items():
        if key not in ignored and hasattr(target, key):
            setattr(target, key, value)


def load_runtime_config(
    *,
    persisted: Optional[dict] = None,
    settings_loader: Optional[Callable[[], dict]] = None,
    credentials_loader: Optional[Callable[[], Any]] = None,
    mexc_credentials_loader: Optional[Callable[[], Any]] = None,
    binance_client_factory: Optional[Callable[[str, str], Any]] = None,
    aggressive_module: Any = None,
    live_settings_module: Any = None,
) -> RuntimeConfig:
    """Load the same effective runtime values used by the dashboard.

    Credential values are used only to construct the authenticated client and
    are never retained in ``RuntimeConfig``.
    """
    from risk import (
        GlobalRiskSettings,
        RiskSettings,
    )

    if settings_loader is None:
        from bot import load_settings

        settings_loader = load_settings
    if aggressive_module is None:
        import aggressive_mode as aggressive_module
    if live_settings_module is None:
        import live_settings as live_settings_module
    if credentials_loader is None:
        from secrets_store import load_credentials

        credentials_loader = load_credentials
    if mexc_credentials_loader is None:
        from exchanges.mexc import load_mexc_credentials

        mexc_credentials_loader = load_mexc_credentials
    if binance_client_factory is None:
        from binance_client import BinanceClient

        binance_client_factory = BinanceClient

    saved = dict(persisted if persisted is not None else (settings_loader() or {}))

    risk_settings = RiskSettings()
    _apply_values(
        risk_settings,
        saved.get("risk"),
        skip={"emergency_stop"},
    )
    risk_settings.emergency_stop = False
    if not getattr(risk_settings, "dynamic_size_pct", 0):
        risk_settings.dynamic_size_pct = 40.0

    global_risk = GlobalRiskSettings()
    _apply_values(
        global_risk,
        saved.get("global_risk"),
        skip={"emergency_stop"},
    )
    global_risk.emergency_stop = False
    global_risk.max_open_trades_binance = max(
        1, min(3, int(getattr(global_risk, "max_open_trades_binance", 3) or 3))
    )
    global_risk.max_open_trades_mexc = max(
        1, min(15, int(getattr(global_risk, "max_open_trades_mexc", 15) or 15))
    )
    global_risk.max_open_trades_total = (
        global_risk.max_open_trades_binance + global_risk.max_open_trades_mexc
    )

    per_symbol: Dict[str, Any] = {}
    for symbol, values in (saved.get("per_symbol_risk") or {}).items():
        if not isinstance(values, dict):
            continue
        item = RiskSettings()
        _apply_values(item, values, skip={"emergency_stop"})
        item.emergency_stop = False
        if not getattr(item, "dynamic_size_pct", 0):
            item.dynamic_size_pct = 40.0
        per_symbol[str(symbol)] = item

    aggressive_mode = aggressive_module.get_mode()
    aggressive_module.apply_profile_to_risk(risk_settings, aggressive_mode)
    for item in per_symbol.values():
        aggressive_module.apply_profile_to_risk(item, aggressive_mode)
    profile = aggressive_module.get_profile(aggressive_mode)

    live = live_settings_module.get_settings()
    volume_multiple = float(getattr(live, "min_volume_multiple", 0.0) or 0.0)
    if not math.isclose(volume_multiple, 0.30, rel_tol=0.0, abs_tol=1e-9):
        raise RuntimeBootstrapError(
            "Refusing headless start: effective min_volume_multiple must remain 0.30"
        )
    if not bool(getattr(live, "volume_filter_on", False)):
        raise RuntimeBootstrapError(
            "Refusing headless start: volume_filter_on is disabled"
        )
    if not bool(getattr(live, "trend_filter_on", False)):
        raise RuntimeBootstrapError(
            "Refusing headless start: trend_filter_on is disabled"
        )

    active_symbols = list(
        saved.get("active_symbols") or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    )
    if len(active_symbols) < 2:
        active_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    mexc_live_orders = True  # mirrors the dashboard's current cold-start invariant
    binance_credentials = credentials_loader()
    if not binance_credentials:
        raise RuntimeBootstrapError("Saved Binance credentials are required")
    binance_client = binance_client_factory(*binance_credentials)
    ok, message = binance_client.test_connection()
    if not ok:
        raise RuntimeBootstrapError(f"Binance connection failed: {message}")
    if mexc_live_orders and not mexc_credentials_loader():
        raise RuntimeBootstrapError("Saved MEXC credentials are required for LIVE mode")

    return RuntimeConfig(
        active_symbols=active_symbols,
        strategy="Market Low",
        interval="1m",
        check_every=int(profile["check_every"]),
        threshold_percent=0.01,
        initial_balance=float(saved.get("initial_balance", 1000.0)),
        exchange_mode="multi",
        mexc_live_orders=mexc_live_orders,
        use_scanner_symbols=bool(saved.get("use_scanner_symbols", True)),
        aggressive_mode=aggressive_mode,
        risk_settings=risk_settings,
        global_risk_settings=global_risk,
        per_symbol_risk_settings=per_symbol,
        binance_client=binance_client,
        telegram={
            "token": saved.get("tg_token", ""),
            "chat_id": saved.get("tg_chat_id", ""),
            "enabled": bool(saved.get("tg_enabled", False)),
        },
        min_volume_multiple=volume_multiple,
    )


def build_runtime_context(
    config: RuntimeConfig,
    *,
    bot_module: Any = None,
    aggressive_module: Any = None,
    telegram_module: Any = None,
) -> RuntimeContext:
    """Build, but do not start, the existing ``TradingBot`` singleton."""
    from risk import GlobalRiskManager, RiskManager

    if bot_module is None:
        import bot as bot_module
    if aggressive_module is None:
        import aggressive_mode as aggressive_module
    if telegram_module is None:
        import telegram_notifier as telegram_module

    telegram_module.configure(**config.telegram)

    if config.use_scanner_symbols:
        plan = bot_module.resolve_live_plan(
            top_n_mexc=config.global_risk_settings.max_open_trades_mexc
        )
        if plan:
            symbols = [item["symbol"] for item in plan]
            venues = {item["symbol"]: item["exchange"] for item in plan}
        else:
            symbols = list(config.active_symbols)
            venues = {}
        scanner_driven = True
    else:
        symbols = list(config.active_symbols)
        venues = {}
        scanner_driven = False

    shared_risk = RiskManager(config.risk_settings)
    per_symbol_risk = {
        symbol: RiskManager(config.per_symbol_risk_settings[symbol])
        if symbol in config.per_symbol_risk_settings
        else shared_risk
        for symbol in symbols
    }
    global_risk = GlobalRiskManager(config.global_risk_settings)

    instance = bot_module.create_bot(
        client=config.binance_client,
        symbols=symbols,
        per_symbol_risk=per_symbol_risk,
        global_risk=global_risk,
        strategy=config.strategy,
        risk_manager=shared_risk,
        interval=config.interval,
        check_every=config.check_every,
        threshold=config.threshold_percent / 100,
        initial_balance=config.initial_balance,
        ai_assist=True,
        ai_aggressiveness="Active Scalper",
        exchange_mode=config.exchange_mode,
        mexc_live_orders=config.mexc_live_orders,
        symbol_venues=venues,
        scanner_driven=scanner_driven,
        rotate_scanner=scanner_driven,
        scanner_top_n=config.global_risk_settings.max_open_trades_mexc,
        manage_manual_trades=bool(
            getattr(config.global_risk_settings, "manage_manual_trades", False)
        ),
    )
    aggressive_module.apply_profile_to_bot(instance, config.aggressive_mode)
    instance._initial_balance = config.initial_balance
    return RuntimeContext(
        config=config,
        bot=instance,
        log_activity=bot_module.log_activity,
    )


def bootstrap_runtime(**kwargs) -> RuntimeContext:
    """Load configuration and construct the non-started live runtime."""
    config = load_runtime_config(**kwargs)
    return build_runtime_context(config)
