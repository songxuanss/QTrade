# QTrade 低耦合架构设计

## 1. 设计目标

QTrade 的核心目标不是“尽快堆功能”，而是建立一个能长期扩展的专业交易平台。因此架构必须满足：

- 底层引擎、数据源、券商、数据库、通知渠道可替换。
- QTrade 的领域模型不污染 LEAN / Qlib / CCXT / ClickHouse 等外部概念。
- 回测、研究、执行、风控可以独立演进。
- 新增市场或新引擎不需要重写 UI、权限、审计、告警。
- 所有任务输入与输出可版本化、可复现、可审计。

## 2. 三面架构

```text
┌──────────────────────────────────────────────┐
│ Control Plane                                │
│ 用户、工作区、策略、审批、告警、风控、审计、任务编排 │
└──────────────────────────────────────────────┘
                    │
┌──────────────────────────────────────────────┐
│ Compute Plane                                │
│ 回测、因子计算、模型训练、参数扫描、报表生成         │
└──────────────────────────────────────────────┘
                    │
┌──────────────────────────────────────────────┐
│ Execution Plane                              │
│ 实盘 runtime、broker adapter、订单生命周期、对账 │
└──────────────────────────────────────────────┘
```

控制面必须长期稳定；计算面和执行面允许按任务替换实现。

## 3. Hexagonal / Ports & Adapters

QTrade 领域服务只依赖内部接口，不直接依赖外部项目。

```text
FastAPI / Domain services
    │
    ├── MarketDataPort
    │   ├── TimescaleAdapter
    │   ├── ClickHouseAdapter
    │   └── VendorIngestionAdapter
    │
    ├── TradingEnginePort
    │   ├── LeanAdapter
    │   ├── NautilusAdapter
    │   └── MockEngineAdapter
    │
    ├── BrokerPort
    │   ├── PaperBrokerAdapter
    │   ├── CCXTAdapter
    │   ├── IBKRAdapter
    │   └── DomesticBrokerAdapter
    │
    ├── ResearchPort
    │   ├── PolarsAdapter
    │   ├── QlibResearchAdapter
    │   └── MlflowCompatibleAdapter
    │
    ├── NotificationPort
    │   ├── InAppAdapter
    │   ├── EmailAdapter
    │   ├── WebhookAdapter
    │   └── TelegramAdapter
    │
    ├── ObjectStorePort
    │   ├── S3Adapter
    │   └── MinIOAdapter
    │
    └── ObservabilityPort
        ├── OpenTelemetryAdapter
        └── PrometheusAdapter
```

## 4. 核心接口边界

### 4.1 TradingEnginePort

用于统一 LEAN、NautilusTrader 或未来自研引擎。

```python
class TradingEnginePort(Protocol):
    def validate(self, spec: StrategySpec) -> ValidationResult: ...
    def submit_backtest(self, spec: BacktestSpec) -> JobHandle: ...
    def cancel(self, run_id: RunId) -> None: ...
    def collect_backtest(self, run_id: RunId) -> NormalizedBacktestResult: ...
    def start_runtime(self, spec: LiveRuntimeSpec) -> RuntimeHandle: ...
    def stop_runtime(self, runtime_id: RuntimeId) -> None: ...
    def get_runtime_state(self, runtime_id: RuntimeId) -> RuntimeState: ...
```

约束：

- API 层不 import LEAN / Nautilus。
- 引擎只作为独立 worker 或容器运行。
- 引擎输出必须转换为 QTrade 标准结果。
- 回测结果必须包含代码版本、数据版本、参数版本和引擎版本。

### 4.2 ResearchPort

用于指标、因子、模型训练和预测。

```python
class ResearchPort(Protocol):
    def build_dataset(self, spec: DatasetSpec) -> DatasetArtifact: ...
    def compute_features(self, spec: FeatureSpec) -> FeatureArtifact: ...
    def train_model(self, spec: ModelSpec) -> ModelArtifact: ...
    def predict(self, model: ModelArtifact, data: DatasetArtifact) -> ScoreArtifact: ...
    def evaluate(self, spec: EvaluationSpec) -> ResearchReport: ...
```

Qlib 若接入，只能作为 `QlibResearchAdapter`，不能成为 QTrade 核心。

