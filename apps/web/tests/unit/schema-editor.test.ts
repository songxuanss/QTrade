import { describe, expect, it } from "vitest";
import {
  addParameter,
  emptySchema,
  parseSchema,
  removeParameter,
  renameParameter,
  serializeSchema,
  setRequired,
  updateRule,
  validateSchema,
} from "../../src/features/strategies/schema-editor";

describe("schema editor", () => {
  it("parses only JSON objects", () => {
    expect(parseSchema("[]")).toBeNull();
    expect(parseSchema("not json")).toBeNull();
    expect(parseSchema('{"type":"object"}')).toEqual({ type: "object" });
  });

  it("adds a parameter and rejects duplicates or invalid names", () => {
    const added = addParameter(emptySchema(), "lookback");
    expect(added.ok).toBe(true);
    if (!added.ok) return;
    expect(added.schema.properties.lookback).toEqual({
      type: "number",
      default: 0,
    });
    expect(addParameter(added.schema, "lookback").ok).toBe(false);
    expect(addParameter(added.schema, "1bad").ok).toBe(false);
  });

  it("keeps required in sync when renaming or removing", () => {
    const schema = setRequired(
      { ...emptySchema(), properties: { fast: { type: "integer" } } },
      "fast",
      true,
    );
    const renamed = renameParameter(schema, "fast", "short");
    expect(renamed.ok).toBe(true);
    if (!renamed.ok) return;
    expect(renamed.schema.required).toEqual(["short"]);
    expect(renamed.schema.properties.fast).toBeUndefined();
    expect(removeParameter(renamed.schema, "short").required).toEqual([]);
  });

  it("drops constraints that no longer match the type", () => {
    const schema = {
      ...emptySchema(),
      properties: {
        threshold: {
          type: "number",
          minimum: 1,
          maximum: 10,
          minLength: 2,
        },
      },
    };
    const asText = updateRule(schema, "threshold", { type: "string" });
    expect(asText.properties.threshold.minimum).toBeUndefined();
    expect(asText.properties.threshold.maximum).toBeUndefined();
    const asNumber = updateRule(schema, "threshold", { type: "integer" });
    expect(asNumber.properties.threshold.minLength).toBeUndefined();
  });

  it("validates the server schema subset", () => {
    expect(validateSchema(emptySchema())).toBeNull();
    expect(
      validateSchema({ ...emptySchema(), additionalProperties: true as false }),
    ).toContain("additionalProperties");
    expect(
      validateSchema({
        ...emptySchema(),
        properties: { fast: { type: "integer", minimum: 5, maximum: 2 } },
      }),
    ).toContain("最小值");
    expect(
      validateSchema({
        ...emptySchema(),
        properties: { flag: { type: "boolean", default: "yes" } },
      }),
    ).toContain("布尔值");
    expect(
      validateSchema({
        ...emptySchema(),
        properties: { fast: { type: "integer" } },
        required: ["missing"],
      }),
    ).toContain("required");
  });

  it("round-trips through serialization", () => {
    const schema = addParameter(emptySchema(), "slow");
    if (!schema.ok) throw new Error("unexpected");
    expect(parseSchema(serializeSchema(schema.schema))).toEqual(schema.schema);
  });
});
