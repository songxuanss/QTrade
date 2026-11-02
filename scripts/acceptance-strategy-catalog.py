"""Real, parameterised strategy sources used by the portfolio acceptance run.

Every strategy is a single ``qtrade.Strategy`` class so it can execute in the
isolated Runner.  The five research strategies share one cross-sectional
cadence: rank the frozen pool from data available before the decision day,
sell dropped members first, then buy new members once sale proceeds settle.
The equal-weight strategy is the passive comparison baseline.

The shared cadence matters operationally: the batch harness feeds bars sorted
by ``(timestamp, instrument)``, so every instrument must be able to act on a
pending order during its own bar rather than only the first instrument of the
day.  Each day is still counted once, before any of that day's closes are
appended, which keeps the ranking strictly causal.
"""

CROSS_SECTIONAL_TEMPLATE = '''from qtrade import Strategy


class {class_name}(Strategy):
    """{docstring}"""

    def on_start(self):
{start_body}
        self.closes = {{}}
        self.holdings = {{}}
        self.quantity = {{}}
        self.days = -1
        self.last_day = None
        self.decision_day = None
        self.next_rebalance = {warmup}
        self.sell_queue = {{}}
        self.buy_queue = {{}}
        self.buy_delay = 1

    def rank(self, closes):
{rank_body}

    def on_bar(self, bar):
        iid = bar.instrument_id
        day = bar.timestamp.date()
        if day != self.last_day:
            self.last_day = day
            self.days += 1
            if self.days >= self.next_rebalance:
                self.decide()
        if self.decision_day is not None:
            age = self.days - self.decision_day
            if age >= 1 and iid in self.sell_queue:
                quantity = self.sell_queue.pop(iid)
                if quantity > 0:
                    self.order("sell", quantity, instrument_id=iid)
                    self.holdings[iid] = 0
                    self.quantity[iid] = 0
            if age >= self.buy_delay and iid in self.buy_queue:
                close = float(bar.close)
                quantity = int(self.buy_queue.pop(iid) / close / self.lot) * self.lot
                if quantity > 0:
                    self.order("buy", quantity, instrument_id=iid)
                    self.holdings[iid] = 1
                    self.quantity[iid] = quantity
        self.closes.setdefault(iid, []).append(float(bar.close))

    def decide(self):
        scores = {{}}
        for iid, closes in self.closes.items():
            if len(closes) >= {minimum_history}:
                score = self.rank(closes)
                if score is not None:
                    scores[iid] = score
        chosen = []
        if len(scores) >= self.top_n:
            chosen = sorted(scores, key=lambda name: (-scores[name], name))[: self.top_n]
        self.decision_day = self.days
        self.sell_queue = {{
            iid: self.quantity.get(iid, 0)
            for iid in self.holdings
            if self.holdings.get(iid, 0) and iid not in chosen
        }}
        self.buy_queue = {{
            iid: self.position_value for iid in chosen if not self.holdings.get(iid, 0)
        }}
        self.buy_delay = 2 if self.sell_queue else 1
        self.next_rebalance = self.days + self.rebalance_every
'''

_COMMON_START = """        self.top_n = int(self.parameters.get("top_n", 5))
        self.position_value = float(self.parameters.get("position_value", 150000))
        self.rebalance_every = int(self.parameters.get("rebalance_every", 20))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.top_n < 1 or self.rebalance_every < 1 or self.lot < 1:
            raise ValueError("invalid portfolio parameters")"""

MOMENTUM = CROSS_SECTIONAL_TEMPLATE.format(
    class_name="Momentum60",
    docstring="60-day total return, skipping the most recent 5 days.",
    start_body="""        self.lookback = int(self.parameters.get("lookback", 60))
        self.skip = int(self.parameters.get("skip", 5))
        if self.lookback < 10 or self.skip < 0:
            raise ValueError("invalid momentum parameters")
"""
    + _COMMON_START,
    warmup=66,
    minimum_history="self.lookback + self.skip + 1",
    rank_body="""        return (
            closes[-1 - self.skip] / closes[-1 - self.skip - self.lookback] - 1.0
        )""",
)

REVERSAL = CROSS_SECTIONAL_TEMPLATE.format(
    class_name="Reversal5",
    docstring="Buy the weakest five-day performers for a short-term bounce.",
    start_body="""        self.lookback = int(self.parameters.get("lookback", 5))
        if self.lookback < 2:
            raise ValueError("invalid reversal parameters")
"""
    + _COMMON_START,
    warmup=6,
    minimum_history="self.lookback + 1",
    rank_body="""        return -(closes[-1] / closes[-1 - self.lookback] - 1.0)""",
)

