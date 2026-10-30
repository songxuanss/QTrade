# 本机认证部署恢复验收

本轮完成OpenSpec `complete-professional-data-workbench` 1.2。固定来源、证券、日期、类别和完整验收用例沿用 [固定范围](../data/COLLECTION-ACCEPTANCE-SCOPE.md)，不修改目标范围。早期访问配置故障及真实验收见 [访问修复](COLLECTION-ACCESS-2026-10-12.md)；此次补齐当前部署的容器地址变化恢复及旧数据保留证据。全Goal仍未完成。

## 当前部署与故障

工作树基准为布局提交 `b141edb`。实际入口 `http://127.0.0.1:13000/workspace/data`；Docker Web镜像 `sha256:2c2fb5b099b8dde9dba3c093719e19a2275a1a797fed6877ed01a4792bec86eb`，独立collector镜像 `sha256:30f867300eed2285b2348cec1348febe5a61fafdfe7ab121a109d49208922ca6`。

13000 Web经3122原生认证API访问既有QTrade业务PG、市场库、MinIO、Valkey；3123原生业务Worker通过18100独立collector公开接口导入标准行情。3121为既有真实sibling SSO。独立collector拥有自身PG、归档卷和采集后台进程。Docker主API18000和容器Worker不是本次认证链路，未启动它们制造重复消费者。Web与collector镜像没有因本次运维脚本变动重建。

此前依赖容器恢复时bridge IP变化，原生API/Worker仍保存旧连接，临时重启脚本依赖存活进程的环境；进程退出后可能丢失运行配置。新增项目内resume/restart和私有启动快照，使这一已发生的故障具有可重复恢复入口。

## 实现及使用

```bash
.venv/bin/python scripts/verification-local.py resume
# 显式重启已核验拥有的SSO/API/业务Worker：
.venv/bin/python scripts/verification-local.py restart
```

resume保留正常进程，仅恢复退出或连接变化的原生进程；启动既有但已停止的所需容器，不创建/删除数据库、对象桶、账号或镜像。发现地址通过Compose项目/服务/工作目录标签核验。保留数据库名、身份和会话密钥、业务配置及对象桶；不执行prepare/cleanup。仅用于此Linux本机loopback认证验证部署，不是通用生产编排器。

恢复模块只向CLI暴露一个recover接口；OS操作集中在内部LocalHost Adapter，受控测试通过同一接口。私有快照0600且Git忽略，原子写入/fsync先于停机。进程登记持久化前通过stdin闸门禁止执行应用，登记失败或控制器退出时EOF使子进程退出。PID/启动身份、命令、工作目录和独立进程组共同核验；外部端口或所有权冲突拒绝，不终止外部进程。文件锁防止并行恢复。就绪超时保留原进程登记，下一次观察同一身份；终止升级SIGKILL后也观察退出，不把信号已发送当作端口已释放。loopback就绪探测不走代理。

## 实际演练与业务结果

1. 首次接管和重复resume，SSO/API/业务Worker及collector-db/collector/Web六项均kept，未重复启动。
2. 保存浏览器登录会话，记录既有任务和两个视口5条记录；采集卷、发布、MinIO对象、身份配置及数据容器/挂载建立只读基线。
3. 仅将原有MinIO容器重新接入原Compose网络的另一空闲地址；容器和卷不重建，无业务数据删除。演练脚本有连接失败回接原端点并再次resume的恢复路径。本次地址变化成功，resume更新连接并替换三项原生进程；紧接着第二次resume六项全部kept。
4. 恢复前的同一浏览器会话仍通过API鉴权并保持工作区；桌面1440及手机390的旧任务、非空5条数据、刷新、无横向溢出、axe0和无浏览器脚本错误通过。
5. 独立访问脚本重新验证两视口主导航、直接访问、刷新；只将本次测试会话在真实DB设过期，API401→真实SSO重认证→数据页→新会话/API200通过。此项不是仅删除cookie模拟过期。
6. 以新身份完成601288.SH、2025-09-01至09-05日线全量下载。UI→API→真实Worker/MaiRui HTTP→RAW5/标准5→质量→发布→市场消费者visible；归档摘要、原始行/history回执、转换输入和质量版本独立核验。两个角色、桌面/手机、非法日期、READ写预览403、结果/图表刷新、243条年度记录100/100/43分页通过。来源回执为history/http_response，非fixture或仅queued。

