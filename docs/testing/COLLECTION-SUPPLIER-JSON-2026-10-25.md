# 来源JSON歧义拒绝：受控验证与环境限制

持久化补充：[四类别响应拒绝证据](SUPPLIER-JSON-PERSISTENCE-2026-10-25.md)新增24项真实PG/私有归档/队列验证，8组最终119项通过；受控HTTP不计真实供应商成功。

后续同日补充：[Ubuntu26本机验收](WINDOWS-UBUNTU26-ACCEPTANCE-2026-10-25.md)已完成112项Linux分组回归、实际Compose UI访问/权限验证；下文环境不可用描述保留为当时记录，不代表当前状态。真实来源下载发布仍未通过。

本轮沿用完整专业数据采集工作台目标及固定验收范围，推进 OpenSpec 4.1 的解码边界，不勾选4.1或其余完整任务。用户已确认原目标，根目录 `GOAL.md` 仅引用完整目标，未缩小范围。整体仍为19/42。

## 缺陷及实现

新回归首次运行24项，15失败、9通过，复现重复键被最后一个值覆盖、NaN/Infinity通过、布尔/小数/显式null业务码被接受、容器业务码抛出TypeError、不可表示指数抛出Decimal异常，以及失败输入被记为调用成功。嵌套深度用例起初没有达到Python 3.12实际C解析器边界，后改为100,000层的小型受控正文验证解析器异常；该初始用例本身不能作为产品缺陷证据。

新增MaiRui私有响应解码器：任意层级重复键（含转义后同名）、NaN/Infinity常量及不可表示数值拒绝；精确Decimal、数值形文本和合法空字段保持。根对象显式code仅接受整数，缺少code的普通数据封套及直接数组仍可处理，已知401/403/108/429分类保持。解析器递归失败转为安全错误。HTTP字节捕获回调仍先于解码；捕获失败不能继续使用响应。错误对外为固定unavailable，正文、URL、凭证不进入诊断。

规则只影响新HTTP获取。旧缓存、冻结转换检查点、映射版本、原始文件和发布没有改写；离线重放仍走注册纯转换器。本轮没有迁移、部署或供应商请求。

## 最终检查

Windows本机，Python 3.12.14；独立 `.tools/windows-checks` 测试环境，pytest9.1.1、Pydantic2.13.5、HTTPX0.28.1及Ruff0.16.10，版本按项目锁文件选用；这不是完整 `uv sync --locked` 环境。Windows额外安装tzdata。配置镜像下载403后改用PyPI；依赖未写入项目锁文件。

```powershell
& '.tools/windows-checks/Scripts/python.exe' -m pytest tests/test_supplier_json_boundary.py tests/test_usage_policy.py tests/test_usage_execution.py -k 'not partial_response_capture' --tb=short
& '.tools/windows-checks/Scripts/python.exe' -m ruff check adapters/data_mairui/src/qtrade_data_mairui/response_json.py adapters/data_mairui/src/qtrade_data_mairui/collection.py tests/test_supplier_json_boundary.py
& '.tools/windows-checks/Scripts/python.exe' -m ruff format --check adapters/data_mairui/src/qtrade_data_mairui/response_json.py tests/test_supplier_json_boundary.py
& '.tools/windows-checks/Scripts/python.exe' -X utf8 scripts/check_architecture.py
```

最终47通过、2项明确排除。新增28项包含精确捕获封套、安全日志、已知错误分类、精确值、捕获失败、失败用量终结，以及行情/因子fetch_prepared拒绝且不产生检查点或调用转换器、财务/事件fetch_with_capture留下精确响应但不产生RAW页。均为MockTransport/受控回调，**不是持久数据库、真实文件归档、供应商、Worker或部署UI端到端证明**。Ruff检查、格式和架构检查通过。架构脚本初次因Windows默认GBK读取UTF-8失败，最终以Python `-X utf8` 通过。

扩大回归的失败没有隐藏：因子缓存与并发两文件合计14项中10项在既有目录fsync的 `os.O_DIRECTORY` 处失败，4项通过；两项部分响应计费用例在既有BoundedHttpClient的Linux平台前提处失败，因此最终显式排除。未修改生产持久化或传输保障来制造Windows通过。完整Linux回归、mypy及OpenSpec严格CLI校验尚未执行（本机未发现相应CLI）。

## 当前部署及交付阻塞

- 旧报告对应Linux本机13000 Web、18100 collector、3121 SSO、3122 API及3123业务Worker；2026-10-25当前Windows对这五个loopback端口实际连接均返回10061（连接拒绝）。未发现Docker CLI，WSL列举未能取得可用发行版。
- 当前同步工作区没有Git仓库元数据，`git status`/`git log`返回非仓库。本轮变更保存在文件中，尚未提交Git；9.6仍未完成。不要对现有目录运行git init冒充原仓库历史。
- 取得原Linux部署/仓库或一个保留原数据与授权的可用测试环境后，须重跑完整隔离回归，执行UI→API→真实数据库→Worker→来源→归档→发布/消费者→刷新，含拒绝、角色及桌面/手机验证。当前没有这些新的证据。

## 六主业务矩阵

| 主业务 | 本轮证据 | 未完成范围 |
| --- | --- | --- |
| 登录/权限 | 无新部署验证；受控来源整数权限码分类保持 | SSO、角色/跨作用域、撤权、桌面/手机重验 |
| 策略版本 | 未改、未测 | 不新增就绪声明 |
| 数据采集 | 受控HTTP边界、四类别拒绝、精确字节捕获回调、用量失败终结 | 持久归档/门禁/Worker/消费者/UI完整闭环及Linux回归 |
| 回测/报告 | 未改、未测 | 不新增真实引擎就绪声明 |
| 告警 | 未改、未测 | 真实来源异常及恢复链路仍待验收 |
| 连接交易控制 | 未改、未测 | 不扩大实盘或声称连接可用 |

该修复只补强解码门禁，不表示完整4.x质量规则、自动更新/新鲜度、用量公平、恢复或整个Goal完成。
