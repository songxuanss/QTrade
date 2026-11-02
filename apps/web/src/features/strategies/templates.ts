import templateData from "./templates.json";

export type StrategyTemplate = {
  id: string;
  name: string;
  summary: string;
  tags: string[];
  default_name: string;
  default_description: string;
  code: string;
  schema: Record<string, unknown>;
};

export const STRATEGY_TEMPLATES: StrategyTemplate[] =
  templateData as StrategyTemplate[];

export function findTemplate(id: string): StrategyTemplate | undefined {
  return STRATEGY_TEMPLATES.find((template) => template.id === id);
}