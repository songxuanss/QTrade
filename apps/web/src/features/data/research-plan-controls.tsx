import type { ResearchPlan } from "./research-collection-types";

export function ResearchPlanControls({
  plan,
  name,
  confirmed,
  onConfirm,
}: {
  plan: ResearchPlan;
  name: string;
  confirmed: boolean;
  onConfirm: (value: boolean) => void;
}) {
  const estimate = plan.execution_estimate;
  return (
    <div className="research-plan-controls" aria-label={`${name}调用估计`}>
      <dl className="research-plan-metrics">
        <div>
          <dt>执行调用估计</dt>
          <dd>
            {estimate.calls_min}–{estimate.calls_max} <span>次</span>
          </dd>
        </div>
        <div>
          <dt>内部信用估计</dt>
          <dd>
            {estimate.credits_min}–{estimate.credits_max}
          </dd>
        </div>
      </dl>
      <p className="data-secondary">{estimate.basis}</p>
      <p className="data-secondary">
        单次最多 {plan.plan_guard.max_instruments} 个标的 /{" "}
        {plan.plan_guard.max_new_parts} 个分段；确认不能绕过规模或硬预算。
      </p>
      {plan.confirmation_required && (
        <label className="research-plan-confirmation">
          <input
            type="checkbox"
            aria-label={`${name}确认调用规模`}
            checked={confirmed}
            onChange={(event) => onConfirm(event.target.checked)}
          />
          我已核对范围及调用区间，确认执行；重试可能增加实际用量。
        </label>
      )}
    </div>
  );
}
