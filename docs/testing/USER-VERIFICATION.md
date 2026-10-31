# 本地 UI 与功能验收

验收入口：<http://127.0.0.1:3120/workspace/strategies>。
请使用 `127.0.0.1`，不要替换为 `localhost`，以保持 OIDC 回调与 Cookie 的来源一致。

这是运行生产构建的本地验收环境：真实 sibling SSO、实际 API、独立 PostgreSQL 数据库和 MinIO 对象存储。实盘关闭。数据保留在重启之间；浏览器测试没有替换登录或策略服务。

登录邮箱为 `qtrade-integration@example.test`。随机密码保存在本机私有文件
`.tools/verification-local/LOGIN.txt`（0600 权限，Git 忽略）。第一次登录如看到授权页，点击“同意授权”。

## 验证场景：策略创建与不可变版本

1. 登录后，在“策略研究”点击“＋ 新建策略”，填写名称“我的均线验收”和用途说明。
2. 保留编辑器默认源码，将“参数 schema”替换为下方 JSON，点击“创建策略”。
3. 确认左侧策略库出现该策略，右侧版本记录出现 `v1`。
4. 将生成的 `lookback` 参数改成 `1`，点击“校验参数”：应提示取值范围不符合版本 schema。
5. 改为 `20` 再校验：应提示参数符合当前版本，底部出现“配置回测”入口。
6. 点击“保存为新版本”：应生成 `v2`，`v1` 仍在版本记录中。刷新页面，策略和两个版本仍保留。
7. 在侧栏打开“回测实验”“告警中心”“交易执行”“系统健康”，检查暗色主题、信息层级、空态和状态提示。缩窄窗口时，应能浏览页面而不出现整页水平溢出。

```json
{
  "type": "object",
  "properties": {
    "lookback": {
      "type": "integer",
      "minimum": 2,
      "maximum": 120,
      "default": 20
    }
  },
  "required": ["lookback"],
  "additionalProperties": false
}
```

这个场景验证真实保存、参数约束和版本持久化；不会提交订单。初始环境没有行情数据和回测结果，相关页面显示空态。A 股逐笔下载需要服务端有效 MaiRui Licence；实盘 broker 控制尚未接通。这些能力不作为本场景的验收前提。

## 启停与重复检查

已修复本机容器入口：<http://127.0.0.1:13000/workspace/data>。必须使用 `127.0.0.1`，不要替换为 `localhost`。
该 Web 容器通过本机认证 override 连接已有持久认证 API（3122）、SSO（3121）与独立 collector（18100），使用原有业务数据与用户身份。默认 Compose 的 18000 API 仍是另一个未连接该身份作用域的实例；它的健康检查不能代替这套完整链路。

启动既有 3121/3122/3123 服务后，用下列命令更新容器 Web（私有文件不会提交）：

```bash
.tools/sso-venv/bin/python scripts/configure-local-auth-deployment.py
docker compose --env-file .env --env-file .env.local-auth -f compose.yaml -f compose.local-auth.yaml up -d --no-deps --build --wait web
```

`compose.local-auth.yaml` 仅用于 Linux 本机 loopback；远程部署需使用可达 HTTPS 身份/API 地址及普通容器网络，不能照搬 host 网络。不要单独执行默认 Compose 更新 Web 并将它误认为认证部署已更新。访问故障与真实验证记录见 [采集 Goal 访问记录](COLLECTION-ACCESS-2026-10-12.md)。

独立验收环境使用已构建的 Next.js standalone 服务，依赖本地 Compose 的数据库和对象存储，以及 `.tools/sso-venv` 中的 sibling SSO 依赖：

```bash
cd /home/pauls/Code/QTrade/apps/web
npm run build
cd ../..
.venv/bin/python scripts/verification-local.py start
.venv/bin/python scripts/verification-local.py status
# 停止服务，但保留数据库和登录账号：
.venv/bin/python scripts/verification-local.py stop
```

进程持续运行于后台；机器重启后需重新 start。启动已在运行的环境前先 stop。启停脚本不会删除常规 QTrade 数据库。

启动脚本自动读取根目录 `.env`，可在其中配置 `MAIRUI_LICENCE`，无需每次 export。已有环境变量优先于文件值；修改文件后 stop/start 生效。`.env` 被 Git 忽略，请勿提交证书。

在启动后的环境重跑真实浏览器验收：

```bash
cd apps/web
node tests/verification-review.mjs
node tests/verification-download.mjs
node tests/verification-download-choices.mjs
```

脚本实际登录、创建策略、验证非法与合法参数、保存 v2 并刷新检查版本；另外检查全部 10 个主页面在 1440px 桌面及 390px 手机下的 axe 可访问性、页面溢出和浏览器异常。
截图与结果保存在 `.tools/verification-local/review/`。仅在完成 SSO 登录后截取 QTrade 页面；登录密码不写入截图或结果报告。每次执行会在独立验收数据库中创建一条新的验收策略，便于人工查看。

下载测试使用实际 MaiRui 来源和独立数据库的 Worker（3123），验证结束早于开始时间的明确拒绝、`600519.SH` 在 2026-09-28 UTC 01:30–07:00 的任务创建、完成、非空批次、校验摘要、刷新持久化及桌面/手机可访问性。此测试需要有效证书和外部来源可用；外部失败不能视为通过。输入时间使用 UTC，北京时间需减 8 小时。

