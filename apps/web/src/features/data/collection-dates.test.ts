import { describe, expect, it } from "vitest";
import { collectionDates } from "./collection-dates";

describe("collection calendar date boundaries", () => {
  it("includes the entire last local A-share date", () => {
    expect(
      collectionDates("2025-09-01", "2025-09-10", "Asia/Shanghai"),
    ).toEqual({
      start: "2025-08-31T16:00:00.000Z",
      end: "2025-09-10T16:00:00.000Z",
    });
  });
  it("preserves US daylight-saving day length", () => {
    const dates = collectionDates(
      "2025-03-09",
      "2025-03-09",
      "America/New_York",
    );
    expect(
      new Date(dates.end).getTime() - new Date(dates.start).getTime(),
    ).toBe(23 * 3600000);
  });
  it("rejects invalid and reversed dates", () => {
    expect(() => collectionDates("2025-02-30", "2025-03-01", "UTC")).toThrow();
    expect(() => collectionDates("2025-09-10", "2025-09-01", "UTC")).toThrow();
  });
});
