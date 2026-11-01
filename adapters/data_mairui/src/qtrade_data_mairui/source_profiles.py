"""Dated, bounded evidence for the adapters shipped here; no network or secrets.

Keep this discovery separate from ResearchCapability embedded in frozen plans:
an editorial evidence correction must not change a saved plan's control digest.
"""

from datetime import date

from qtrade_data_contracts.collection import SourceDataset
from qtrade_data_contracts.research_transport import ResearchCapability
from qtrade_data_contracts.source_profiles import (
    ASPECTS,
    Aspect,
    CapabilityFact,
    EvidenceLevel,
    ProfileKind,
    SourceProfile,
)

from .collection import MaiRuiCollection
from .research import MaiRuiAdjustmentFactors, MaiRuiDistributions, MaiRuiFinancial

AUDIT = "docs/data/CAPABILITY-AUDIT-2026-10-12.md"
SCOPE = "docs/data/COLLECTION-ACCEPTANCE-SCOPE.md"


def fact(
    aspect: Aspect,
    detail: str,
    evidence: EvidenceLevel = "unknown",
    unblock: str | None = None,
    reference: str = AUDIT,
) -> CapabilityFact:
    return CapabilityFact(
        aspect=aspect,
        detail=detail,
        evidence=evidence,
        reference=reference,
        as_of=date(2026, 10, 16),
        unblock_condition=unblock,
    )


def profiles(provider: MaiRuiCollection) -> tuple[SourceProfile, ...]:
    """Use local adapter configuration only; never probe permission on page load."""
    registered = [*provider.datasets()]
    research = [
        c
        for p in (
            MaiRuiFinancial(provider, "balance"),
            MaiRuiFinancial(provider, "income"),
            MaiRuiFinancial(provider, "cashflow"),
            MaiRuiDistributions(provider),
            MaiRuiAdjustmentFactors(provider),
        )
        for c in p.capabilities()
    ]
    result = []
    items: list[SourceDataset | ResearchCapability] = [*registered, *research]
    for item in items:
        source = item.source
        is_price = source.endswith(".bars")
        is_factor = source == "mairui.factor.history"
        financial = source.startswith("mairui.financial.")
        if is_price:
            history = (
                "已验证2025年度日线；普通接口实际边界逐标的核验，不认证全历史。"
                if source == "mairui.history.bars"
                else "已验证2025-09的1m/5m；Pro往年年片与当年增量边界不同，非全历史认证。"
            )
            units = "股票样本v为手、a为元，标准量转股；股票价为元。指数的量价单位未逐项验证。"
            axis = (
                "北京时间；分钟结束标签转为区间开始，日线按交易日；查询左闭右开。"
                "1m的09:30原始边界时段仍未认证。"
            )
            adjustment = (
                "本适配器仅发布未复权；探查复权价格不代表已接入复权因子、基准或完整事件体系。"
            )
            updates = fact(
                "updates",
                "日线默认按交易日17:10估计可获取，非来源实时保证；分钟各周期的发布时延未完整核验。",
                "documented",
                reference="docs/testing/COLLECTION-NETWORK-AND-FROZEN-CHART-2026-10-13.md",
            )
        elif is_factor:
            history = "pe_ttm、roe_ttm有历史样本；北向最新/历史有差异，空响应非无权限；深度待验证。"
            units = "元数据缺单位、频率和计算版本；保留原值/未知，不猜单位或认证准确性。"
            axis = "trade_date是观测日；来源可知时间、报告期及更新时刻未验证，采集时间不能补造PIT。"
            adjustment = (
                "供应商因子与本地计算因子区分；价格复权或因子定义版本未知，不能推断研究PIT就绪。"
            )
            updates = fact(
                "updates",
                "因子频率按每项元数据呈现；缺失时保持未知，不能把所有因子当每日更新。",
                unblock="取得因子级更新时间规则与实际历史更新样本",
            )
        elif financial:
            history = "已验证600519的2015/2025三表及多披露版本，非全证券或完整修订账本。"
            units = (
                "保留来源数值及未知单位；合并/母公司、累计/单季口径未完整确认，不暗中缩放或差分。"
            )
            axis = "按报告期左闭右开；披露日非精确可知时间，无证据as-of拒绝。"
            adjustment = "普通历史财务，多披露版本分别保留；不作为行情复权或完整财务PIT承诺。"
            updates = fact(
                "updates",
                "财务按披露及修订更新，稀疏数据不套行情每日完整性；来源更新时间规则未完整确认。",
                unblock="取得报表披露/修订频率证据及持续观测",
            )
        else:
            history = "已验证600519现金分红/正送转；不证明配股、拆并股或完整历史事件。"
            units = "现金为每10股税前人民币，送股/转增为每10股；现金支付日缺失保持未知。"
            axis = "event_date按公告日筛选，非除权日或支付日；关键日期与本地采集时间分别记录。"
            adjustment = (
                "事件不是复权因子，不自动构造持仓现金账本；无原生事件ID，同日身份歧义拒绝。"
            )
            updates = fact(
                "updates",
                "事件按公告/生效规则更新，合法稀疏或无观测不解释为每日缺口；来源刷新时延未知。",
                unblock="取得事件发布时间/修订规则与持续观测",
            )
        facts = (
            fact(
                "history",
                history,
                "e2e_verified" if is_price else "sample_verified",
                reference=(
                    "docs/testing/COLLECTION-NETWORK-AND-FROZEN-CHART-2026-10-13.md"
                    if is_price
                    else AUDIT
                ),
            ),
            fact("units", units, "sample_verified"),
            fact("adjustment", adjustment, "documented", reference=SCOPE),
            fact("time", axis, "documented", reference=SCOPE),
            updates,
            fact(
                "permission",
                "现有证书的代表性样本已验证；已配置不等于当前账号全标的/全周期有权限，实际请求仍校验授权。",
                "sample_verified",
            ),
            fact(
                "quota",
                "供应商实际账户配额未取得完整证据；工作台额度是可配置的平台保护预算，不是套餐剩余权益。",
                unblock="取得账户实际配额说明及可核对用量证据",
            ),
            fact(
                "license",
                "现有付费授权不自动证明可公开分发、转售或对外展示；用途与导出许可仍待确认。",
                unblock="取得供应商对存储、导出、研究及展示用途的明确许可说明",
            ),
        )
        result.append(
            SourceProfile(
                source=source,
                name=item.name,
                kind=item.dataset_kind if isinstance(item, SourceDataset) else item.kind,
                mapping_version=item.mapping_version,
                markets=item.markets,
                periods=item.periods if isinstance(item, SourceDataset) else (),
                instrument_kinds=item.instrument_kinds,
                timezone=item.timezone,
                state="configured" if item.configured else "unconfigured",
                facts=facts,
            )
        )
    # These are extension requirements, not invented working supplier endpoints.
    extensions: tuple[tuple[ProfileKind, str, str], ...] = (
        (
            "membership_interval",
            "历史有效成员",
            "取得历史区间接口与成员变动样本；当前成员快照不代表历史区间",
        ),
        (
            "security_identity_change",
            "退市与证券身份变更",
            "取得历史证券目录、身份映射及成功退市样本；单样本404不能推导供应商不提供",
        ),
        (
            "rights_terms",
            "配股条款",
            "取得历史配股接口与样本；已核对本供应商公开文档与候选端点"
            "（hscp/jnpg、hscp/pg 返回 422，hscp/jnfh 无配股字段），"
            "结论仅限当前供应商与当前订阅",
        ),
    )
    for kind, name, condition in extensions:
        result.append(
            SourceProfile(
                source="extension." + kind,
                name=name,
                kind=kind,
                mapping_version=None,
                markets=("CN",),
                periods=(),
                instrument_kinds=("stock",),
                timezone="Asia/Shanghai",
                state="not_connected",
                facts=tuple(
                    fact(
                        a,
                        "本项目尚未接通；不能据此判断供应商不提供。",
                        unblock=condition,
                        reference=SCOPE,
                    )
                    for a in ASPECTS
                ),
            )
        )
    return tuple(result)


