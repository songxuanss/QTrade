# Windows 本机 Linux 验证环境（Ubuntu 26）

> 2026-10-31 更新：用户已将项目 Git 交付和日常隔离工具链改为 Ubuntu 24（见根目录 `AGENTS.md`）；本文保留 Ubuntu 26 环境的建立记录，作为可选的部署验证目标，不再表示每次交付必须在 26 执行。

用户于2026-10-25授权在当前Windows机器建立类似原Linux部署的环境，并明确要求Ubuntu 26。本环境使用独立的 **Ubuntu 26.04.1 LTS / WSL 2**，不转换或删除原Ubuntu-18.04（WSL 1），不恢复/覆盖旧生产数据库，不启用实盘。当前环境建立过程不能替代Goal的真实业务验收。

## 已核验基础

- Windows 10 Pro 22H2，build19045.6466；虚拟化已启用。
- WSL3.0.3，Linux内核6.18.54.1；管理员确认后安装Microsoft包，未执行Windows重启。
- 新发行版名称 `QTrade-Ubuntu-26`，虚拟磁盘位于 `D:\QTrade-WSL\Ubuntu-26`，使用D盘避免挤占C盘。
- 实际 `/etc/os-release` 为Ubuntu26.04.1 LTS，systemd为PID1。
- 官方Ubuntu镜像 `ubuntu-26.04.1-wsl-amd64.wsl`，SHA-256 `48d56724b5c8e60f24893e83e73bbb58c60b3ca22fba3da977075420acd54104`，与官方HTTPS校验文件一致。
- WSL包 `Microsoft.WSL_3.0.3.0_x64_ARM64.msixbundle`，SHA-256 `b0d0c317ee7249eb4a40788b1516d3c3053cdfce62ff7bef7f0c35f4d2606005`，与Microsoft发布资产digest一致，安装由Windows验证包签名。实际WSL可执行文件Microsoft签名有效。

来源：[Microsoft WSL发布](https://github.com/microsoft/WSL/releases/tag/3.0.3)、[Ubuntu 26.04官方校验文件](https://releases.ubuntu.com/26.04/SHA256SUMS)、[Microsoft导入/安装说明](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)。这些来源说明安装机制和来源，实际可用性仍以本机执行结果为准。

## 当前状态及命令

从Windows QTrade目录运行：

```powershell
.\scripts\windows-qtrade.ps1 -Action status
.\scripts\windows-qtrade.ps1 -Action keepalive
.\scripts\windows-qtrade.ps1 -Action bootstrap -Proxy http://127.0.0.1:13556
```

基础依赖、锁文件Python环境、镜像及生产Web构建已完成。`status`显示实际状态，不要并行运行bootstrap/apt。`keepalive`复用经过进程身份核对的隐藏WSL客户端，避免最后一个客户端退出后发行版关闭；systemd服务不足以保持WSL存活。尚未配置Windows登录自动启动。

当前入口为 <http://127.0.0.1:13000/workspace/data>。Compose运行Web、业务PostgreSQL、市场库、Valkey、对象存储、独立collector及其数据库；SSO3121、业务API3122和业务Worker3123使用原生验证进程，原生Web3120另保留。真实SSO、桌面/手机访问刷新、会话恢复及六组权限拒绝检查通过。健康不代表下载完成，范围见[本机验收增量](../testing/WINDOWS-UBUNTU26-ACCEPTANCE-2026-10-25.md)。

Windows旧system32 WSL入口在升级后出现访问错误；已验证的入口为 `C:\Program Files\WSL\wsl.exe`，包装脚本明确使用它，不修改其他应用的PATH。原Windows PATH中失效的 `G:\VMWare\bin` 会产生路径翻译警告，尚未据此删除用户PATH项。

直接进入新发行版：

```powershell
& 'C:\Program Files\WSL\wsl.exe' --distribution QTrade-Ubuntu-26
```

## 网络与源码边界

WSL首次NAT配置失败后自动回退Consomme。该模式下直接DNS和直连HTTPS探测失败，使用当前Windows已有的无凭证loopback HTTP代理后，Ubuntu官方仓库HTTPS返回200，apt官方索引获取通过。未通过开放外部端口或关闭防火墙解决网络问题。代理端口是当前机器配置，不是通用部署默认；其他机器应显式提供自身无凭证loopback代理，或在可直连时省略Proxy。

`setup-wsl-linux.sh` 仅在指定新发行版、root身份及Ubuntu26.04下工作。它安装Ubuntu仓库的Docker Engine/Compose/Buildx、Node.js/npm及Python工具；项目Python由uv0.12.22按照锁文件准备3.12版本，Ubuntu系统Python保持自己的版本。Docker daemon仅配置该发行版内的代理，**不代表bridge容器已经能访问代理或真实供应商**，后续必须分别核验构建和运行网络。

源码从Windows QTrade与sibling SSO复制到Linux `/home/qtrade/workspaces/QTrade` 和 `/home/qtrade/workspaces/sso`。不复制 `.env*`、`.tools`、`.venv`、Git元数据、Node依赖、旧测试输出，不删除目标文件。当前同步目录原本就缺少Git元数据；Linux副本是源码副本，不声称恢复Git提交历史。后续代码变更须再次同步并核对实际部署文件，不以副本存在代替部署版本证据。

## 尚未完成及边界

用户确认Git交付使用Ubuntu26，保留原仓库历史，每个独立且已验证的阶段及时提交，不积累到最后统一提交。当前仍缺原仓库地址或含`.git`的备份，尚不能产生保留历史的提交。找到原仓库后须先比对迁入当前变动，再按实际依赖分阶段提交；不得用新建历史或改写提交时间冒充此前已提交。提交授权不等于远程push授权。

- 新环境真实下载、非空RAW、质量发布、消费者及自动采集闭环尚未通过；运行中的Worker/调度器不代替业务验收。
- 候选MaiRui凭证复测为HTTP404/业务404，权限未确认，未保存到运行配置；基础访问与Pro历史/因子权益须分别验证。
- 全部角色/跨站、键盘及完整桌面/手机故障矩阵仍未完成；本轮已验证的权限和会话持久化不外推全部业务。
- `configure-wsl-dns.py`的配置尚未通过发行版重启验证，新WSL调用仍重写resolv.conf；不能声称DNS已修复。当前构建及探查使用显式代理。
- 私有配置和新验证身份不进入日志、截图或报告，不冒充旧环境账号或业务数据。
- 原Git历史与旧数据迁移没有完成，不能据此关闭交付/恢复任务。

完整Goal与19/42验收计数保持；基础环境就绪后继续完整Linux回归和部署业务验收。