重复下载测试先完成一个 15 分钟时段，再请求 30 分钟，验证提示不会创建任务、“仅下载缺失数据”只提交后 15 分钟、两段合并判定完整覆盖，以及显式“重新下载全部”保留旧记录并创建新任务。测试在验收数据库中选取尚无该时段记录的股票；重复使用前可采用新的隔离数据库。

风控验收需先在隔离 SSO 数据库准备第二个独立身份：

```bash
.tools/sso-venv/bin/python scripts/verification-approver.py
cd apps/web
node tests/verification-risk.mjs
```

浏览器脚本从两个真实登录会话操作：确认停止、刷新保留、申请恢复、自批拒绝、第二位风险管理员切换工作区并批准恢复；还检查只读页面与写入拒绝，以及桌面/手机可访问性。结束后安全开关恢复为未触发。账号凭据仅保存在被 Git 忽略的权限 0600 文件中。

告警实际 UI 验收：

```bash
cd apps/web
node tests/verification-alerts.mjs
cd ../..
.venv/bin/python scripts/verification-alert-persistence.py
```

此流程从浏览器创建规则、拒绝非法表达式、停用/启用生成新版本、提交手动事实评估并等待 Worker 事件，然后逐一点击认领、确认、静默、添加备注和关闭。刷新后核对全部五步历史；第二用户切换同一工作区验证只读页面和写入拒绝。数据库检查确认 Worker 的站内通知已送达并持久化。手动评估使用输入的事实，不表示实时行情已连接。测试规则结束后停用，保留历史供查看。

「数据管理」按“行情与因子 / 板块与股票池 / 财务与事件 / 自动更新 / 数据资产 / 运行监控 / 逐笔与兼容”分区；只有当前分区显示表单，切换不会清空其他分区草稿。新增下载显示“任务已提交，等待 Worker 执行”是接受状态，不代表 RAW 已落盘或发布完成，应到“数据资产”继续核对任务、批次、质量和图表。

行情与因子区的“标的范围”支持同时勾选多个行业/主题/指数股票池，并追加公司代码；预览前显示合并后的来源、成员数和去重后标的数，提交时冻结所选池成员。只有完全未选择池和公司时代码才会拒绝。已选股票池使用高亮行和“已选 N”提示；同名池额外显示版本、观察日期和短 snapshot id，便于区分。

日期统一按 `YYYY-MM-DD` 显示和输入，右侧日历按钮保留原生日期选择器；手机端开始/结束日期各占整行。直接输入 `20250901` 会在离开输入框时规范为 `2025-09-01`。工作区功能文本最小 11px、正文 13–14px，装饰性英文标签除外；完整设计评审与前后指标见 `docs/testing/DESIGN-QA-2026-10-31.md`。

市场终端的“已下载数据”直接汇总已发布数据集，按标的列出 1m/5m/15m/30m/1h/日线入口和冻结数据集版本，无需先加入自选或手工输入代码；财务与事件通过同一面板直达数据管理财务区。

切换工作区页面时先立即显示加载骨架，再替换为完整内容；不要用骨架出现与否判断数据是否已完成。选择长区间或大量标的后，“标的范围”下方会显示粗估采集分段数和拆分依据；普通分钟历史按最多 7 天一段，可点击“改用 Pro 分钟历史”显式切换为按日历年组织。粗估只用于提前判断调用规模，实际分段、覆盖和调用量以预览为准。导航与分段实测明细见 `docs/testing/PERF-2026-10-31.md`。

下载后，在「数据管理 → 数据资产 → 研究数据集」选择已完成的 `600519.SH` 下载，点击「生成 1 分钟研究数据」，再点击「查看历史行情」。实际验收样例为 2026-09-28 UTC 01:30–07:00，生成 238 根已观察分钟 K 线。没有成交的分钟不补齐；同一任务重复生成复用已有数据集。当前只有对应已生成的 1m 周期，切换其他周期应明确显示暂无数据。此功能不会把历史数据宣称为实时连接。

```bash
cd apps/web
node tests/verification-research-data.mjs
```

脚本实际操作生成按钮和行情链接，再验证周期切换、自选添加/移除与刷新、桌面/手机图表和可访问性，同时核对实际 API 返回的 Timescale 数据。

### Tick 下载时长配置

在项目根目录 `.env` 中设置 `QTRADE_TICK_DOWNLOAD_MAX_DAYS=0`，表示单次下载时长不限（默认值）。以后需要限制时改为正整数，例如 `QTRADE_TICK_DOWNLOAD_MAX_DAYS=30`。上限按起止时间之间的自然日时长计算，不是交易日数量；恰好等于上限可以提交。负数、空值和非整数会阻止服务启动，避免配置失效。

修改后重新启动 API；Docker 部署执行 `docker compose up -d`，本地验证部署执行 `.venv/bin/python scripts/verification-local.py stop` 后再执行 `.venv/bin/python scripts/verification-local.py start`。页面从服务端读取并显示当前上限。来源自身的历史起始日期和按日分页不受此配置改变。