LOW_VOLATILITY = CROSS_SECTIONAL_TEMPLATE.format(
    class_name="LowVolatility60",
    docstring="Lowest realised daily-return volatility over a 60-day window.",
    start_body="""        self.lookback = int(self.parameters.get("lookback", 60))
        if self.lookback < 10:
            raise ValueError("invalid volatility parameters")
"""
    + _COMMON_START,
    warmup=61,
    minimum_history="self.lookback + 1",
    rank_body="""        returns = [
            closes[index] / closes[index - 1] - 1.0
            for index in range(len(closes) - self.lookback, len(closes))
        ]
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / len(returns)
        return -(variance ** 0.5)""",
)

TREND = CROSS_SECTIONAL_TEMPLATE.format(
    class_name="Trend20_60",
    docstring="Fast/slow moving-average trend, ranked by normalised MA spread.",
    start_body="""        self.fast = int(self.parameters.get("fast", 20))
        self.slow = int(self.parameters.get("slow", 60))
        if self.fast < 2 or self.slow <= self.fast:
            raise ValueError("invalid trend parameters")
"""
    + _COMMON_START,
    warmup=60,
    minimum_history="self.slow",
    rank_body="""        fast = sum(closes[-self.fast :]) / self.fast
        slow = sum(closes[-self.slow :]) / self.slow
        if fast <= slow:
            return None
        return fast / slow - 1.0""",
)

RISK_ADJUSTED = CROSS_SECTIONAL_TEMPLATE.format(
    class_name="RiskAdjustedMomentum60",
    docstring="60-day momentum divided by realised volatility.",
    start_body="""        self.lookback = int(self.parameters.get("lookback", 60))
        self.skip = int(self.parameters.get("skip", 5))
        if self.lookback < 10 or self.skip < 0:
            raise ValueError("invalid risk-adjusted parameters")
"""
    + _COMMON_START,
    warmup=66,
    minimum_history="self.lookback + self.skip + 1",
    rank_body="""        momentum = (
            closes[-1 - self.skip] / closes[-1 - self.skip - self.lookback] - 1.0
        )
        returns = [
            closes[index] / closes[index - 1] - 1.0
            for index in range(len(closes) - self.lookback, len(closes))
        ]
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / len(returns)
        volatility = variance ** 0.5
        if volatility <= 0:
            return None
        return momentum / volatility""",
)

EQUAL_WEIGHT = '''from qtrade import Strategy


class EqualWeight20(Strategy):
    """Buy every pool member once and hold to the end of the dataset."""

    def on_start(self):
        self.position_value = float(self.parameters.get("position_value", 45000))
        self.lot = int(self.parameters.get("lot_size", 100))
        if self.position_value <= 0 or self.lot < 1:
            raise ValueError("invalid equal weight parameters")
        self.bought = set()

    def on_bar(self, bar):
        iid = bar.instrument_id
        if iid in self.bought:
            return
        close = float(bar.close)
        quantity = int(self.position_value / close / self.lot) * self.lot
        if quantity > 0:
            self.bought.add(iid)
            self.order("buy", quantity, instrument_id=iid)
'''

_CROSS_SECTIONAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "lookback",
        "skip",
        "top_n",
        "position_value",
        "rebalance_every",
        "lot_size",
    ],
    "properties": {
        "lookback": {
            "type": "integer",
            "minimum": 5,
            "maximum": 200,
            "default": 60,
            "title": "回看交易日",
        },
        "skip": {
            "type": "integer",
            "minimum": 0,
            "maximum": 30,
            "default": 5,
            "title": "跳过最近交易日",
        },
        "top_n": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "default": 5,
            "title": "持仓数量",
        },
        "position_value": {
            "type": "number",
            "minimum": 1000,
            "maximum": 1000000,
            "default": 150000,
            "title": "单标的计划金额",
        },
        "rebalance_every": {
            "type": "integer",
            "minimum": 1,
            "maximum": 120,
            "default": 20,
            "title": "再平衡间隔（交易日）",
        },
        "lot_size": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1000,
            "default": 100,
            "title": "整手股数",
        },
    },
}

_REVERSAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lookback", "top_n", "position_value", "rebalance_every", "lot_size"],
    "properties": {
        "lookback": {
            "type": "integer",
            "minimum": 2,
            "maximum": 20,
            "default": 5,
            "title": "反转窗口",
        },
        "top_n": _CROSS_SECTIONAL_SCHEMA["properties"]["top_n"],
        "position_value": _CROSS_SECTIONAL_SCHEMA["properties"]["position_value"],
        "rebalance_every": _CROSS_SECTIONAL_SCHEMA["properties"]["rebalance_every"],
        "lot_size": _CROSS_SECTIONAL_SCHEMA["properties"]["lot_size"],
    },
}

_TREND_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fast", "slow", "top_n", "position_value", "rebalance_every", "lot_size"],
    "properties": {
        "fast": {"type": "integer", "minimum": 2, "maximum": 120, "default": 20},
        "slow": {"type": "integer", "minimum": 5, "maximum": 200, "default": 60},
        "top_n": _CROSS_SECTIONAL_SCHEMA["properties"]["top_n"],
        "position_value": _CROSS_SECTIONAL_SCHEMA["properties"]["position_value"],
        "rebalance_every": _CROSS_SECTIONAL_SCHEMA["properties"]["rebalance_every"],
        "lot_size": _CROSS_SECTIONAL_SCHEMA["properties"]["lot_size"],
    },
}

_BENCHMARK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["position_value", "lot_size"],
    "properties": {
        "position_value": {
            "type": "number",
            "minimum": 1000,
            "maximum": 1000000,
            "default": 45000,
            "title": "单标的计划金额",
        },
        "lot_size": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1000,
            "default": 100,
            "title": "整手股数",
        },
    },
}

STRATEGIES = (
    {
        "key": "momentum",
        "name": "中期动量 60-5",
        "description": "12-1 动量的短窗口版本：过去 60 个交易日收益，跳过最近 5 日。",
        "code": MOMENTUM,
        "parameter_schema": _CROSS_SECTIONAL_SCHEMA,
        "parameters": {
            "lookback": 60,
            "skip": 5,
            "top_n": 5,
            "position_value": 150000,
            "rebalance_every": 20,
            "lot_size": 100,
        },
    },
    {
        "key": "reversal",
        "name": "短期反转 5 日",
        "description": "买入过去 5 个交易日跌幅最大的 5 只，利用短期超跌反弹。",
        "code": REVERSAL,
        "parameter_schema": _REVERSAL_SCHEMA,
        "parameters": {
            "lookback": 5,
            "top_n": 5,
            "position_value": 150000,
            "rebalance_every": 20,
            "lot_size": 100,
        },
    },
    {
        "key": "low_volatility",
        "name": "低波动 60 日",
        "description": "选择过去 60 个交易日已实现波动率最低的 5 只。",
        "code": LOW_VOLATILITY,
        "parameter_schema": _CROSS_SECTIONAL_SCHEMA,
        "parameters": {
            "lookback": 60,
            "skip": 5,
            "top_n": 5,
            "position_value": 150000,
            "rebalance_every": 20,
            "lot_size": 100,
        },
    },
    {
        "key": "trend",
        "name": "趋势 20/60",
        "description": "20 日均线高于 60 日均线时，按均线乖离率选择前 5 只。",
        "code": TREND,
        "parameter_schema": _TREND_SCHEMA,
        "parameters": {
            "fast": 20,
            "slow": 60,
            "top_n": 5,
            "position_value": 150000,
            "rebalance_every": 20,
            "lot_size": 100,
        },
    },
    {
        "key": "risk_adjusted",
        "name": "风险调整动量 60-5",
        "description": "60 日动量除以同期日收益波动率，选择风险调整后最强的前 5 只。",
        "code": RISK_ADJUSTED,
        "parameter_schema": _CROSS_SECTIONAL_SCHEMA,
        "parameters": {
            "lookback": 60,
            "skip": 5,
            "top_n": 5,
            "position_value": 150000,
            "rebalance_every": 20,
            "lot_size": 100,
        },
    },
    {
        "key": "equal_weight",
        "name": "等权 20 只基准",
        "description": "一次买入股票池全部 20 只并持有，作为被动基准。",
        "code": EQUAL_WEIGHT,
        "parameter_schema": _BENCHMARK_SCHEMA,
        "parameters": {"position_value": 45000, "lot_size": 100},
    },
)
