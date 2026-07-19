"""Dynamic Position Sizing + Adaptive Leverage — spec section 30.2.

    Dynamic Position Sizing: Kelly criterion variant adjusted by current
    volatility and correlation.
    Adaptive Leverage: Inverse function of volatility and risk score.
    Max 125x (Binance limit), typically 1-20x.

Like the Decision Fusion Engine's weighted vote, these are deliberately
transparent, explainable heuristic formulas — the exact seam a more
sophisticated (ML-fitted Kelly, regime-conditioned vol model, ...) version
plugs into later without changing the function signatures.
"""

from __future__ import annotations

from aitos.risk.models import PositionSizeResult, RiskLimits


def kelly_fraction(win_rate: float, win_loss_ratio: float) -> float:
    """Classic Kelly fraction: f* = W - (1-W)/R, clamped to [0, 1].

    ``win_rate`` (W) is the historical/expected probability of a winning
    trade; ``win_loss_ratio`` (R) is average win size / average loss size.
    """
    if not 0.0 <= win_rate <= 1.0:
        raise ValueError("win_rate must be within [0.0, 1.0]")
    if win_loss_ratio <= 0:
        raise ValueError("win_loss_ratio must be positive")
    f = win_rate - (1.0 - win_rate) / win_loss_ratio
    return max(0.0, min(f, 1.0))


def calculate_adaptive_leverage(
    volatility_percentile: float,
    risk_score: float,
    risk_limits: RiskLimits,
    base_leverage: float = 10.0,
) -> float:
    """Leverage shrinks as volatility and/or risk score rise.

    ``volatility_percentile`` and ``risk_score`` are both 0-100. At the
    extremes (100/100) leverage bottoms out near 1x; at 0/0 it's
    ``base_leverage``. Always clamped to ``risk_limits.max_leverage``.
    """
    volatility_percentile = max(0.0, min(volatility_percentile, 100.0))
    risk_score = max(0.0, min(risk_score, 100.0))

    vol_damp = 1.0 - (volatility_percentile / 100.0) * 0.8   # 0.2x .. 1.0x
    risk_damp = 1.0 - (risk_score / 100.0) * 0.9              # 0.1x .. 1.0x

    leverage = base_leverage * vol_damp * risk_damp
    leverage = max(1.0, min(leverage, risk_limits.max_leverage))
    return round(leverage, 2)


def calculate_position_size(
    equity_usd: float,
    entry_price: float,
    stop_loss_price: float,
    risk_limits: RiskLimits,
    risk_score: float = 0.0,
    win_rate: float | None = None,
    win_loss_ratio: float | None = None,
    volatility_percentile: float = 50.0,
    correlation_penalty: float = 0.0,
    requested_risk_pct: float | None = None,
    base_leverage: float = 10.0,
) -> PositionSizeResult:
    """Compute a position size in USD notional plus the leverage to use.

    Sizing logic:
    1. Start from ``requested_risk_pct`` (or the configured default),
       capped at the hard-cap limit — never exceedable regardless of input.
    2. If ``win_rate``/``win_loss_ratio`` are supplied, scale risk by a
       Kelly-derived confidence factor (a coin-flip-edge Kelly of 0.5 keeps
       the requested risk unchanged; lower edges shrink it, floor 10%).
    3. Dampen further for volatility and open-position correlation — both
       only ever shrink size, never grow it beyond step 1's cap.
    4. Convert the resulting risk-in-dollars to a position size via the
       stop distance, and derive leverage independently via
       ``calculate_adaptive_leverage``.
    """
    if equity_usd <= 0:
        raise ValueError("equity_usd must be positive")
    stop_distance = abs(entry_price - stop_loss_price)
    if stop_distance <= 0:
        raise ValueError("stop_loss_price must differ from entry_price")

    risk_pct = requested_risk_pct if requested_risk_pct is not None else risk_limits.max_risk_per_trade_pct
    risk_pct = min(risk_pct, risk_limits.max_risk_per_trade_hard_cap_pct)

    kelly_note = ""
    if win_rate is not None and win_loss_ratio is not None:
        kf = kelly_fraction(win_rate, win_loss_ratio)
        kelly_scalar = max(min(kf / 0.5, 1.0), 0.1) if kf > 0 else 0.1
        risk_pct *= kelly_scalar
        kelly_note = f", kelly_fraction={kf:.3f} (scalar={kelly_scalar:.2f})"

    vol_factor = 1.0 - (max(0.0, min(volatility_percentile, 100.0)) / 100.0) * 0.5   # 0.5x .. 1.0x
    corr_factor = 1.0 - max(0.0, min(correlation_penalty, 1.0)) * 0.5                # 0.5x .. 1.0x
    risk_pct *= vol_factor * corr_factor

    risk_amount_usd = equity_usd * (risk_pct / 100.0)
    units = risk_amount_usd / stop_distance
    position_size_usd = units * entry_price

    leverage = calculate_adaptive_leverage(volatility_percentile, risk_score, risk_limits, base_leverage)

    rationale = (
        f"risk={risk_pct:.3f}% of equity (vol_factor={vol_factor:.2f}, corr_factor={corr_factor:.2f}"
        f"{kelly_note}), leverage={leverage}x (volatility_percentile={volatility_percentile}, risk_score={risk_score})"
    )
    return PositionSizeResult(
        position_size_usd=round(position_size_usd, 2),
        leverage=leverage,
        risk_amount_usd=round(risk_amount_usd, 2),
        rationale=rationale,
    )
