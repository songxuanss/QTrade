# 采集新建页布局优化验收

本次仅调整专业工作台的信息层级和排列，沿用 `complete-professional-data-workbench`；整体18/42保持，不以布局测试勾选整个业务Goal。

## 设计

- 沿用功能分区与“新建采集 / 任务与结果”视图。合并标题和连接状态，移除重复步骤标题与流程说明。
- 桌面扩大配置列，类别/来源/周期、股票池/标的、日期/下载方式按行排列，右侧预览摘要保持独立。390px手机并排日期，其他复杂字段整行，原40px输入高度保留。
- 日期依据、来源限制和标的优先规则可通过原生details按需展开，支持键盘Enter；切换视图保留草稿。错误、调用规模确认、预览与提交直接可达。因子分类搜索和选择保留。
- 展示CSS局限在collector模块，数据管理标题取消重复底边距；不修改来源、API、权限、Worker、归档或发布逻辑。必要注释解释展示边界和说明折叠原因。

## 实际部署

入口：`http://127.0.0.1:13000/workspace/data`。仅重建Web，镜像 `sha256:2c2fb5b099b8dde9dba3c093719e19a2275a1a797fed6877ed01a4792bec86eb`；沿用原SSO、API、业务Worker、独立collector和持久数据库。

相同空日期、默认行情配置下，测量 `#independent-collector` 的新建视图，不代表整页高度或其他任务状态：

| 视口 | 调整前 | 调整后 | 缩短 |
| --- | ---: | ---: | ---: |
| 1440×1000 | 939.7px | 644.1px | 31.5% |
| 390×844 | 1577.6px | 1185.4px | 24.9% |

桌面整个默认页面可在1000px高视口显示；手机仍使用正常页面滚动，不强制把表单塞进嵌套滚动区。桌面和手机截图均已人工视觉复核。部署前基线运行完成高度/管理员检查，但在READ长任务检查失败，不能视为完整基线通过；部署后完整回归另行执行并记录。

## 验证

```bash
cd apps/web
QTRADE_COLLECTION_REPORT=layout-density-after node tests/verification-collection-compact-ui.mjs
QTRADE_COLLECTION_REPORT=layout-density-after node tests/verification-collection-compact-ui.mjs --resume
cd ../..
QTRADE_COLLECTION_REPORT=layout-density-after .venv/bin/python scripts/verification-collection-compact-archive.py
```

首次运行创建601288.SH、2025-09-01至09-05日线全量任务，UI→实际API→Worker→MaiRui→非空RAW5/标准5→质量→发布→市场消费者visible。独立在collector中核验确切任务和发布的文件摘要、原始行对应history回执、转换输入及质量检查点；回执类别为calendar/history/http_response，非fixture或仅queued。

真实SSO管理员和采集READ，1440×1000及390×844：新建/结果切换保留输入、日期不完整拒绝、说明键盘展开且输入不变、行情与因子表单无横向溢出、axe无违规；结果刷新、已存任务选择、年度243条按100/100/43分页、有界键盘滚动、明确该下载数据集的5根K线和刷新。READ新建入口不显示，带有效CSRF的写预览返回403。只读未以隐藏按钮替代服务端授权验证。私有任务身份、截图和计数报告置于忽略的.tools目录，无凭证进入报告或提交。

生产构建、TypeScript、定向ESLint/Prettier、OpenSpec严格校验及git diff检查通过。本次未运行全站业务测试。

## 六主业务增量

| 主业务 | 本次范围 | 集成等级/边界 |
| --- | --- | --- |
| 登录/权限 | SSO、切工作区、管理员与READ、写入403 | 真实服务/数据库；未覆盖完整撤权矩阵 |
| 数据采集/市场消费 | 新下载、Worker、来源HTTP、RAW/标准各5、发布、图表及刷新；年度分页 | 真实集成；因子仅检查表单布局，不新增因子下载声明 |
| 策略版本 | 未改、未重测 | 不新增可用性声明 |
| 回测/报告 | 未改、未重测 | 不代表真实引擎交付 |
| 告警 | 未改、未重测 | 不代表实时来源已接通 |
| 连接交易控制 | 未改、未重测 | 未扩大实盘范围 |
