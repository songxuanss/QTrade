#!/usr/bin/env python3
"""Generate the official strategy template library consumed by the Web workbench.

The JSON file is the single source of truth for both the Web UI and the Python
static-validation tests, so it is generated here instead of being hand-edited.
Run with ``--check`` to verify the committed file is current.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "apps/web/src/features/strategies/templates.json"

SMA = '''from qtrade import Strategy


class MovingAverageCross(Strategy):
    """双均线交叉：快线上穿慢线买入，下穿卖出，逐标的独立维护状态。"""

    def on_start(self):
        self.fast = int(self.parameters.get("fast", 5))
        self.slow = int(self.parameters.get("slow", 20))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.fast < 2 or self.fast >= self.slow:
            raise ValueError("fast must be at least 2 and smaller than slow")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.closes = {}
        self.quantity = {}

    def on_bar(self, bar):
        instrument = bar.instrument_id
        closes = self.closes.setdefault(instrument, [])
        closes.append(float(bar.close))
        if len(closes) < self.slow + 1:
            return
        fast_now = sum(closes[-self.fast:]) / self.fast
        slow_now = sum(closes[-self.slow:]) / self.slow
        fast_prev = sum(closes[-self.fast - 1:-1]) / self.fast
        slow_prev = sum(closes[-self.slow - 1:-1]) / self.slow
        held = self.quantity.get(instrument, 0)
        if fast_prev <= slow_prev and fast_now > slow_now and held == 0:
            size = int(self.position_value / closes[-1] / self.lot) * self.lot
            if size > 0:
                self.buy(size, instrument_id=instrument)
                self.quantity[instrument] = size
        elif fast_prev >= slow_prev and fast_now < slow_now and held > 0:
            self.sell(held, instrument_id=instrument)
            self.quantity[instrument] = 0
'''

RSI = '''from qtrade import Strategy


class RsiReversal(Strategy):
    """RSI 均值回归：超卖买入、超买卖出，逐标的独立计算 RSI。"""

    def on_start(self):
        self.period = int(self.parameters.get("period", 14))
        self.oversold = float(self.parameters.get("oversold", 30))
        self.overbought = float(self.parameters.get("overbought", 70))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.period < 2 or self.oversold >= self.overbought:
            raise ValueError("invalid rsi parameters")
        if self.oversold <= 0 or self.overbought >= 100:
            raise ValueError("thresholds must be inside 0-100")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.previous = {}
        self.average_gain = {}
        self.average_loss = {}
        self.samples = {}
        self.quantity = {}

    def rsi(self, instrument, close):
        previous = self.previous.get(instrument)
        if previous is None:
            self.previous[instrument] = close
            self.average_gain[instrument] = 0.0
            self.average_loss[instrument] = 0.0
            self.samples[instrument] = 0
            return None
        change = close - previous
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        self.samples[instrument] += 1
        count = self.samples[instrument]
        window = self.period if count > self.period else count
        self.average_gain[instrument] = (
            self.average_gain[instrument] * (window - 1) + gain
        ) / window
        self.average_loss[instrument] = (
            self.average_loss[instrument] * (window - 1) + loss
        ) / window
        if count < self.period:
            return None
        if self.average_loss[instrument] == 0:
            return 100.0
        ratio = self.average_gain[instrument] / self.average_loss[instrument]
        return 100.0 - 100.0 / (1.0 + ratio)

    def on_bar(self, bar):
        instrument = bar.instrument_id
        close = float(bar.close)
        value = self.rsi(instrument, close)
        self.previous[instrument] = close
        if value is None:
            return
        held = self.quantity.get(instrument, 0)
        if value < self.oversold and held == 0:
            size = int(self.position_value / close / self.lot) * self.lot
            if size > 0:
                self.buy(size, instrument_id=instrument)
                self.quantity[instrument] = size
        elif value > self.overbought and held > 0:
            self.sell(held, instrument_id=instrument)
            self.quantity[instrument] = 0
'''

MOMENTUM = '''from qtrade import Strategy


class MomentumRank(Strategy):
    """横截面动量：每 N 个交易日买入过去涨幅最高的前 K 只，先卖后买。"""

    def on_start(self):
        self.lookback = int(self.parameters.get("lookback", 60))
        self.top_n = int(self.parameters.get("top_n", 5))
        self.rebalance_every = int(self.parameters.get("rebalance_every", 20))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.lookback < 2 or self.top_n < 1 or self.rebalance_every < 1:
            raise ValueError("invalid momentum parameters")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.closes = {}
        self.held = {}
        self.quantity = {}
        self.days = -1
        self.last_day = None
        self.decision_day = None
        self.next_rebalance = self.lookback
        self.sell_queue = {}
        self.buy_queue = {}

    def score(self, closes):
        if len(closes) <= self.lookback:
            return None
        return closes[-1] / closes[-self.lookback - 1] - 1.0

    def decide(self):
        scores = {}
        for instrument, closes in self.closes.items():
            value = self.score(closes)
            if value is not None:
                scores[instrument] = value
        chosen = []
        if len(scores) >= self.top_n:
            chosen = sorted(scores, key=lambda name: (-scores[name], name))[: self.top_n]
        self.decision_day = self.days
        self.next_rebalance = self.days + self.rebalance_every
        self.sell_queue = {
            instrument: self.quantity.get(instrument, 0)
            for instrument in self.held
            if self.held.get(instrument) and instrument not in chosen
        }
        self.buy_queue = {
            instrument: self.position_value
            for instrument in chosen
            if not self.held.get(instrument)
        }

    def on_bar(self, bar):
        instrument = bar.instrument_id
        day = bar.timestamp.date()
        if day != self.last_day:
            self.last_day = day
            self.days += 1
            if self.days >= self.next_rebalance:
                self.decide()
        if self.decision_day is not None:
            age = self.days - self.decision_day
            if age == 1 and instrument in self.sell_queue:
                quantity = self.sell_queue.pop(instrument)
                if quantity > 0:
                    self.sell(quantity, instrument_id=instrument)
                    self.held[instrument] = False
                    self.quantity[instrument] = 0
            if age == 2 and not self.sell_queue and instrument in self.buy_queue:
                closes = self.closes.get(instrument, [])
                if closes:
                    quantity = int(self.buy_queue[instrument] / closes[-1] / self.lot) * self.lot
                    if quantity > 0:
                        self.buy(quantity, instrument_id=instrument)
                        self.held[instrument] = True
                        self.quantity[instrument] = quantity
                self.buy_queue.pop(instrument)
        self.closes.setdefault(instrument, []).append(float(bar.close))
'''

LOW_VOL = '''from qtrade import Strategy


class LowVolatility(Strategy):
    """横截面低波：每 N 个交易日买入过去波动率最低的前 K 只，先卖后买。"""

    def on_start(self):
        self.lookback = int(self.parameters.get("lookback", 60))
        self.top_n = int(self.parameters.get("top_n", 5))
        self.rebalance_every = int(self.parameters.get("rebalance_every", 20))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.lookback < 2 or self.top_n < 1 or self.rebalance_every < 1:
            raise ValueError("invalid low volatility parameters")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.closes = {}
        self.returns = {}
        self.held = {}
        self.quantity = {}
        self.days = -1
        self.last_day = None
        self.decision_day = None
        self.next_rebalance = self.lookback
        self.sell_queue = {}
        self.buy_queue = {}

    def score(self, series):
        if len(series) < self.lookback:
            return None
        window = series[-self.lookback:]
        mean = sum(window) / self.lookback
        variance = sum((value - mean) ** 2 for value in window) / self.lookback
        return variance ** 0.5

    def decide(self):
        scores = {}
        for instrument, series in self.returns.items():
            value = self.score(series)
            if value is not None:
                scores[instrument] = value
        chosen = []
        if len(scores) >= self.top_n:
            chosen = sorted(scores, key=lambda name: (scores[name], name))[: self.top_n]
        self.decision_day = self.days
        self.next_rebalance = self.days + self.rebalance_every
        self.sell_queue = {
            instrument: self.quantity.get(instrument, 0)
            for instrument in self.held
            if self.held.get(instrument) and instrument not in chosen
        }
        self.buy_queue = {
            instrument: self.position_value
            for instrument in chosen
            if not self.held.get(instrument)
        }

    def on_bar(self, bar):
        instrument = bar.instrument_id
        day = bar.timestamp.date()
        if day != self.last_day:
            self.last_day = day
            self.days += 1
            if self.days >= self.next_rebalance:
                self.decide()
        if self.decision_day is not None:
            age = self.days - self.decision_day
            if age == 1 and instrument in self.sell_queue:
                quantity = self.sell_queue.pop(instrument)
                if quantity > 0:
                    self.sell(quantity, instrument_id=instrument)
                    self.held[instrument] = False
                    self.quantity[instrument] = 0
            if age == 2 and not self.sell_queue and instrument in self.buy_queue:
                closes = self.closes.get(instrument, [])
                if closes:
                    quantity = int(self.buy_queue[instrument] / closes[-1] / self.lot) * self.lot
                    if quantity > 0:
                        self.buy(quantity, instrument_id=instrument)
                        self.held[instrument] = True
                        self.quantity[instrument] = quantity
                self.buy_queue.pop(instrument)
        close = float(bar.close)
        closes = self.closes.setdefault(instrument, [])
        if closes:
            self.returns.setdefault(instrument, []).append(close / closes[-1] - 1.0)
        closes.append(close)
'''

EQUAL = '''from qtrade import Strategy


class EqualWeight(Strategy):
    """等权基准：在每个标的的第一根 bar 买入相同金额并持有，用作对照。"""

    def on_start(self):
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.entered = set()

    def on_bar(self, bar):
        instrument = bar.instrument_id
        if instrument in self.entered:
            return
        self.entered.add(instrument)
        close = float(bar.close)
        if close <= 0:
            return
        quantity = int(self.position_value / close / self.lot) * self.lot
        if quantity > 0:
            self.buy(quantity, instrument_id=instrument)
'''

TS_MOMENTUM = '''from qtrade import Strategy


class TimeSeriesMomentum(Strategy):
    """时序动量：标的自身回看期收益转正后持有，跌破退出阈值离场。"""

    def on_start(self):
        self.lookback = int(self.parameters.get("lookback", 60))
        self.entry = float(self.parameters.get("entry_threshold", 0.03))
        self.exit = float(self.parameters.get("exit_threshold", -0.03))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.lookback < 2 or self.entry <= self.exit:
            raise ValueError("invalid momentum thresholds")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.closes = {}
        self.quantity = {}

    def on_bar(self, bar):
        instrument = bar.instrument_id
        closes = self.closes.setdefault(instrument, [])
        closes.append(float(bar.close))
        if len(closes) <= self.lookback:
            return
        momentum = closes[-1] / closes[-self.lookback - 1] - 1.0
        held = instrument in self.quantity and self.quantity[instrument] > 0
        if not held and momentum >= self.entry:
            quantity = int(self.position_value / closes[-1] / self.lot) * self.lot
            if quantity > 0:
                self.buy(quantity, instrument_id=instrument)
                self.quantity[instrument] = quantity
        elif held and momentum <= self.exit:
            self.sell(self.quantity[instrument], instrument_id=instrument)
            self.quantity[instrument] = 0
'''

DUAL_MOMENTUM = '''from qtrade import Strategy


class DualMomentum(Strategy):
    """双动量：持有横截面动量前列，且要求绝对动量为正，否则保持空仓。"""

    def on_start(self):
        self.lookback = int(self.parameters.get("lookback", 120))
        self.top_n = int(self.parameters.get("top_n", 3))
        self.rebalance_every = int(self.parameters.get("rebalance_every", 20))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.lookback < 2 or self.top_n < 1 or self.rebalance_every < 1:
            raise ValueError("invalid dual momentum parameters")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.closes = {}
        self.held = {}
        self.quantity = {}
        self.days = -1
        self.last_day = None
        self.decision_day = None
        self.next_rebalance = self.lookback
        self.sell_queue = {}
        self.buy_queue = {}

    def score(self, closes):
        if len(closes) <= self.lookback:
            return None
        return closes[-1] / closes[-self.lookback - 1] - 1.0

    def decide(self):
        scores = {}
        for instrument, closes in self.closes.items():
            value = self.score(closes)
            if value is not None and value > 0:
                scores[instrument] = value
        chosen = []
        if len(scores) >= self.top_n:
            chosen = sorted(scores, key=lambda name: (-scores[name], name))[: self.top_n]
        self.decision_day = self.days
        self.next_rebalance = self.days + self.rebalance_every
        self.sell_queue = {
            instrument: self.quantity.get(instrument, 0)
            for instrument in self.held
            if self.held.get(instrument) and instrument not in chosen
        }
        self.buy_queue = {
            instrument: self.position_value
            for instrument in chosen
            if not self.held.get(instrument)
        }

    def on_bar(self, bar):
        instrument = bar.instrument_id
        day = bar.timestamp.date()
        if day != self.last_day:
            self.last_day = day
            self.days += 1
            if self.days >= self.next_rebalance:
                self.decide()
        if self.decision_day is not None:
            age = self.days - self.decision_day
            if age == 1 and instrument in self.sell_queue:
                quantity = self.sell_queue.pop(instrument)
                if quantity > 0:
                    self.sell(quantity, instrument_id=instrument)
                    self.held[instrument] = False
                    self.quantity[instrument] = 0
            if age == 2 and not self.sell_queue and instrument in self.buy_queue:
                closes = self.closes.get(instrument, [])
                if closes:
                    quantity = int(self.buy_queue[instrument] / closes[-1] / self.lot) * self.lot
                    if quantity > 0:
                        self.buy(quantity, instrument_id=instrument)
                        self.held[instrument] = True
                        self.quantity[instrument] = quantity
                self.buy_queue.pop(instrument)
        self.closes.setdefault(instrument, []).append(float(bar.close))
'''

DONCHIAN = '''from qtrade import Strategy


class DonchianBreakout(Strategy):
    """唐奇安通道：突破前 N 日最高价买入，跌破前 M 日最低价离场。"""

    def on_start(self):
        self.entry_window = int(self.parameters.get("entry_window", 55))
        self.exit_window = int(self.parameters.get("exit_window", 20))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.entry_window < 2 or self.exit_window < 2:
            raise ValueError("channel windows must be at least 2")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.highs = {}
        self.lows = {}
        self.closes = {}
        self.quantity = {}

    def on_bar(self, bar):
        instrument = bar.instrument_id
        highs = self.highs.setdefault(instrument, [])
        lows = self.lows.setdefault(instrument, [])
        closes = self.closes.setdefault(instrument, [])
        high = float(bar.high)
        low = float(bar.low)
        close = float(bar.close)
        held = instrument in self.quantity and self.quantity[instrument] > 0
        if len(highs) >= self.entry_window:
            upper = max(highs[-self.entry_window:])
            lower = min(lows[-self.exit_window:]) if len(lows) >= self.exit_window else None
            if not held and close > upper:
                quantity = int(self.position_value / close / self.lot) * self.lot
                if quantity > 0:
                    self.buy(quantity, instrument_id=instrument)
                    self.quantity[instrument] = quantity
            elif held and lower is not None and close < lower:
                self.sell(self.quantity[instrument], instrument_id=instrument)
                self.quantity[instrument] = 0
        highs.append(high)
        lows.append(low)
        closes.append(close)
'''

MACD = '''from qtrade import Strategy


class MacdTrend(Strategy):
    """MACD 趋势：快慢 EMA 差上穿信号线买入，下穿卖出。"""

    def on_start(self):
        self.fast = int(self.parameters.get("fast", 12))
        self.slow = int(self.parameters.get("slow", 26))
        self.signal = int(self.parameters.get("signal", 9))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.fast < 2 or self.fast >= self.slow or self.signal < 2:
            raise ValueError("invalid macd windows")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.fast_ema = {}
        self.slow_ema = {}
        self.signal_ema = {}
        self.diff = {}
        self.samples = {}
        self.quantity = {}

    def on_bar(self, bar):
        instrument = bar.instrument_id
        close = float(bar.close)
        if instrument not in self.samples:
            self.fast_ema[instrument] = close
            self.slow_ema[instrument] = close
            self.signal_ema[instrument] = 0.0
            self.diff[instrument] = 0.0
            self.samples[instrument] = 0
            return
        self.fast_ema[instrument] += (
            close - self.fast_ema[instrument]
        ) * 2.0 / (self.fast + 1.0)
        self.slow_ema[instrument] += (
            close - self.slow_ema[instrument]
        ) * 2.0 / (self.slow + 1.0)
        momentum = self.fast_ema[instrument] - self.slow_ema[instrument]
        self.signal_ema[instrument] += (
            momentum - self.signal_ema[instrument]
        ) * 2.0 / (self.signal + 1.0)
        previous = self.diff[instrument]
        self.samples[instrument] += 1
        if self.samples[instrument] < self.slow:
            self.diff[instrument] = momentum - self.signal_ema[instrument]
            return
        current = momentum - self.signal_ema[instrument]
        held = self.quantity.get(instrument, 0)
        if previous <= 0 and current > 0 and held == 0:
            size = int(self.position_value / close / self.lot) * self.lot
            if size > 0:
                self.buy(size, instrument_id=instrument)
                self.quantity[instrument] = size
        elif previous >= 0 and current < 0 and held > 0:
            self.sell(held, instrument_id=instrument)
            self.quantity[instrument] = 0
        self.diff[instrument] = current
'''

BOLLINGER = '''from qtrade import Strategy


class BollingerReversion(Strategy):
    """布林带均值回归：跌破下轨买入，回到中轨卖出。"""

    def on_start(self):
        self.window = int(self.parameters.get("window", 20))
        self.deviations = float(self.parameters.get("deviations", 2.0))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.window < 3 or self.deviations <= 0:
            raise ValueError("invalid bollinger parameters")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.closes = {}
        self.quantity = {}

    def on_bar(self, bar):
        instrument = bar.instrument_id
        closes = self.closes.setdefault(instrument, [])
        close = float(bar.close)
        closes.append(close)
        if len(closes) < self.window:
            return
        window = closes[-self.window:]
        mean = sum(window) / self.window
        variance = sum((value - mean) ** 2 for value in window) / self.window
        deviation = variance ** 0.5
        holding = self.quantity.get(instrument, 0)
        if holding == 0 and close < mean - self.deviations * deviation:
            size = int(self.position_value / close / self.lot) * self.lot
            if size > 0:
                self.buy(size, instrument_id=instrument)
                self.quantity[instrument] = size
        elif holding > 0 and close >= mean:
            self.sell(holding, instrument_id=instrument)
            self.quantity[instrument] = 0
'''

RSI_TREND = '''from qtrade import Strategy


class RsiTrendFilter(Strategy):
    """RSI 加趋势过滤：长期均线之上才在超卖时买入，跌破均线或超买离场。"""

    def on_start(self):
        self.rsi_period = int(self.parameters.get("rsi_period", 14))
        self.trend_window = int(self.parameters.get("trend_window", 120))
        self.oversold = float(self.parameters.get("oversold", 35))
        self.exit_level = float(self.parameters.get("exit_level", 65))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.rsi_period < 2 or self.trend_window < 3:
            raise ValueError("invalid rsi or trend window")
        if self.oversold >= self.exit_level:
            raise ValueError("oversold must be below exit level")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.closes = {}
        self.average_gain = {}
        self.average_loss = {}
        self.previous = {}
        self.samples = {}
        self.quantity = {}

    def rsi(self, instrument, close):
        previous = self.previous.get(instrument)
        if previous is None:
            self.previous[instrument] = close
            self.average_gain[instrument] = 0.0
            self.average_loss[instrument] = 0.0
            self.samples[instrument] = 0
            return None
        change = close - previous
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        self.samples[instrument] += 1
        count = self.samples[instrument]
        window = self.rsi_period if count > self.rsi_period else count
        self.average_gain[instrument] = (
            self.average_gain[instrument] * (window - 1) + gain
        ) / window
        self.average_loss[instrument] = (
            self.average_loss[instrument] * (window - 1) + loss
        ) / window
        if count < self.rsi_period:
            return None
        if self.average_loss[instrument] == 0:
            return 100.0
        ratio = self.average_gain[instrument] / self.average_loss[instrument]
        return 100.0 - 100.0 / (1.0 + ratio)

    def on_bar(self, bar):
        instrument = bar.instrument_id
        close = float(bar.close)
        closes = self.closes.setdefault(instrument, [])
        closes.append(close)
        value = self.rsi(instrument, close)
        self.previous[instrument] = close
        if value is None or len(closes) < self.trend_window:
            return
        trend = sum(closes[-self.trend_window:]) / self.trend_window
        holding = self.quantity.get(instrument, 0)
        if holding == 0 and close > trend and value < self.oversold:
            size = int(self.position_value / close / self.lot) * self.lot
            if size > 0:
                self.buy(size, instrument_id=instrument)
                self.quantity[instrument] = size
        elif holding > 0 and (close < trend or value > self.exit_level):
            self.sell(holding, instrument_id=instrument)
            self.quantity[instrument] = 0
'''

CHANDELIER = '''from qtrade import Strategy


class AtrChandelierExit(Strategy):
    """吊灯止损：均线金叉入场，用 ATR 倍数的移动止损离场。"""

    def on_start(self):
        self.trend_window = int(self.parameters.get("trend_window", 50))
        self.atr_window = int(self.parameters.get("atr_window", 14))
        self.atr_multiple = float(self.parameters.get("atr_multiple", 3.0))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.trend_window < 3 or self.atr_window < 2 or self.atr_multiple <= 0:
            raise ValueError("invalid chandelier parameters")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.closes = {}
        self.highs = {}
        self.lows = {}
        self.previous_close = {}
        self.atr = {}
        self.atr_samples = {}
        self.peak = {}
        self.quantity = {}

    def update_atr(self, instrument, high, low, close):
        previous = self.previous_close.get(instrument)
        if previous is None:
            self.atr[instrument] = high - low
            self.atr_samples[instrument] = 1
            return
        true_range = max(high - low, abs(high - previous), abs(low - previous))
        count = self.atr_samples.get(instrument, 0) + 1
        self.atr_samples[instrument] = count
        window = self.atr_window if count > self.atr_window else count
        self.atr[instrument] = (self.atr[instrument] * (window - 1) + true_range) / window

    def on_bar(self, bar):
        instrument = bar.instrument_id
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)
        closes = self.closes.setdefault(instrument, [])
        self.update_atr(instrument, high, low, close)
        closes.append(close)
        self.previous_close[instrument] = close
        if len(closes) < self.trend_window + 1:
            return
        trend = sum(closes[-self.trend_window:]) / self.trend_window
        trend_previous = sum(closes[-self.trend_window - 1:-1]) / self.trend_window
        holding = self.quantity.get(instrument, 0)
        if holding == 0 and closes[-2] <= trend_previous and close > trend:
            size = int(self.position_value / close / self.lot) * self.lot
            if size > 0:
                self.buy(size, instrument_id=instrument)
                self.quantity[instrument] = size
                self.peak[instrument] = close
        elif holding > 0:
            if close > self.peak.get(instrument, close):
                self.peak[instrument] = close
            stop = self.peak[instrument] - self.atr_multiple * self.atr[instrument]
            if close < stop:
                self.sell(holding, instrument_id=instrument)
                self.quantity[instrument] = 0
                self.peak[instrument] = close
'''

REVERSAL = '''from qtrade import Strategy


class CrossSectionalReversal(Strategy):
    """横截面短期反转：买入近期跌幅最大的前 K 只，持有若干交易日后换仓。"""

    def on_start(self):
        self.lookback = int(self.parameters.get("lookback", 5))
        self.top_n = int(self.parameters.get("top_n", 3))
        self.rebalance_every = int(self.parameters.get("rebalance_every", 5))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.lookback < 2 or self.top_n < 1 or self.rebalance_every < 1:
            raise ValueError("invalid reversal parameters")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.closes = {}
        self.held = {}
        self.quantity = {}
        self.days = -1
        self.last_day = None
        self.decision_day = None
        self.next_rebalance = self.lookback
        self.sell_queue = {}
        self.buy_queue = {}

    def score(self, closes):
        if len(closes) <= self.lookback:
            return None
        return closes[-1] / closes[-self.lookback - 1] - 1.0

    def decide(self):
        scores = {}
        for instrument, closes in self.closes.items():
            value = self.score(closes)
            if value is not None:
                scores[instrument] = value
        chosen = []
        if len(scores) >= self.top_n:
            chosen = sorted(scores, key=lambda name: (scores[name], name))[: self.top_n]
        self.decision_day = self.days
        self.next_rebalance = self.days + self.rebalance_every
        self.sell_queue = {
            instrument: self.quantity.get(instrument, 0)
            for instrument in self.held
            if self.held.get(instrument) and instrument not in chosen
        }
        self.buy_queue = {
            instrument: self.position_value
            for instrument in chosen
            if not self.held.get(instrument)
        }

    def on_bar(self, bar):
        instrument = bar.instrument_id
        day = bar.timestamp.date()
        if day != self.last_day:
            self.last_day = day
            self.days += 1
            if self.days >= self.next_rebalance:
                self.decide()
        if self.decision_day is not None:
            age = self.days - self.decision_day
            if age == 1 and instrument in self.sell_queue:
                quantity = self.sell_queue.pop(instrument)
                if quantity > 0:
                    self.sell(quantity, instrument_id=instrument)
                    self.held[instrument] = False
                    self.quantity[instrument] = 0
            if age == 2 and not self.sell_queue and instrument in self.buy_queue:
                closes = self.closes.get(instrument, [])
                if closes:
                    quantity = int(self.buy_queue[instrument] / closes[-1] / self.lot) * self.lot
                    if quantity > 0:
                        self.buy(quantity, instrument_id=instrument)
                        self.held[instrument] = True
                        self.quantity[instrument] = quantity
                self.buy_queue.pop(instrument)
        self.closes.setdefault(instrument, []).append(float(bar.close))
'''

HIGH_WINDOW = '''from qtrade import Strategy


class HighWindowMomentum(Strategy):
    """区间新高动量：接近回看期最高价且站上长期均线时持有，回落或跌破均线离场。"""

    def on_start(self):
        self.high_window = int(self.parameters.get("high_window", 120))
        self.proximity = float(self.parameters.get("proximity", 0.95))
        self.trend_window = int(self.parameters.get("trend_window", 60))
        self.trailing_drawdown = float(self.parameters.get("trailing_drawdown", 0.1))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.high_window < 5 or self.trend_window < 3:
            raise ValueError("windows must be at least 5 and 3")
        if self.proximity <= 0 or self.proximity > 1:
            raise ValueError("proximity must be inside (0, 1]")
        if self.trailing_drawdown <= 0 or self.trailing_drawdown >= 1:
            raise ValueError("trailing drawdown must be inside (0, 1)")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.closes = {}
        self.quantity = {}

    def on_bar(self, bar):
        instrument = bar.instrument_id
        closes = self.closes.setdefault(instrument, [])
        close = float(bar.close)
        closes.append(close)
        if len(closes) < self.high_window or len(closes) < self.trend_window:
            return
        recent_high = max(closes[-self.high_window:])
        trend = sum(closes[-self.trend_window:]) / self.trend_window
        holding = self.quantity.get(instrument, 0)
        if holding == 0:
            near_high = close >= self.proximity * recent_high
            if near_high and close > trend:
                size = int(self.position_value / close / self.lot) * self.lot
                if size > 0:
                    self.buy(size, instrument_id=instrument)
                    self.quantity[instrument] = size
        elif close < trend or close <= (1.0 - self.trailing_drawdown) * recent_high:
            self.sell(holding, instrument_id=instrument)
            self.quantity[instrument] = 0
'''

VOL_TARGET = '''from qtrade import Strategy


class VolTargetMomentum(Strategy):
    """波动率目标动量：按风险调整动量选股，并用目标波动率缩放单只仓位。"""

    def on_start(self):
        self.lookback = int(self.parameters.get("lookback", 60))
        self.top_n = int(self.parameters.get("top_n", 3))
        self.rebalance_every = int(self.parameters.get("rebalance_every", 20))
        self.target_vol = float(self.parameters.get("target_vol", 0.2))
        self.base_value = float(self.parameters.get("base_value", 150000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.lookback < 5 or self.top_n < 1 or self.rebalance_every < 1:
            raise ValueError("invalid volatility targeting parameters")
        if self.target_vol <= 0 or self.base_value <= 0 or self.lot < 1:
            raise ValueError("target volatility, base value and lot must be positive")
        self.closes = {}
        self.returns = {}
        self.held = {}
        self.quantity = {}
        self.days = -1
        self.last_day = None
        self.decision_day = None
        self.next_rebalance = self.lookback
        self.sell_queue = {}
        self.buy_queue = {}

    def statistics(self, instrument):
        closes = self.closes.get(instrument, [])
        series = self.returns.get(instrument, [])
        if len(closes) <= self.lookback or len(series) < self.lookback:
            return None
        window = series[-self.lookback:]
        mean = sum(window) / self.lookback
        variance = sum((value - mean) ** 2 for value in window) / self.lookback
        volatility = (variance ** 0.5) * (252.0 ** 0.5)
        if volatility <= 0:
            return None
        momentum = closes[-1] / closes[-self.lookback - 1] - 1.0
        return momentum / volatility, volatility

    def decide(self):
        scores = {}
        volatilities = {}
        for instrument in self.closes:
            stats = self.statistics(instrument)
            if stats is not None:
                scores[instrument] = stats[0]
                volatilities[instrument] = stats[1]
        chosen = []
        if len(scores) >= self.top_n:
            chosen = sorted(scores, key=lambda name: (-scores[name], name))[: self.top_n]
        self.decision_day = self.days
        self.next_rebalance = self.days + self.rebalance_every
        self.sell_queue = {
            instrument: self.quantity.get(instrument, 0)
            for instrument in self.held
            if self.held.get(instrument) and instrument not in chosen
        }
        self.buy_queue = {}
        for instrument in chosen:
            if self.held.get(instrument):
                continue
            scale = self.target_vol / volatilities[instrument]
            if scale > 2.0:
                scale = 2.0
            if scale < 0.2:
                scale = 0.2
            self.buy_queue[instrument] = self.base_value * scale

    def on_bar(self, bar):
        instrument = bar.instrument_id
        day = bar.timestamp.date()
        if day != self.last_day:
            self.last_day = day
            self.days += 1
            if self.days >= self.next_rebalance:
                self.decide()
        if self.decision_day is not None:
            age = self.days - self.decision_day
            if age == 1 and instrument in self.sell_queue:
                quantity = self.sell_queue.pop(instrument)
                if quantity > 0:
                    self.sell(quantity, instrument_id=instrument)
                    self.held[instrument] = False
                    self.quantity[instrument] = 0
            if age == 2 and not self.sell_queue and instrument in self.buy_queue:
                closes = self.closes.get(instrument, [])
                if closes:
                    value = self.buy_queue[instrument]
                    quantity = int(value / closes[-1] / self.lot) * self.lot
                    if quantity > 0:
                        self.buy(quantity, instrument_id=instrument)
                        self.held[instrument] = True
                        self.quantity[instrument] = quantity
                self.buy_queue.pop(instrument)
        close = float(bar.close)
        closes = self.closes.setdefault(instrument, [])
        if closes:
            self.returns.setdefault(instrument, []).append(close / closes[-1] - 1.0)
        closes.append(close)
'''


DRAGON = '''from qtrade import Strategy


class DragonPullback(Strategy):
    """龙回头：前期强势股缩量回踩均线时低吸，反弹、破位或超时离场。"""

    def on_start(self):
        self.run_window = int(self.parameters.get("run_window", 20))
        self.run_gain = float(self.parameters.get("run_gain", 0.25))
        self.pullback_min = float(self.parameters.get("pullback_min", 0.03))
        self.pullback_max = float(self.parameters.get("pullback_max", 0.12))
        self.volume_ratio = float(self.parameters.get("volume_ratio", 0.8))
        self.support_window = int(self.parameters.get("support_window", 10))
        self.take_profit = float(self.parameters.get("take_profit", 0.08))
        self.max_hold = int(self.parameters.get("max_hold", 10))
        self.position_value = float(self.parameters.get("position_value", 50000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.run_window < 5 or self.support_window < 3:
            raise ValueError("windows must be at least 5 and 3")
        if self.run_gain <= 0 or self.pullback_min <= 0:
            raise ValueError("run gain and pullback must be positive")
        if self.pullback_min >= self.pullback_max:
            raise ValueError("pullback minimum must be below maximum")
        if self.volume_ratio <= 0 or self.take_profit <= 0 or self.max_hold < 1:
            raise ValueError("invalid volume or exit settings")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.closes = {}
        self.highs = {}
        self.volumes = {}
        self.quantity = {}
        self.entry = {}
        self.held_days = {}

    def on_bar(self, bar):
        instrument = bar.instrument_id
        closes = self.closes.setdefault(instrument, [])
        highs = self.highs.setdefault(instrument, [])
        volumes = self.volumes.setdefault(instrument, [])
        close = float(bar.close)
        high = float(bar.high)
        volume = float(bar.volume)
        held = self.quantity.get(instrument, 0)
        if held > 0:
            self.held_days[instrument] = self.held_days.get(instrument, 0) + 1
            if len(closes) >= self.support_window:
                support = sum(closes[-self.support_window:]) / self.support_window
            else:
                support = close
            target = self.entry.get(instrument, close) * (1.0 + self.take_profit)
            if close < support or close >= target:
                self.sell(held, instrument_id=instrument)
                self.quantity[instrument] = 0
                self.entry.pop(instrument, None)
                self.held_days[instrument] = 0
            elif self.held_days[instrument] >= self.max_hold:
                self.sell(held, instrument_id=instrument)
                self.quantity[instrument] = 0
                self.entry.pop(instrument, None)
                self.held_days[instrument] = 0
        elif len(closes) > self.run_window and len(volumes) > 5:
            run_low = min(closes[-self.run_window - 1:-1])
            run_high = max(highs[-self.run_window:])
            gain = closes[-1] / run_low - 1.0
            pullback = close / run_high - 1.0 if run_high > 0 else 0.0
            average_volume = sum(volumes[-6:-1]) / 5.0
            shrunk = average_volume > 0 and volume <= self.volume_ratio * average_volume
            support = sum(closes[-self.support_window:]) / self.support_window
            if (
                gain >= self.run_gain
                and -self.pullback_max <= pullback <= -self.pullback_min
                and shrunk
                and close >= support
            ):
                size = int(self.position_value / close / self.lot) * self.lot
                if size > 0:
                    self.buy(size, instrument_id=instrument)
                    self.quantity[instrument] = size
                    self.entry[instrument] = close
                    self.held_days[instrument] = 0
        closes.append(close)
        highs.append(high)
        volumes.append(volume)
'''

VOLUME_SURGE = '''from qtrade import Strategy


class VolumePriceSurge(Strategy):
    """量价齐升：放量突破近期高点且均线向上时跟进，跌破短均线离场。"""

    def on_start(self):
        self.trend_window = int(self.parameters.get("trend_window", 20))
        self.breakout_window = int(self.parameters.get("breakout_window", 10))
        self.volume_window = int(self.parameters.get("volume_window", 20))
        self.volume_ratio = float(self.parameters.get("volume_ratio", 1.8))
        self.exit_window = int(self.parameters.get("exit_window", 10))
        self.max_hold = int(self.parameters.get("max_hold", 20))
        self.position_value = float(self.parameters.get("position_value", 50000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.trend_window < 3 or self.breakout_window < 2 or self.volume_window < 2:
            raise ValueError("windows must be at least 3, 2 and 2")
        if self.volume_ratio <= 0 or self.exit_window < 2 or self.max_hold < 1:
            raise ValueError("invalid volume or exit settings")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.closes = {}
        self.volumes = {}
        self.quantity = {}
        self.held_days = {}

    def on_bar(self, bar):
        instrument = bar.instrument_id
        closes = self.closes.setdefault(instrument, [])
        volumes = self.volumes.setdefault(instrument, [])
        close = float(bar.close)
        volume = float(bar.volume)
        held = self.quantity.get(instrument, 0)
        if held > 0:
            self.held_days[instrument] = self.held_days.get(instrument, 0) + 1
            if len(closes) >= self.exit_window:
                exit_line = sum(closes[-self.exit_window:]) / self.exit_window
            else:
                exit_line = close
            if close < exit_line or self.held_days[instrument] >= self.max_hold:
                self.sell(held, instrument_id=instrument)
                self.quantity[instrument] = 0
                self.held_days[instrument] = 0
        elif len(closes) >= self.trend_window + 1:
            trend = sum(closes[-self.trend_window:]) / self.trend_window
            trend_previous = sum(closes[-self.trend_window - 1:-1]) / self.trend_window
            prior_high = max(closes[-self.breakout_window - 1:-1])
            average_volume = sum(
                volumes[-self.volume_window - 1:-1]
            ) / self.volume_window
            if (
                close > trend
                and trend > trend_previous
                and close > prior_high
                and average_volume > 0
                and volume >= self.volume_ratio * average_volume
            ):
                size = int(self.position_value / close / self.lot) * self.lot
                if size > 0:
                    self.buy(size, instrument_id=instrument)
                    self.quantity[instrument] = size
                    self.held_days[instrument] = 0
        closes.append(close)
        volumes.append(volume)
'''

SQUEEZE = '''from qtrade import Strategy


class MovingAverageSqueeze(Strategy):
    """均线粘合突破：短中长均线收窄后放量突破前高入场，跌破慢线离场。"""

    def on_start(self):
        self.fast = int(self.parameters.get("fast", 5))
        self.mid = int(self.parameters.get("mid", 10))
        self.slow = int(self.parameters.get("slow", 20))
        self.squeeze_ratio = float(self.parameters.get("squeeze_ratio", 0.02))
        self.squeeze_days = int(self.parameters.get("squeeze_days", 3))
        self.breakout_window = int(self.parameters.get("breakout_window", 20))
        self.position_value = float(self.parameters.get("position_value", 50000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.fast < 2 or self.fast >= self.mid or self.mid >= self.slow:
            raise ValueError("moving average windows must increase")
        if self.squeeze_ratio <= 0 or self.squeeze_days < 1:
            raise ValueError("invalid squeeze settings")
        if self.breakout_window < 5:
            raise ValueError("breakout window must be at least 5")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.closes = {}
        self.squeeze_count = {}
        self.quantity = {}

    def on_bar(self, bar):
        instrument = bar.instrument_id
        closes = self.closes.setdefault(instrument, [])
        closes.append(float(bar.close))
        if len(closes) < self.slow:
            return
        close = closes[-1]
        fast_line = sum(closes[-self.fast:]) / self.fast
        mid_line = sum(closes[-self.mid:]) / self.mid
        slow_line = sum(closes[-self.slow:]) / self.slow
        low_line = min(fast_line, mid_line, slow_line)
        high_line = max(fast_line, mid_line, slow_line)
        band = (high_line - low_line) / slow_line if slow_line > 0 else 1.0
        if band <= self.squeeze_ratio:
            self.squeeze_count[instrument] = self.squeeze_count.get(instrument, 0) + 1
        else:
            self.squeeze_count[instrument] = 0
        held = self.quantity.get(instrument, 0)
        if held > 0:
            if close < slow_line:
                self.sell(held, instrument_id=instrument)
                self.quantity[instrument] = 0
            return
        if len(closes) <= self.breakout_window:
            return
        prior_high = max(closes[-self.breakout_window - 1:-1])
        if self.squeeze_count.get(instrument, 0) >= self.squeeze_days and close > prior_high:
            size = int(self.position_value / close / self.lot) * self.lot
            if size > 0:
                self.buy(size, instrument_id=instrument)
                self.quantity[instrument] = size
'''

GAP = '''from qtrade import Strategy


class GapHold(Strategy):
    """跳空不补：放量跳空高开且收阳后跟随，回补缺口或触及止损离场。"""

    def on_start(self):
        self.gap_min = float(self.parameters.get("gap_min", 0.02))
        self.volume_ratio = float(self.parameters.get("volume_ratio", 1.3))
        self.volume_window = int(self.parameters.get("volume_window", 10))
        self.stop_ratio = float(self.parameters.get("stop_ratio", 0.06))
        self.take_profit = float(self.parameters.get("take_profit", 0.15))
        self.max_hold = int(self.parameters.get("max_hold", 10))
        self.position_value = float(self.parameters.get("position_value", 50000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.gap_min <= 0 or self.volume_ratio <= 0 or self.volume_window < 2:
            raise ValueError("invalid gap or volume settings")
        if self.stop_ratio <= 0 or self.stop_ratio >= 1:
            raise ValueError("stop ratio must be inside (0, 1)")
        if self.take_profit <= 0 or self.max_hold < 1:
            raise ValueError("invalid profit target or holding limit")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.previous = {}
        self.volumes = {}
        self.quantity = {}
        self.entry = {}
        self.gap_floor = {}
        self.held_days = {}

    def on_bar(self, bar):
        instrument = bar.instrument_id
        volumes = self.volumes.setdefault(instrument, [])
        close = float(bar.close)
        open_price = float(bar.open)
        volume = float(bar.volume)
        previous = self.previous.get(instrument)
        held = self.quantity.get(instrument, 0)
        if held > 0:
            self.held_days[instrument] = self.held_days.get(instrument, 0) + 1
            entry = self.entry.get(instrument, close)
            floor = self.gap_floor.get(instrument, close)
            if (
                close < floor
                or close <= entry * (1.0 - self.stop_ratio)
                or close >= entry * (1.0 + self.take_profit)
                or self.held_days[instrument] >= self.max_hold
            ):
                self.sell(held, instrument_id=instrument)
                self.quantity[instrument] = 0
                self.entry.pop(instrument, None)
                self.gap_floor.pop(instrument, None)
                self.held_days[instrument] = 0
        elif previous is not None and len(volumes) >= self.volume_window:
            average_volume = sum(volumes[-self.volume_window:]) / self.volume_window
            gapped = previous > 0 and open_price >= previous * (1.0 + self.gap_min)
            if (
                gapped
                and close > open_price
                and average_volume > 0
                and volume >= self.volume_ratio * average_volume
            ):
                size = int(self.position_value / close / self.lot) * self.lot
                if size > 0:
                    self.buy(size, instrument_id=instrument)
                    self.quantity[instrument] = size
                    self.entry[instrument] = close
                    self.gap_floor[instrument] = previous
                    self.held_days[instrument] = 0
        volumes.append(volume)
        self.previous[instrument] = close
'''

GRID = '''from qtrade import Strategy


class GridReversion(Strategy):
    """网格低吸：每跌一格买入、每涨一格卖出一份，适合宽幅震荡的标的。"""

    def on_start(self):
        self.grid_step = float(self.parameters.get("grid_step", 0.05))
        self.max_levels = int(self.parameters.get("max_levels", 3))
        self.position_value = float(self.parameters.get("position_value", 15000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.grid_step <= 0 or self.grid_step >= 0.5:
            raise ValueError("grid step must be inside (0, 0.5)")
        if self.max_levels < 1 or self.max_levels > 20:
            raise ValueError("max levels must be between 1 and 20")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.reference = {}
        self.levels = {}
        self.quantity = {}

    def on_bar(self, bar):
        instrument = bar.instrument_id
        close = float(bar.close)
        reference = self.reference.get(instrument)
        if reference is None:
            self.reference[instrument] = close
            self.levels[instrument] = []
            self.quantity[instrument] = 0
            return
        levels = self.levels[instrument]
        size = int(self.position_value / close / self.lot) * self.lot
        if (
            len(levels) < self.max_levels
            and size > 0
            and close <= reference * (1.0 - self.grid_step)
        ):
            self.buy(size, instrument_id=instrument)
            levels.append(size)
            self.quantity[instrument] = self.quantity.get(instrument, 0) + size
            self.reference[instrument] = close
        elif levels and close >= reference * (1.0 + self.grid_step):
            sold = levels.pop()
            self.sell(sold, instrument_id=instrument)
            self.quantity[instrument] = self.quantity.get(instrument, 0) - sold
            self.reference[instrument] = close
'''

KDJ = '''from qtrade import Strategy


class KdjCross(Strategy):
    """KDJ 金叉：低位金叉买入、高位死叉卖出，论坛最常见的短线择时规则之一。"""

    def on_start(self):
        self.period = int(self.parameters.get("period", 9))
        self.k_smooth = int(self.parameters.get("k_smooth", 3))
        self.d_smooth = int(self.parameters.get("d_smooth", 3))
        self.oversold = float(self.parameters.get("oversold", 30))
        self.overbought = float(self.parameters.get("overbought", 70))
        self.position_value = float(self.parameters.get("position_value", 50000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.period < 2 or self.k_smooth < 1 or self.d_smooth < 1:
            raise ValueError("invalid kdj windows")
        if self.oversold <= 0 or self.overbought >= 100:
            raise ValueError("thresholds must be inside 0-100")
        if self.oversold >= self.overbought:
            raise ValueError("oversold must be below overbought")
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("position value and lot must be positive")
        self.highs = {}
        self.lows = {}
        self.closes = {}
        self.k = {}
        self.d = {}
        self.quantity = {}

    def on_bar(self, bar):
        instrument = bar.instrument_id
        highs = self.highs.setdefault(instrument, [])
        lows = self.lows.setdefault(instrument, [])
        closes = self.closes.setdefault(instrument, [])
        close = float(bar.close)
        highs.append(float(bar.high))
        lows.append(float(bar.low))
        closes.append(close)
        if len(closes) < self.period:
            return
        lowest = min(lows[-self.period:])
        highest = max(highs[-self.period:])
        if highest == lowest:
            rsv = 50.0
        else:
            rsv = (close - lowest) / (highest - lowest) * 100.0
        previous_k = self.k.get(instrument, 50.0)
        previous_d = self.d.get(instrument, 50.0)
        current_k = previous_k + (rsv - previous_k) / self.k_smooth
        current_d = previous_d + (current_k - previous_d) / self.d_smooth
        self.k[instrument] = current_k
        self.d[instrument] = current_d
        held = self.quantity.get(instrument, 0)
        if (
            held == 0
            and previous_k <= previous_d
            and current_k > current_d
            and current_k < self.oversold
        ):
            size = int(self.position_value / close / self.lot) * self.lot
            if size > 0:
                self.buy(size, instrument_id=instrument)
                self.quantity[instrument] = size
        elif (
            held > 0
            and previous_k >= previous_d
            and current_k < current_d
            and current_k > self.overbought
        ):
            self.sell(held, instrument_id=instrument)
            self.quantity[instrument] = 0
'''


def schema(properties: dict[str, dict[str, object]]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": [],
        "additionalProperties": False,
    }


def integer(title, default, minimum, maximum):
    return {
        "type": "integer",
        "title": title,
        "default": default,
        "minimum": minimum,
        "maximum": maximum,
    }


def number(title, default, minimum, maximum):
    return {
        "type": "number",
        "title": title,
        "default": default,
        "minimum": minimum,
        "maximum": maximum,
    }


def build_templates() -> list[dict[str, object]]:
    return [
        {
            "id": "sma-cross",
            "name": "双均线交叉",
            "category": "趋势",
            "summary": "快线上穿慢线买入、下穿卖出，逐标的独立维护持仓状态。",
            "tags": ["趋势", "均线", "入门"],
            "default_name": "双均线交叉",
            "default_description": "SMA(5,20) 交叉，每只标的独立计算，适合先跑通完整回测流程。",
            "code": SMA,
            "schema": schema(
                {
                    "fast": integer("快线周期", 5, 2, 60),
                    "slow": integer("慢线周期", 20, 3, 250),
                    "position_value": number("单只目标金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "ts-momentum",
            "name": "时序动量",
            "category": "趋势",
            "summary": "标的自身回看期收益转正后持有、转负离场，趋势跟随的经典形式。",
            "tags": ["动量", "趋势", "单标的"],
            "default_name": "时序动量",
            "default_description": "60 日时序动量，收益高于 3% 入场、低于 -3% 离场。",
            "code": TS_MOMENTUM,
            "schema": schema(
                {
                    "lookback": integer("动量回看天数", 60, 2, 250),
                    "entry_threshold": number("入场阈值", 0.03, -1, 1),
                    "exit_threshold": number("离场阈值", -0.03, -1, 1),
                    "position_value": number("单只目标金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "donchian-breakout",
            "name": "唐奇安通道突破",
            "category": "趋势",
            "summary": "突破前 N 日最高价入场、跌破前 M 日最低价离场，海龟交易法的核心规则。",
            "tags": ["突破", "趋势", "海龟"],
            "default_name": "唐奇安通道突破",
            "default_description": "55 日高点突破入场，20 日低点跌破离场。",
            "code": DONCHIAN,
            "schema": schema(
                {
                    "entry_window": integer("入场通道天数", 55, 2, 250),
                    "exit_window": integer("离场通道天数", 20, 2, 250),
                    "position_value": number("单只目标金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "macd-trend",
            "name": "MACD 趋势",
            "category": "趋势",
            "summary": "快慢 EMA 差与信号线交叉决定买卖，广泛使用的趋势跟踪指标。",
            "tags": ["MACD", "趋势", "指标"],
            "default_name": "MACD 趋势",
            "default_description": "MACD(12,26,9) 金叉买入、死叉卖出。",
            "code": MACD,
            "schema": schema(
                {
                    "fast": integer("快线 EMA", 12, 2, 60),
                    "slow": integer("慢线 EMA", 26, 3, 250),
                    "signal": integer("信号线 EMA", 9, 2, 60),
                    "position_value": number("单只目标金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "atr-chandelier",
            "name": "ATR 吊灯止损",
            "category": "趋势",
            "summary": "均线转多入场，用 ATR 倍数的移动止损锁定利润，控制趋势策略回撤。",
            "tags": ["ATR", "止损", "趋势"],
            "default_name": "ATR 吊灯止损",
            "default_description": "50 日均线入场，3 倍 ATR 吊灯止损离场。",
            "code": CHANDELIER,
            "schema": schema(
                {
                    "trend_window": integer("趋势均线天数", 50, 3, 250),
                    "atr_window": integer("ATR 窗口", 14, 2, 60),
                    "atr_multiple": number("ATR 倍数", 3.0, 0.5, 10),
                    "position_value": number("单只目标金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "high-window-momentum",
            "name": "区间新高动量",
            "category": "趋势",
            "summary": "接近回看期最高价且站上长期均线时持有，是 52 周新高效应的日线实现。",
            "tags": ["新高", "动量", "趋势"],
            "default_name": "区间新高动量",
            "default_description": "120 日高点 95% 以内且站上 60 日均线时买入。",
            "code": HIGH_WINDOW,
            "schema": schema(
                {
                    "high_window": integer("高点回看天数", 120, 5, 240),
                    "proximity": number("接近高点比例", 0.95, 0.5, 1),
                    "trend_window": integer("趋势均线天数", 60, 3, 250),
                    "trailing_drawdown": number("回落离场比例", 0.1, 0.02, 0.5),
                    "position_value": number("单只目标金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "rsi-reversal",
            "name": "RSI 均值回归",
            "category": "反转",
            "summary": "RSI 跌破超卖线买入、升破超买线卖出，逐标的独立计算。",
            "tags": ["反转", "震荡", "指标"],
            "default_name": "RSI 均值回归",
            "default_description": "RSI(14) 超卖买入、超买卖出的均值回归示例。",
            "code": RSI,
            "schema": schema(
                {
                    "period": integer("RSI 周期", 14, 2, 60),
                    "oversold": number("超卖阈值", 30, 1, 50),
                    "overbought": number("超买阈值", 70, 50, 99),
                    "position_value": number("单只目标金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "bollinger-reversion",
            "name": "布林带均值回归",
            "category": "反转",
            "summary": "跌破下轨买入、回到中轨卖出，用波动率自适应定义超跌区间。",
            "tags": ["布林带", "反转", "波动率"],
            "default_name": "布林带均值回归",
            "default_description": "20 日布林带 2 倍标准差，跌破下轨买入、回到中轨卖出。",
            "code": BOLLINGER,
            "schema": schema(
                {
                    "window": integer("均线窗口", 20, 3, 120),
                    "deviations": number("标准差倍数", 2.0, 0.5, 5),
                    "position_value": number("单只目标金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "rsi-trend-filter",
            "name": "RSI 趋势过滤",
            "category": "反转",
            "summary": "只在长期均线之上做超卖买入，避免下跌趋势中的逆势接刀。",
            "tags": ["RSI", "反转", "过滤"],
            "default_name": "RSI 趋势过滤",
            "default_description": "120 日均线之上、RSI(14) 低于 40 买入，超买或跌破均线离场。",
            "code": RSI_TREND,
            "schema": schema(
                {
                    "rsi_period": integer("RSI 周期", 14, 2, 60),
                    "trend_window": integer("趋势均线天数", 120, 3, 250),
                    "oversold": number("超卖阈值", 40, 5, 60),
                    "exit_level": number("离场阈值", 65, 40, 95),
                    "position_value": number("单只目标金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "momentum-rank",
            "name": "横截面动量",
            "category": "横截面",
            "summary": "按过去涨幅排序，持有前 N 名，先卖出再买入，适合多标的日线数据集。",
            "tags": ["动量", "横截面", "选股"],
            "default_name": "横截面动量",
            "default_description": "60 日动量排序，每 20 个交易日调仓，持有前 5 名。",
            "code": MOMENTUM,
            "schema": schema(
                {
                    "lookback": integer("动量回看天数", 60, 2, 250),
                    "top_n": integer("持仓数量", 5, 1, 50),
                    "rebalance_every": integer("调仓间隔（交易日）", 20, 1, 250),
                    "position_value": number("单只目标金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "dual-momentum",
            "name": "双动量轮动",
            "category": "横截面",
            "summary": "只持有绝对动量为正的横截面前列，市场整体走弱时自动空仓。",
            "tags": ["双动量", "轮动", "绝对动量"],
            "default_name": "双动量轮动",
            "default_description": "120 日动量绝对值为正且排名前 3 时持有，每 20 日调仓。",
            "code": DUAL_MOMENTUM,
            "schema": schema(
                {
                    "lookback": integer("动量回看天数", 120, 2, 250),
                    "top_n": integer("持仓数量", 3, 1, 50),
                    "rebalance_every": integer("调仓间隔（交易日）", 20, 1, 250),
                    "position_value": number("单只目标金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "vol-target-momentum",
            "name": "波动率目标动量",
            "category": "横截面",
            "summary": "用风险调整动量选股，并按目标波动率缩放仓位，波动越高仓位越小。",
            "tags": ["波动率目标", "动量", "风险"],
            "default_name": "波动率目标动量",
            "default_description": "60 日风险调整动量前 3 名，按 20% 目标波动率缩放仓位。",
            "code": VOL_TARGET,
            "schema": schema(
                {
                    "lookback": integer("回看天数", 60, 5, 250),
                    "top_n": integer("持仓数量", 3, 1, 50),
                    "rebalance_every": integer("调仓间隔（交易日）", 20, 1, 250),
                    "target_vol": number("目标年化波动率", 0.2, 0.02, 1),
                    "base_value": number("基准单只金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "low-volatility",
            "name": "横截面低波",
            "category": "横截面",
            "summary": "按过去波动率排序，持有波动最低的前 N 名，适合防守型配置。",
            "tags": ["低波", "横截面", "选股"],
            "default_name": "横截面低波",
            "default_description": "60 日波动率排序，每 20 个交易日调仓，持有最低的 5 只。",
            "code": LOW_VOL,
            "schema": schema(
                {
                    "lookback": integer("波动率窗口（交易日）", 60, 2, 250),
                    "top_n": integer("持仓数量", 5, 1, 50),
                    "rebalance_every": integer("调仓间隔（交易日）", 20, 1, 250),
                    "position_value": number("单只目标金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "cross-sectional-reversal",
            "name": "横截面短期反转",
            "category": "横截面",
            "summary": "买入近期跌幅最大的前 N 只并短期持有，对应经典的短期反转效应。",
            "tags": ["反转", "横截面", "短期"],
            "default_name": "横截面短期反转",
            "default_description": "5 日跌幅最大的 3 只，每 5 个交易日换仓。",
            "code": REVERSAL,
            "schema": schema(
                {
                    "lookback": integer("反转回看天数", 5, 2, 60),
                    "top_n": integer("持仓数量", 3, 1, 50),
                    "rebalance_every": integer("调仓间隔（交易日）", 5, 1, 60),
                    "position_value": number("单只目标金额", 150000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "dragon-pullback",
            "name": "龙回头",
            "category": "扫地僧",
            "summary": "前期强势股缩量回踩均线时低吸，反弹或破位离场，论坛流传最广的短线战法之一。",
            "tags": ["实战", "回调", "缩量"],
            "default_name": "龙回头",
            "default_description": "20 日内涨幅超过 25% 后，缩量回踩 3%–12% 且不破 10 日线时买入。",
            "code": DRAGON,
            "schema": schema(
                {
                    "run_window": integer("强势区间天数", 20, 5, 120),
                    "run_gain": number("强势涨幅门槛", 0.25, 0.05, 1),
                    "pullback_min": number("最小回调幅度", 0.03, 0.01, 0.2),
                    "pullback_max": number("最大回调幅度", 0.12, 0.05, 0.4),
                    "volume_ratio": number("缩量比例", 0.8, 0.1, 1.5),
                    "support_window": integer("支撑均线天数", 10, 3, 60),
                    "take_profit": number("止盈比例", 0.08, 0.01, 0.5),
                    "max_hold": integer("最长持有天数", 10, 1, 60),
                    "position_value": number("单只目标金额", 50000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "volume-price-surge",
            "name": "量价齐升",
            "category": "扫地僧",
            "summary": "放量突破近期高点且均线向上时跟进，跌破短均线离场，量价配合的经典实战规则。",
            "tags": ["实战", "放量", "突破"],
            "default_name": "量价齐升",
            "default_description": "成交量达到 20 日均量 1.8 倍并突破 10 日高点时买入。",
            "code": VOLUME_SURGE,
            "schema": schema(
                {
                    "trend_window": integer("趋势均线天数", 20, 3, 120),
                    "breakout_window": integer("突破回看天数", 10, 2, 120),
                    "volume_window": integer("均量窗口", 20, 2, 120),
                    "volume_ratio": number("放量倍数", 1.8, 1, 10),
                    "exit_window": integer("离场均线天数", 10, 2, 60),
                    "max_hold": integer("最长持有天数", 20, 1, 120),
                    "position_value": number("单只目标金额", 50000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "ma-squeeze-breakout",
            "name": "均线粘合突破",
            "category": "扫地僧",
            "summary": "短中长均线收窄代表分歧收敛，随后放量突破前高视为方向选择，跌破慢线离场。",
            "tags": ["实战", "粘合", "突破"],
            "default_name": "均线粘合突破",
            "default_description": "5/10/20 日均线带宽低于 2% 持续 3 天后，突破 20 日高点买入。",
            "code": SQUEEZE,
            "schema": schema(
                {
                    "fast": integer("快线天数", 5, 2, 30),
                    "mid": integer("中线天数", 10, 3, 60),
                    "slow": integer("慢线天数", 20, 5, 120),
                    "squeeze_ratio": number("粘合带宽比例", 0.02, 0.005, 0.1),
                    "squeeze_days": integer("粘合持续天数", 3, 1, 20),
                    "breakout_window": integer("突破回看天数", 20, 5, 120),
                    "position_value": number("单只目标金额", 50000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "gap-hold",
            "name": "跳空不补",
            "category": "扫地僧",
            "summary": "放量跳空高开且收阳说明资金抢筹，缺口未回补前持有，回补或止损离场。",
            "tags": ["实战", "缺口", "动量"],
            "default_name": "跳空不补",
            "default_description": "高开 2% 且放量收阳时买入，回补缺口或持有 10 日离场。",
            "code": GAP,
            "schema": schema(
                {
                    "gap_min": number("最小跳空幅度", 0.02, 0.005, 0.2),
                    "volume_ratio": number("放量倍数", 1.3, 1, 10),
                    "volume_window": integer("均量窗口", 10, 2, 60),
                    "stop_ratio": number("止损比例", 0.06, 0.01, 0.3),
                    "take_profit": number("止盈比例", 0.15, 0.02, 1),
                    "max_hold": integer("最长持有天数", 10, 1, 60),
                    "position_value": number("单只目标金额", 50000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "grid-reversion",
            "name": "网格低吸",
            "category": "扫地僧",
            "summary": "每跌一格买入、每涨一格卖出一份，用纪律代替预测的震荡实战法。",
            "tags": ["实战", "网格", "震荡"],
            "default_name": "网格低吸",
            "default_description": "每下跌 5% 买入一份、每上涨 5% 卖出一份，最多持有 3 层。",
            "code": GRID,
            "schema": schema(
                {
                    "grid_step": number("网格间距", 0.05, 0.01, 0.2),
                    "max_levels": integer("最大层数", 3, 1, 20),
                    "position_value": number("每格金额", 15000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "kdj-cross",
            "name": "KDJ 金叉",
            "category": "扫地僧",
            "summary": "低位金叉买入、高位死叉卖出，A 股论坛最常见的短线择时指标，换手较高。",
            "tags": ["实战", "KDJ", "择时"],
            "default_name": "KDJ 金叉",
            "default_description": "KDJ(9,3,3) 在 30 以下金叉买入、70 以上死叉卖出。",
            "code": KDJ,
            "schema": schema(
                {
                    "period": integer("RSV 周期", 9, 2, 60),
                    "k_smooth": integer("K 平滑", 3, 1, 10),
                    "d_smooth": integer("D 平滑", 3, 1, 10),
                    "oversold": number("超卖阈值", 30, 5, 50),
                    "overbought": number("超买阈值", 70, 50, 95),
                    "position_value": number("单只目标金额", 50000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
        {
            "id": "equal-weight",
            "name": "等权基准",
            "category": "基准",
            "summary": "每个标的买入相同金额并持有，用于和主动策略做横向对照。",
            "tags": ["基准", "被动", "横截面"],
            "default_name": "等权基准",
            "default_description": "多标的等权买入并持有；单只金额需按标的数量摊分初始资金。",
            "code": EQUAL,
            "schema": schema(
                {
                    "position_value": number("单只目标金额", 50000, 1000, 100000000),
                    "lot_size": integer("整手股数", 100, 1, 1000),
                }
            ),
        },
    ]


def render() -> str:
    return json.dumps(build_templates(), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    content = render()
    if arguments.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != content:
            print(f"{TARGET} is stale; run scripts/generate-strategy-templates.py")
            return 1
        print(f"{TARGET} is current")
        return 0
    TARGET.write_text(content, encoding="utf-8")
    print(f"wrote {TARGET} ({len(build_templates())} templates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
