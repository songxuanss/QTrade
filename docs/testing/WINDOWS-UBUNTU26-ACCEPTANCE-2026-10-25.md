# Ubuntu26本机验收增量（2026-10-25）

完整Goal保持19/42，不勾选新的完整任务。旧Linux历史验收不证明本机新环境具有旧数据。环境说明见[运行手册](../operations/WINDOWS-LOCAL.md)。

## 当前部署及测试

独立WSL2发行版`QTrade-Ubuntu-26`，实际Ubuntu26.04.1 LTS。Compose Web13000，原生SSO3121/API3122/Worker3123，独立collector18100。复核七个容器均healthy；这只是可用性前提。没有原Git元数据或旧业务数据，不以新建Git库替代恢复历史。

根锁文件Python3.12.15环境的112项分组测试通过：供应商JSON边界、标准化输入、来源标志、目录缓存并发、用量策略/执行、有界HTTP。两处供应商模块mypy及原生/生产Web构建通过。collector原Dockerfile独立使用pip范围依赖，其容器版本不等同根uv锁文件；112项不外推全部容器契约。

从Ubuntu项目`apps/web`执行：

```sh
QTRADE_VERIFICATION_ORIGIN=http://127.0.0.1:13000 QTRADE_ACCESS_REPORT=windows-compose-access node tests/verification-collector-access.mjs
node tests/verification-collector-authorization.mjs
```

访问脚本通过3组：真实SSO；桌面1440及手机390导航、直达、刷新、无页面横溢；仅使当前测试会话在真实数据库过期后SSO恢复并返回采集页。原生3120另通过同组，但其报告固定文字误称Compose，仅13000结果用于本次Compose证据。

权限脚本通过6组：匿名401；无成员权限跨工作区404；无CSRF授权修改403且权限不变；risk_manager只读角色在两视口刷新后读200、写403、无写控件且有只读说明；拒绝前后持久任务列表一致；撤权立即使当前会话401，刷新仍401。仅使用真实隔离SSO/API/PostgreSQL及collector，没有供应商任务或伪造数据。最终停用第二身份的验证成员关系。

初次测试错误预期跨工作区403、只读按钮disabled、撤权后404；阅读既有安全/页面实现后改为隐藏资源404、写控件不存在、会话失效401。失败运行不计通过，最终完整六组一次通过。本轮未修改产品鉴权。

安全摘要在Linux`.tools/verification-local/review/windows-compose-access.json`和`windows-collector-authorization.json`，已复制到Windows`.tools/windows-environment/`。仅记录固定检查名/地址/状态，不保存凭证、cookie或CSRF；不启用登录截图/trace，错误只输出固定阶段。

## 六主业务矩阵（本机）

| 主流程 | 真实集成证据 | 未覆盖/阻塞 |
| --- | --- | --- |
| 登录/权限 | 真实SSO、API、PG；授权/撤权、CSRF、跨工作区拒绝、两视口/刷新/会话恢复 | 全部角色、跨站Origin及完整键盘矩阵 |
| 策略版本 | 本轮未运行 | 创建/冻结/切换及持久消费者 |
| 数据采集 | Compose页面、服务桥接、真实任务读取、拒写无副作用 | 候选许可HTTP404；新下载→Worker→非空RAW→门禁→发布→消费→刷新未通过 |
| 回测/报告 | 本轮未运行 | 真实执行器、任务、持久报告/刷新 |
| 告警 | 本轮未运行 | 过期→outbox→通知记录→恢复 |
| 已连接交易控制 | 本轮未运行，实盘保持关闭 | 未连接不算回归通过，不扩展到真实订单 |

无fixture计作真实来源成功。候选凭证通过私有stdin有界复测，HTTP404、响应110字节、整数业务码404；未确认权限，未保存到运行配置，Pro/history未测。不能据此单独判定永久无效，也不能声称已授权。解阻后仍须完成真实业务闭环。

## 保留完整范围

[OpenSpec](../../openspec/changes/complete-professional-data-workbench/tasks.md)中完整质量/重处理、预算公平性、全部工作台用例、日历调度/恢复、新鲜度告警、真实因子/研究类别、隔离备份恢复及最终Git交付仍未完成。DNS持久化和Windows登录自动恢复未验收。基础部署不关闭上述任务。
