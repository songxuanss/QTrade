import templateData from "./templates.json";

export type StrategyTemplate = {
  id: string;
  name: string;
  category: string;
  summary: string;
  tags: string[];
  default_name: string;
  default_description: string;
  code: string;
  schema: Record<string, unknown>;
};

export type StrategyTemplateGroup = {
  category: string;
  templates: StrategyTemplate[];
};

export const STRATEGY_TEMPLATES: StrategyTemplate[] =
  templateData as StrategyTemplate[];

export const STRATEGY_TEMPLATE_GROUPS: StrategyTemplateGroup[] =
  STRATEGY_TEMPLATES.reduce<StrategyTemplateGroup[]>((groups, template) => {
    const group = groups.find((item) => item.category === template.category);
    if (group) {
      group.templates.push(template);
    } else {
      groups.push({ category: template.category, templates: [template] });
    }
    return groups;
  }, []);

export function findTemplate(id: string): StrategyTemplate | undefined {
  return STRATEGY_TEMPLATES.find((template) => template.id === id);
}