"""Apply frozen corporate actions and vendor adjustment factors to a ledger."""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, Decimal

from qtrade_data_contracts.research import AdjustmentFactor, CorporateAction
from qtrade_domain.ports import ErrorCode, PortError

RightsPolicy = str


@dataclass(frozen=True, slots=True)
class RightsSubscription:
    instrument_id: str
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True)
class CorporateActionResult:
    quantity: Decimal
    available: Decimal
    cash: Decimal
    applied: tuple[CorporateAction, ...]
    subscriptions: tuple[RightsSubscription, ...] = ()


@dataclass(slots=True)
class CorporateActionLedger:
    """Idempotent ex-date ledger.

    Cash is credited from ex-date holdings before that day's fills. Bonus and
    transfer shares increase total quantity but not same-day sellable quantity;
    splits scale both. Rights are subscribed from available cash under an
    explicit policy and never inflate same-day sellable quantity.
    """

    actions: tuple[CorporateAction, ...]
    rights_policy: RightsPolicy = "subscribe"
    _by_instrument: dict[str, list[CorporateAction]] = field(init=False)
    _applied: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        if self.rights_policy not in {"subscribe", "skip"}:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid rights policy")
        grouped: dict[str, list[CorporateAction]] = {}
        for action in self.actions:
            if not isinstance(action, CorporateAction):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid corporate action")
            if action.action == "rights" and (
                action.rights_shares is None or action.rights_price is None
            ):
                raise PortError(ErrorCode.UNSUPPORTED, "rights terms are incomplete")
            if action.ex_date is None:
                raise PortError(ErrorCode.UNSUPPORTED, "corporate action requires an ex-date")
            grouped.setdefault(action.instrument_id, []).append(action)
        for values in grouped.values():
            values.sort(key=lambda item: (item.ex_date, item.event_identity))
        self._by_instrument = grouped

    def apply(
        self,
        instrument_id: str,
        market_date: date,
        quantity: Decimal,
        available: Decimal,
        cash: Decimal,
        currency: str,
    ) -> CorporateActionResult:
        applied: list[CorporateAction] = []
        subscriptions: list[RightsSubscription] = []
        # Cash and rights entitlements are fixed at the start of the ex-date.
        # Applying a bonus/split first must not inflate them for the same day.
        entitlement = quantity
        for action in self._by_instrument.get(instrument_id, ()):
            if action.event_identity in self._applied:
                continue
            assert action.ex_date is not None
            if market_date < action.ex_date:
                continue
            if action.cash is not None:
                if action.cash_currency != currency:
                    raise PortError(
                        ErrorCode.UNSUPPORTED,
                        "corporate-action cash currency differs from the run",
                    )
                cash += entitlement / action.basis_shares * action.cash
            if action.bonus_shares is not None or action.transfer_shares is not None:
                extra = (
                    quantity
                    / action.basis_shares
                    * ((action.bonus_shares or Decimal(0)) + (action.transfer_shares or Decimal(0)))
                ).to_integral_value(rounding=ROUND_DOWN)
                quantity += extra
            if action.split_ratio is not None:
                quantity = (quantity * action.split_ratio).to_integral_value(rounding=ROUND_DOWN)
                available = (available * action.split_ratio).to_integral_value(rounding=ROUND_DOWN)
            if action.action == "rights" and action.rights_shares is not None:
                price = action.rights_price or Decimal(0)
                entitled = (
                    entitlement / action.basis_shares * action.rights_shares
                ).to_integral_value(rounding=ROUND_DOWN)
                if self.rights_policy == "subscribe" and entitled > 0 and price > 0:
                    affordable = (cash / price).to_integral_value(rounding=ROUND_DOWN)
                    subscribed = min(entitled, affordable)
                    if subscribed > 0:
                        cash -= subscribed * price
                        # Rights shares are not sellable on the ex-date.
                        quantity += subscribed
                        subscriptions.append(RightsSubscription(instrument_id, subscribed, price))
            self._applied.add(action.event_identity)
            applied.append(action)
        return CorporateActionResult(
            quantity, available, cash, tuple(applied), tuple(subscriptions)
        )


def validate_adjustment_factors(
    actions: tuple[CorporateAction, ...],
    factors: tuple[AdjustmentFactor, ...],
    instrument_ids: tuple[str, ...],
) -> tuple[AdjustmentFactor, ...]:
    """Vendor factors must be explained by frozen events; never applied silently.

    Only the change days matter: every factor change must coincide with a
    frozen corporate action ex-date for that instrument. Mixing forward and
    backward conventions is ambiguous and is rejected.
    """
    if not factors:
        return ()
    allowed = set(instrument_ids)
    ex_dates: dict[str, set[date]] = {}
    for action in actions:
        if action.ex_date is not None:
            ex_dates.setdefault(action.instrument_id, set()).add(action.ex_date)
    grouped: dict[str, list[AdjustmentFactor]] = {}
    for factor in factors:
        if factor.instrument_id not in allowed:
            raise PortError(ErrorCode.INVALID_INPUT, "adjustment factor is outside the dataset")
        grouped.setdefault(factor.instrument_id, []).append(factor)
    for instrument, values in grouped.items():
        conventions = {item.adjustment for item in values}
        if len(conventions) != 1:
            raise PortError(
                ErrorCode.UNSUPPORTED,
                "mixed adjustment-factor conventions are not supported",
            )
        ordered = sorted(values, key=lambda item: item.observation_date)
        unexplained = {
            item.observation_date
            for previous, item in zip(ordered, ordered[1:], strict=False)
            if item.factor != previous.factor
            and item.observation_date not in ex_dates.get(instrument, set())
        }
        if unexplained:
            raise PortError(
                ErrorCode.UNSUPPORTED,
                "vendor adjustment factors change without a frozen corporate action on "
                + ", ".join(sorted(day.isoformat() for day in unexplained)[:8]),
            )
    return tuple(sorted(factors, key=lambda item: (item.instrument_id, item.observation_date)))