全量保留校验首次失败，原因是把可更新的 `provider-cache/factor-catalog-current.v1.json` 混入不可变文件比较。依据FactorCatalogCache.save的原子替换行为，后续校验明确区分这一个当前目录快照，保留原始失败记录和其变化标志，并在运行collector内校验当前严格契约/内容摘要；其余文件仍逐个比较，不泛化跳过provider-cache目录。

| 恢复前内容 | 保留证据 |
| --- | --- |
| 2,049个不可变文件（总文件2,050，含1个可变目录快照） | 每个旧路径SHA-256不变；目录快照变化明确报告且契约有效 |
| 260个行情/因子发布、70个研究发布 | 每个旧作用域/发布身份及内容摘要不变 |
| 5,916个MinIO对象 | 每个旧key完整正文SHA-256不变，不以ETag代替 |
| 既有SSO身份/会话配置 | 私有值仅内存比较，摘要一致，原cookie实际可用 |
| 5个持久数据容器及挂载 | 原容器身份与挂载摘要一致 |

新下载追加后，采集文件2,059、行情/因子发布261、研究发布70、MinIO对象5,918；新版本只追加，旧引用不变。计数/身份和截图位于忽略的.tools/verification-local/review，凭证不进入报告或提交。

## 可重复命令与受控测试

```bash
.venv/bin/python -m pytest tests/test_verification_runtime.py
.venv/bin/python scripts/verification-runtime-evidence.py before
# 执行恢复后；before有已存基线时明确拒绝覆盖：
.venv/bin/python scripts/verification-runtime-evidence.py after
cd apps/web
node tests/verification-runtime-recovery-ui.mjs before
node tests/verification-runtime-recovery-ui.mjs after
QTRADE_ACCESS_REPORT=runtime-recovery-access node tests/verification-collector-access.mjs
QTRADE_COLLECTION_REPORT=runtime-recovery-download node tests/verification-collection-compact-ui.mjs --resume
cd ../..
QTRADE_COLLECTION_REPORT=runtime-recovery-download .venv/bin/python scripts/verification-collection-compact-archive.py
```

15项受控测试通过：首次/重复/退出恢复、地址变化、显式重启、环境文件丢失但快照可用、凭证/数据库/查询保持、并发锁、端口/所有权/非local拒绝、快照损坏、就绪超时原进程保留、登记失败闸门、真实OS闸门EOF及放行、SIGKILL退出观察。受控OS不冒充真实供应商验收。Ruff、mypy、定向ESLint/Prettier、OpenSpec严格校验和秘密扫描通过。

## 六主业务矩阵与边界

| 主业务 | 本轮实际验证 | 限制 |
| --- | --- | --- |
| 登录/权限 | 原会话跨恢复可用；真实SSO、工作区、DB过期重认证；管理员与READ拒绝 | 完整跨作用域/撤权矩阵仍由9.3跟踪 |
| 数据采集/市场消费 | 旧文件/对象/发布保留、新下载真实闭环、图表和刷新、两视口 | 不证明所有调度/门禁失败矩阵 |
| 策略版本 | 未改、未重测 | 不新增ready声明 |
| 回测/报告 | 未改、未重测 | 不证明真实引擎交付 |
| 告警 | 未改、未重测 | 不证明实时事件来源接通 |
| 连接交易控制 | 未改、未重测 | 未扩大实盘范围 |

此次是既有本机部署恢复，不替代9.5隔离数据库/对象/manifest一致备份恢复，不证明第二台物理机器部署，也不把原生业务Worker的恢复当作独立采集调度器重启补跑验收。1.2完成不关闭其余23项任务。