def reference_profile(configured: bool) -> SourceProfile:
    """Describe the separately hosted current-reference adapter without I/O.

    Configuration comes from its own deployment, not the collector's licence.
    A current membership observation has no historical effective interval.
    """
    return SourceProfile(
        source="mairui.reference",
        name="MaiRui 当前板块与指数成员",
        kind="current_membership",
        mapping_version="mairui.hszg.v1",
        markets=("CN",),
        periods=(),
        instrument_kinds=("stock",),
        timezone="Asia/Shanghai",
        state="configured" if configured else "unconfigured",
        facts=(
            fact(
                "history",
                "已验证当前沪深300等成员快照；不提供历史有效区间，不可用今天名单回填历史。",
                "sample_verified",
            ),
            fact(
                "units",
                "成员字段为证券代码、简称、交易所及分类关联；没有价格、数量或权重，不能猜成指数权重。",
                "documented",
                reference=SCOPE,
            ),
            fact(
                "adjustment",
                "分类版本mairui.hszg.v1；冻结成员与名称，后续目录变化不覆盖原快照。无复权语义。",
                "documented",
                reference=SCOPE,
            ),
            fact(
                "time",
                "observed_at是实际来源观察时刻，缓存重读保留原时刻；不作为历史成分生效起止。",
                "documented",
                reference=SCOPE,
            ),
            fact(
                "updates",
                "当前名单刷新及历史生效规则未完整确认；缓存期限只是平台行为，不是供应商更新保证。",
                unblock="取得成员变动、生效时间及发布规则的来源证据",
            ),
            fact(
                "permission",
                "现有授权已验证代表性当前成员；配置存在不保证当前全部行业、主题或指数权限。",
                "sample_verified",
            ),
            fact(
                "quota",
                "参考目录接口的实际账户配额未确认，不与下载服务的保护信用预算混同。",
                unblock="取得参考目录实际配额与可核对用量说明",
            ),
            fact(
                "license",
                "当前成员数据的存储、导出及对外展示许可未完整确认。",
                unblock="取得来源对当前参考数据用途的明确许可说明",
            ),
        ),
    )
