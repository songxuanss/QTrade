"""Effective-interval universe membership helpers and coverage disclosure."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True, slots=True)
class MembershipWindow:
    instrument_id: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.instrument_id, str)
            or not self.instrument_id
            or self.start.utcoffset() is None
            or self.end.utcoffset() is None
            or self.start >= self.end
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid membership window")


@dataclass(frozen=True, slots=True)
class UniverseCoverage:
    instruments: int
    full_coverage: int
    partial_coverage: int
    unknown: int

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not int or value < 0
                for value in (
                    self.instruments,
                    self.full_coverage,
                    self.partial_coverage,
                    self.unknown,
                )
            )
            or self.full_coverage + self.partial_coverage + self.unknown != self.instruments
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid universe coverage")


def membership_allows(windows: tuple[MembershipWindow, ...], moment: datetime) -> bool:
    """Half-open [start, end) membership: an instrument leaves at `end`."""
    return any(window.start <= moment < window.end for window in windows)


def coverage_for(
    instrument_ids: Iterable[str],
    windows: Iterable[MembershipWindow],
    start: datetime,
    end: datetime,
) -> UniverseCoverage:
    """Classify how well the supplied intervals describe the run window."""
    if start.utcoffset() is None or end.utcoffset() is None or start >= end:
        raise PortError(ErrorCode.INVALID_INPUT, "invalid coverage range")
    instrument_ids = tuple(instrument_ids)
    grouped: dict[str, list[MembershipWindow]] = {}
    for window in windows:
        if window.instrument_id in instrument_ids:
            grouped.setdefault(window.instrument_id, []).append(window)
    full = partial = unknown = 0
    for instrument in instrument_ids:
        intervals = grouped.get(instrument)
        if not intervals:
            unknown += 1
        elif any(item.start <= start and item.end >= end for item in intervals):
            full += 1
        else:
            partial += 1
    return UniverseCoverage(len(set(instrument_ids)), full, partial, unknown)


def disclosure(mode: str, coverage: UniverseCoverage) -> str:
    if mode == "dataset":
        return (
            "未启用成员区间校验：数据集只包含采集时仍可交易的标的，"
            "已退市或中途移出的标的可能缺失，存在幸存者偏差。"
        )
    if mode != "membership":
        raise PortError(ErrorCode.INVALID_INPUT, "invalid universe mode")
    return (
        f"已按 {coverage.instruments} 个标的的成员区间回放："
        f"{coverage.full_coverage} 个全程覆盖、{coverage.partial_coverage} 个区间不完整、"
        f"{coverage.unknown} 个未知。非成员区间内不交易，成员区间结束时按最后可用收盘价强制平仓；"
        "区间若由工作区定义而非交易所历史，仍可能低估幸存者偏差。"
    )