### 4.3 BrokerPort

```python
class BrokerPort(Protocol):
    def get_accounts(self) -> list[BrokerAccountView]: ...
    def get_balances(self, account_id: AccountId) -> list[BalanceView]: ...
    def get_positions(self, account_id: AccountId) -> list[PositionView]: ...
    def place_order(self, command: OrderCommand) -> BrokerOrderAck: ...
    def cancel_order(self, command: CancelCommand) -> BrokerCancelAck: ...
    def stream_events(self) -> AsyncIterator[BrokerEvent]: ...
    def reconcile(self, account_id: AccountId) -> ReconciliationReport: ...
```

所有实盘订单必须先经过 QTrade `RiskEngine`，再进入 `BrokerPort`。

## 5. 领域模型隔离规则

### 禁止

```text
QTrade API -> 直接 import qlib
QTrade API -> 直接 import LEAN internal module
Strategy service -> 直接调用 CCXT
Domain model -> 直接依赖 ClickHouse SQL
UI -> 直接连接 broker
UI -> 理解某个引擎的私有结果格式
```

### 必须

```text
外部输入 -> Anti-Corruption Layer -> QTrade canonical model
QTrade canonical model -> Adapter -> 外部系统
```

这样可以保证：

- LEAN 更换为 Nautilus 不影响 UI。
- Qlib 升级不影响策略服务。
- CCXT 更换为券商原生 SDK 不影响风控。
- TimescaleDB 更换为 ClickHouse 不影响业务代码。

## 6. 标准数据契约

所有跨模块数据使用稳定契约：

| 契约 | 说明 |
| --- | --- |
| `Instrument` | 统一标的、交易所、精度、货币、合约规格 |
| `Bar` | 标准 OHLCVV：open/high/low/close/volume/value |
| `Trade` | 标准逐笔成交 |
| `BookSnapshot` | 标准订单簿快照 |
| `StrategySpec` | 策略代码、参数、依赖、运行时 |
| `BacktestSpec` | 策略、数据集、成本模型、初始资金、时间范围 |
| `NormalizedBacktestResult` | 权益、交易、指标、日志、图表数据 |
| `FeatureArtifact` | 因子或特征矩阵及元数据 |
| `ModelArtifact` | 模型文件、训练配置、评估结果 |
| `ScoreArtifact` | 模型输出分数及时间戳 |
| `OrderCommand` | 预交易风控后的标准下单命令 |
| `RiskDecision` | allow / deny / require_approval 及原因 |

## 7. 任务与产物版本化

每次研究或回测任务必须生成 manifest：

```json
{
  "run_id": "bt_123",
  "kind": "backtest",
  "strategy": {"repo": "qtrade", "commit": "..."},
  "dataset": {"id": "us_equity_1m_v3", "hash": "..."},
  "parameters": {"hash": "..."},
  "engine": {"name": "lean", "version": "..."},
  "environment": {"image": "...", "digest": "..."},
  "created_by": "sub:...",
  "created_at": "2026-10-01T00:00:00Z"
}
```

要求：

- artifact 不可变。
- manifest 与结果一起保存。
- 同一 manifest 重复运行必须得到一致结果，除非显式声明非确定性来源。

## 8. 模块部署边界

一期可以使用 Docker Compose，但进程边界先拆清楚：

```text
qtrade-web
qtrade-api
qtrade-worker
qtrade-realtime
qtrade-engine-runner-lean
qtrade-research-runner
qtrade-market-data-ingestor
postgres
timescale / clickhouse
valkey
object-store
```

这样后续可以平滑迁移 Kubernetes 或托管服务，不需要重写应用。

## 9. 扩展方式

新增市场的典型改动：

```text
Instrument provider
Market data adapter
Calendar / trading rules
Cost model
Broker adapter，如需要实盘
UI 市场配置
```

不应该改动：

- 登录与权限
- 策略生命周期
- 风控主链路
- 回测报告主模型
- 审计

新增引擎的典型改动：

```text
实现 TradingEnginePort
新增 runner 镜像
新增结果转换器
能力声明 capability descriptor
回归测试通过
```

不应该改动：

- API contract
- UI 主流程
- 策略管理
- 风控

