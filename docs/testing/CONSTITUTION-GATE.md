# 宪法门禁与验证记录

`CONSTITUTION.md` 把散落在 `docs/`、`AGENTS.md` 与验收脚本里的硬约束收敛为可编号、可引用的条文。
本文记录这些条文如何被强制、如何本地复跑，以及当前的强制边界。

## 1. 条文与强制方式

| 条文 | 主题 | 强制 |
| --- | --- | --- |
| A1 | 规格先行（OpenSpec 唯一变更入口，产物中文） | automated |
| A2 | Ports & Adapters 边界 | automated |
| A3 | 外部引擎与厂商 SDK 只出现在 adapter | automated |
| A4 | 用户策略代码只在隔离 Runner 执行 | automated |
| A5 | 身份只来自 SSO，禁止本地密码认证 | automated |
| A6 | 凭据不入库、不入日志、不入产物 | automated |
| A7 | 产物不可变且带 manifest | automated |
| A8 | 量化结论必须来自真实执行 | review |
| A9 | 未完成能力与外部故障显式披露 | review |
| A10 | 实盘默认关闭且受控 | review |
| A11 | 时间与交易日口径显式 | automated |
| A12 | 依赖与工具链锁定 | automated |

`automated` 表示 CI 失败即阻断合并；`review` 表示没有机械门禁，必须在提交说明、验收文档或变更归档中给出可复核证据。

## 2. 门禁实现

`scripts/check_constitution.py` 负责四类检查：

1. **文档结构**：版本号、修订日期、条文编号唯一、每条必须有 `**强制**` 行、强制的目标路径必须真实存在（删除被引用的测试会让检查失败）。
2. **A4 解释器边界**：扫描 `apps/`、`packages/`、`adapters/` 的 AST，`exec` / `eval` / `compile` 只允许出现在 `apps/runner/` 与 `adapters/runner/`。
3. **A5/A6 身份与凭据**：禁止密码哈希/校验库与口令标识符；扫描全部受版本控制文件中的私钥块、AWS/GitHub/Slack/Google 凭证特征；禁止跟踪 `.env`、`.pem`、`.key`、`.p12`、`.pfx`。
4. **A1/A12 变更与工具链**：pull request 上要求行为变更同时包含 `openspec/changes/` delta；校验 Python 3.12、Node 22 与 CI 的 `--locked` / `npm ci`。

接入位置：

- `tests/test_constitution.py`：`test_repository_constitution_passes` 在整个 `pytest` 中执行全部自动检查，因此现有 CI 的 `uv run pytest` 步骤已经生效；`test_pull_request_requires_openspec_delta` 在 `GITHUB_EVENT_NAME=pull_request` 且基线可取时执行 `--spec-diff`。
- `scripts/check-python.sh`：本地一条命令跑齐 ruff、mypy、架构与宪法检查。

`--spec-diff` 的独立 workflow 步骤尚未加入（当前推送凭据没有 workflow 写权限）。具备权限后补上 `fetch-depth: 0` 与该步骤即可启用：

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
# ...
- if: github.event_name == 'pull_request'
  run: uv run --locked python scripts/check_constitution.py --spec-diff "${{ github.base_ref }}"
```

## 3. 本地复跑

```bash
uv run --locked python scripts/check_constitution.py
uv run --locked python scripts/check_constitution.py --spec-diff origin/main
uv run --locked pytest tests/test_constitution.py
```

pull request 之外（例如直接推送 main）不会运行 `--spec-diff`，因为缺少可靠基线；这类提交靠评审与 OpenSpec change 记录。

## 4. 验证证据（2026-11-02）

| 项 | 结果 |
| --- | --- |
| `scripts/check_constitution.py` | `Constitution checks passed` |
| `scripts/check_architecture.py` | `Architecture checks passed` |
| `ruff check .` | 通过 |
| 全量 pytest | 1721 项，0 失败，299 跳过（新增宪法测试 34 项，其中 1 项仅在 pull request 环境执行） |
| `tests/test_ci_gates.py` | CI YAML 结构、必需门禁与失败传播仍通过 |

`--spec-diff` 用临时仓库验证了三条路径：

| 场景 | 结果 |
| --- | --- |
| 只改 `apps/api/a.py`，无 OpenSpec delta | 拒绝，提示补 delta 或标注 `[no-spec-change]` |
| 同一变更包含 `openspec/changes/x/spec.md` | 通过 |
| 提交信息含 `[no-spec-change]` | 通过 |

## 5. 已知边界

- A8、A9、A10 没有机械门禁。真实供应商不可用、实盘能力未接通时，只能靠验收文档与评审记录判断，不能用 fixture 通过代替。
- 独立 workflow 的 `--spec-diff` 步骤尚未接入；当前由 `pytest` 在 pull request 环境且基线可取时执行，直接推送 main 不受该门禁约束。仓库尚未配置分支保护，接入时应把宪法检查设为 required status check。
- 凭据扫描基于已知厂商特征，无法发现自定义格式的密钥；新增供应商密钥时需同步扩充 `SECRET_PATTERNS`。测试夹具若需要凭证特征，必须在运行时拼接字符串，否则扫描会命中测试文件本身。
- 宪法只能约束可观察产物，不能阻止代理在实现中偏离已批准的 delta；这部分仍由 OpenSpec 评审与测试覆盖承担。