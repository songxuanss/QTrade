"""Stable summary metrics derived from immutable equity observations."""

from datetime import UTC
from decimal import Decimal

from qtrade_contracts import BacktestEquityPoint, BacktestFill, BacktestMetrics
from qtrade_domain.ports import ErrorCode, PortError


def calculate_backtest_metrics(
    points: tuple[BacktestEquityPoint, ...],
    *,
    periods_per_year: int | None = None,
    benchmark_return: Decimal | None = None,
    fills: tuple[BacktestFill, ...] = (),
    initial_cash: Decimal | None = None,
) -> BacktestMetrics:
    """Calculate performance and exposure without guessing sampling frequency."""
    if (
        not points
        or any(not isinstance(point, BacktestEquityPoint) for point in points)
        or any(not isinstance(fill, BacktestFill) for fill in fills)
        or (
            periods_per_year is not None
            and (type(periods_per_year) is not int or periods_per_year < 2)
        )
        or (
            benchmark_return is not None
            and (not isinstance(benchmark_return, Decimal) or not benchmark_return.is_finite())
        )
        or (
            initial_cash is not None
            and (
                not isinstance(initial_cash, Decimal)
                or not initial_cash.is_finite()
                or initial_cash <= 0
            )
        )
        or any(
            left.timestamp.astimezone(UTC) >= right.timestamp.astimezone(UTC)
            for left, right in zip(points, points[1:], strict=False)
        )
    ):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid backtest metric inputs")

    equities = tuple(point.equity for point in points)
    # The first equity point is already after the first bar's fills and fees.
    # Risk and growth must start from the funded account, otherwise the first
    # loss disappears from drawdown and CAGR.
    start_equity = initial_cash if initial_cash is not None else equities[0]
    peak = start_equity
    max_drawdown = Decimal(0)
    for equity in equities:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    max_drawdown = max(max_drawdown, *(point.drawdown for point in points))

    last = equities[-1]
    elapsed = (
        points[-1].timestamp.astimezone(UTC) - points[0].timestamp.astimezone(UTC)
    ).total_seconds()
    annualized_return: Decimal | None = None
    if elapsed >= 86400 and start_equity > 0 and last >= 0:
        years = Decimal(str(elapsed)) / Decimal("31556952")
        annualized_return = (
            (last / start_equity) ** (Decimal(1) / years) - 1 if last > 0 else Decimal(-1)
        )

    period_equities = (start_equity, *equities)
    returns = tuple(
        (current / previous) - 1
        for previous, current in zip(period_equities, period_equities[1:], strict=False)
        if previous > 0
    )
    volatility: Decimal | None = None
    sharpe: Decimal | None = None
    sortino: Decimal | None = None
    if periods_per_year is not None and len(returns) >= 2:
        mean = sum(returns, Decimal(0)) / len(returns)
        variance = sum(((value - mean) ** 2 for value in returns), Decimal(0)) / (len(returns) - 1)
        deviation = variance.sqrt()
        if deviation > 0:
            annualizer = Decimal(periods_per_year).sqrt()
            volatility = deviation * annualizer
            sharpe = mean / deviation * annualizer
        downside = tuple(min(value, Decimal(0)) for value in returns)
        downside_deviation = (
            sum((value**2 for value in downside), Decimal(0)) / len(downside)
        ).sqrt()
        if downside_deviation > 0:
            sortino = mean / downside_deviation * Decimal(periods_per_year).sqrt()

    # Trade matching must not depend on an adapter's dictionary/input order.
    ordered_fills = tuple(sorted(fills, key=lambda fill: fill.filled_at))
    lots: dict[str, list[list[Decimal]]] = {}
    gross_profit = Decimal(0)
    gross_loss = Decimal(0)
    trade_count = 0
    winning_trades = 0
    losing_trades = 0
    total_traded_notional = Decimal(0)
    for fill in ordered_fills:
        quantity = fill.filled_quantity
        direction = Decimal(1) if fill.side == "buy" else Decimal(-1)
        fee_per_unit = fill.fee / quantity
        book = lots.setdefault(fill.instrument_id, [])
        realized_this_fill = Decimal(0)
        while quantity > 0 and book and book[0][0] * direction < 0:
            lot = book[0]
            matched = min(quantity, abs(lot[0]))
            realized_this_fill += (fill.price - lot[1]) * matched * (
                Decimal(1) if lot[0] > 0 else Decimal(-1)
            ) - matched * (lot[2] + fee_per_unit)
            lot[0] += -matched if lot[0] > 0 else matched
            quantity -= matched
            if lot[0] == 0:
                book.pop(0)
        if quantity > 0:
            book.append([quantity * direction, fill.price, fee_per_unit])
        if quantity < fill.filled_quantity:
            trade_count += 1
            if realized_this_fill > 0:
                winning_trades += 1
                gross_profit += realized_this_fill
            elif realized_this_fill < 0:
                losing_trades += 1
                gross_loss += abs(realized_this_fill)
        total_traded_notional += fill.filled_quantity * fill.price

    exposures = tuple(
        sum((abs(position.market_value) for position in point.positions), Decimal(0)) / point.equity
        for point in points
        if point.equity > 0
    )
    denominator = initial_cash if initial_cash is not None else start_equity
    turnover = total_traded_notional / denominator if denominator > 0 else Decimal(0)
    calmar = (
        annualized_return / max_drawdown
        if max_drawdown > 0 and annualized_return is not None
        else None
    )

    return BacktestMetrics(
        max_drawdown=max_drawdown,
        annualized_return=annualized_return,
        annualized_volatility=volatility,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        benchmark_return=benchmark_return,
        calmar_ratio=calmar,
        win_rate=(Decimal(winning_trades) / trade_count) if trade_count else None,
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else None,
        turnover=turnover,
        average_exposure=(sum(exposures, Decimal(0)) / len(exposures)) if exposures else Decimal(0),
        max_exposure=max(exposures, default=Decimal(0)),
        trade_count=trade_count,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
    )
