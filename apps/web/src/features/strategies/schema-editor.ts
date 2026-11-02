export type ParameterRule = {
  type?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  enum?: unknown[];
  title?: string;
  description?: string;
};

export type ParameterSchema = {
  type: "object";
  properties: Record<string, ParameterRule>;
  required: string[];
  additionalProperties: false;
  title?: string;
  description?: string;
};

export type SchemaEditResult =
  | { ok: true; schema: ParameterSchema }
  | { ok: false; error: string };

const PARAMETER_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const TYPES = new Set(["string", "integer", "number", "boolean"]);
const RULE_KEYS = new Set([
  "type",
  "default",
  "minimum",
  "maximum",
  "minLength",
  "maxLength",
  "enum",
  "title",
  "description",
]);

export function emptySchema(): ParameterSchema {
  return {
    type: "object",
    properties: {},
    required: [],
    additionalProperties: false,
  };
}

export function parseSchema(source: string): ParameterSchema | null {
  try {
    const value = JSON.parse(source) as unknown;
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    return value as ParameterSchema;
  } catch {
    return null;
  }
}

export function serializeSchema(schema: ParameterSchema): string {
  return JSON.stringify(schema, null, 2);
}

export function schemaProperties(
  schema: ParameterSchema | null,
): [string, ParameterRule][] {
  const properties = schema?.properties;
  if (!properties || typeof properties !== "object" || Array.isArray(properties))
    return [];
  return Object.entries(properties).filter(
    (entry): entry is [string, ParameterRule] =>
      Boolean(entry[1]) &&
      typeof entry[1] === "object" &&
      !Array.isArray(entry[1]),
  );
}

export function addParameter(
  schema: ParameterSchema,
  name: string,
): SchemaEditResult {
  const trimmed = name.trim();
  if (!PARAMETER_NAME.test(trimmed))
    return { ok: false, error: "参数名只能包含字母、数字和下划线，且不能以数字开头。" };
  if (Object.hasOwn(schema.properties, trimmed))
    return { ok: false, error: "参数名已存在。" };
  return {
    ok: true,
    schema: {
      ...schema,
      properties: {
        ...schema.properties,
        [trimmed]: { type: "number", default: 0 },
      },
    },
  };
}

export function removeParameter(
  schema: ParameterSchema,
  name: string,
): ParameterSchema {
  const properties = { ...schema.properties };
  delete properties[name];
  return {
    ...schema,
    properties,
    required: schema.required.filter((item) => item !== name),
  };
}

export function renameParameter(
  schema: ParameterSchema,
  from: string,
  to: string,
): SchemaEditResult {
  const trimmed = to.trim();
  if (trimmed === from) return { ok: true, schema };
  if (!PARAMETER_NAME.test(trimmed))
    return { ok: false, error: "参数名只能包含字母、数字和下划线，且不能以数字开头。" };
  if (Object.hasOwn(schema.properties, trimmed))
    return { ok: false, error: "参数名已存在。" };
  const properties: Record<string, ParameterRule> = {};
  for (const [key, rule] of Object.entries(schema.properties)) {
    properties[key === from ? trimmed : key] = rule;
  }
  return {
    ok: true,
    schema: {
      ...schema,
      properties,
      required: schema.required.map((item) => (item === from ? trimmed : item)),
    },
  };
}

export function updateRule(
  schema: ParameterSchema,
  name: string,
  patch: Partial<ParameterRule>,
): ParameterSchema {
  const current = schema.properties[name];
  if (!current) return schema;
  const next: ParameterRule = { ...current, ...patch };
  for (const key of Object.keys(next) as (keyof ParameterRule)[]) {
    if (next[key] === undefined || next[key] === "") delete next[key];
  }
  if (next.type === "string") {
    delete next.minimum;
    delete next.maximum;
  }
  if (next.type === "integer" || next.type === "number") {
    delete next.minLength;
    delete next.maxLength;
  }
  if (!Array.isArray(next.enum) || next.enum.length === 0) delete next.enum;
  return {
    ...schema,
    properties: { ...schema.properties, [name]: next },
  };
}

export function setRequired(
  schema: ParameterSchema,
  name: string,
  required: boolean,
): ParameterSchema {
  const values = schema.required.filter((item) => item !== name);
  if (required) values.push(name);
  return { ...schema, required: values };
}

export function validateSchema(schema: ParameterSchema): string | null {
  const allowedTop = new Set([
    "type",
    "properties",
    "required",
    "additionalProperties",
    "title",
    "description",
  ]);
  const unknownTop = Object.keys(schema).find((key) => !allowedTop.has(key));
  if (unknownTop) return `schema 含未知字段 ${unknownTop}。`;
  if (schema.additionalProperties !== false)
    return "schema 必须设置 additionalProperties: false。";
  if (schema.type !== "object") return "schema 的 type 必须是 object。";
  if (
    !schema.properties ||
    typeof schema.properties !== "object" ||
    Array.isArray(schema.properties)
  )
    return "schema.properties 必须是对象。";
  if (!Array.isArray(schema.required)) return "schema.required 必须是数组。";
  const names = Object.keys(schema.properties);
  if (schema.required.some((item) => !names.includes(item)))
    return "required 中的参数必须也出现在 properties 中。";
  for (const name of names) {
    const rule = schema.properties[name];
    if (!PARAMETER_NAME.test(name)) return `参数名 ${name} 不合法。`;
    if (!rule || typeof rule !== "object" || Array.isArray(rule))
      return `参数 ${name} 的规则必须是对象。`;
    const unknown = Object.keys(rule).find((key) => !RULE_KEYS.has(key));
    if (unknown) return `参数 ${name} 含未知字段 ${unknown}。`;
    if (!rule.type || !TYPES.has(rule.type))
      return `参数 ${name} 的 type 必须是 string、integer、number 或 boolean。`;
    if (rule.type === "string" && (rule.minimum !== undefined || rule.maximum !== undefined))
      return `参数 ${name} 是字符串，不能设置最小/最大值。`;
    if (rule.type === "integer" || rule.type === "number") {
      if (rule.minLength !== undefined || rule.maxLength !== undefined)
        return `参数 ${name} 是数值，不能设置长度范围。`;
      if (
        rule.minimum !== undefined &&
        rule.maximum !== undefined &&
        rule.minimum > rule.maximum
      )
        return `参数 ${name} 的最小值不能大于最大值。`;
    }
    if (rule.type === "integer" && rule.default !== undefined && !Number.isInteger(rule.default))
      return `参数 ${name} 的默认值必须是整数。`;
    if (
      (rule.type === "integer" || rule.type === "number") &&
      rule.default !== undefined &&
      typeof rule.default !== "number"
    )
      return `参数 ${name} 的默认值必须是数字。`;
    if (rule.type === "boolean" && rule.default !== undefined && typeof rule.default !== "boolean")
      return `参数 ${name} 的默认值必须是布尔值。`;
    if (rule.type === "string" && rule.default !== undefined && typeof rule.default !== "string")
      return `参数 ${name} 的默认值必须是字符串。`;
    if (rule.enum !== undefined && (!Array.isArray(rule.enum) || rule.enum.length === 0))
      return `参数 ${name} 的 enum 必须是非空数组。`;
  }
  return null;
}