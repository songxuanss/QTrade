# 普通历史财务适配器阶段证据

2026-10-13实施OpenSpec3.3的一部分，尚未完成任务，不勾选。新增MaiRuiFinancial支持balance/income/cashflow来源，按年规划、报告期半开范围到接口包含端点转换。禁止指数及未验证沪深之外市场。披露日保持date精度，历史可知时间未知，不启用PIT；单位和累计/单季未确认时保存原值，不换算。

实际现有证书发起600519收入表2015年单次有界请求，归档6行，raw内容摘要 `5a6a02afa8bbd88dc6839f45db30f1f14ae27e3e1f0aa2b7c159e0d1dc2e76fe`。初次转换因来源用“-”缺失标记被拒绝；原始文件保持。修复后从该不可变原始响应成功转换6个财务版本，132项未报告字段明确not_reported，与provider_null及零区分。Decimal解析保留精确数值，未知字段作为类型化指标保存原字段ID，不冒称已验证中文定义。

3项受控HTTP测试通过：精度/同报告期修订/未知单位/缺失分类、坏报告日期拒绝、权限错误不能转为空成功。mypy与ruff通过。真实样本不代表完整下载链路：尚未接入任务Worker、真实数据库发布和UI消费；另外两张报表本阶段未重新实测。3.3仍未完成，后续接入部署链路并逐项验收。

## 部署API/Worker闭环追加

本阶段已将三表能力组合入实际collector、启动循环调用ResearchWorker，并增加POST /v2/research/jobs、GET /v2/research/jobs/{request_id}；预览仍无副作用。现有身份回查负责read/manage和作用域限制，Worker执行/发布前再次验证manage。入口不提供fixture源。

`python scripts/verification-research-live.py`在18100实际部署通过：600519、2015收入表，纯预览后任务404，重复提交同请求、真实Worker、来源响应、非空原始/标准、门禁发布6个版本、明确版本查询、PIT拒绝、状态重新查询一致。详细公开身份/清单保存私有忽略路径review/research-live-results.json，无证书。当前镜像 `sha256:9220f39c687cc4a00daa1326669d0154f54bb31d95c8704d0dae39fd3c7f7d7c`，迁移0007已随启动应用。来源能力等级仍sample，不能从单项API闭环升级完整UI验收。

`verification-research-interface.py`实际7项通过（普通财务目录/计划、未知schema、跨作用域及未知清单拒绝、旧任务兼容）；原4个行情产物非空归档/分页摘要再次复核通过。20项契约/财务适配器测试、ruff/mypy通过。

本记录早先“未连接Worker”等描述是前阶段状态，以此次追加为准。三表2025及income2015完整验收、UI消费、桌面手机/只读撤权等仍待完成；OpenSpec3.3不勾选，整体目标不完成。队列未实现全部缺失覆盖/失败重试/取消/续租，用量与调度仍按目标推进。

## 2025三表实际部署追加

使用verification-research-live.py参数化固定范围，分别执行balance/income/cashflow、2025年半开报告期。三项均真实Worker完成、各4条发布，查询记录报表类型及报告年份一致，未知可知时间保持，as-of拒绝，重新读取状态一致。

| 报表 | 记录数 | 门禁 | 标准记录SHA256 | 发布内容版本 |
|---|---|---|---|---|
| balance | 4 | warning | 6560197d36be47a6c4451b8abc7780ed953e764bf573d340fb817a0028d3ef8a | 9fba53dbf771fbfae5c0e9e0de9a3d918ef5b61b6dc24ead22bf3722baa5f4c9 |
| income | 4 | warning | efca00f040b4fe7559ba48685bd23c353320d60ed7020fe0775d501ae56b7901 | c7a0a51ea0de448d430de062ca61369b0c2fb721901760c91ee98cc62744fcf0 |
| cashflow | 4 | warning | 54d0b6ab20e39a0d5443d1c934bf9dcb628cded3dcb840d4fe429ed61d2f5ee1 | 67357c3a4861191d64883409bf2e880d178e5565d6260c0839cf9cbfdcc1a4fd |

每次验证使用新运行身份，详细清单私有保存独立文件以保留前次证据，未覆盖旧验证。脚本默认income2015，允许固定income2015和三表2025参数组合；其他范围不借用这些数量断言。此前“另外两张报表未实测”已由本次追加补齐。UI、全部权限/撤权、默认年/增量等仍未完成，OpenSpec3.3保持未勾选。

## 首次财务UI真实闭环

`cd apps/web && node tests/verification-financial-ui.mjs`通过：实际13000、SSO登录、UI选择income/600519/2015报告期、预览→执行→刷新任务至已发布→浏览6个版本→整页刷新→再次浏览6个版本。请求经过业务3122身份/CSRF桥接、独立18100队列/真实Worker/现有MaiRui来源、归档校验门禁和明确版本查询。该公开Reader每次核验原始及标准完整性，未使用fixture Provider。桌面1440×1000及手机390×844页面无横向溢出，财务区域axe违规均0。报告私有review/financial-ui-results.json不含凭证。

部署Web镜像sha256:069ef1c9d0972b16566b4c01feaa85b8b0e01bd2465eaad278f7a8282e52484f、collector镜像sha256:e98255c2b5d8fb876e79ccf46a42370f2d5bbefecbf9bb138ff5b752f649d3f0；实际业务API已保留原环境和数据库重启加载桥接。Web类型/ESLint/生产构建通过。

按3.3任务文字，三表真实Worker非空发布及公开版本查询、同报告期披露修订、原值/未知单位和PIT拒绝已验证，任务完成。公司行为尚未接入、6.4完整财务/事件消费仍未完成；此UI用例不是默认年/补缺、输入刷新持久化、全部角色/撤权/键盘/故障矩阵的证明。这些原目标要求继续实施，不升级总体交付。
