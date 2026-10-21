import { describe, expect, it } from "vitest";
import { dateBounds, localDate } from "./listFilters";
describe("local calendar list filters", () => {
  it("includes the entire ending date and sends local midnight as UTC", () => {
    const bounds = dateBounds("2026-09-14", "2026-09-14");
    const start = new Date(bounds.date_from); const end = new Date(bounds.date_to);
    expect(start.getHours()).toBe(0); expect(start.getDate()).toBe(14);
    expect(end.getHours()).toBe(0); expect(end.getDate()).toBe(15);
    expect(localDate(bounds.date_from)).toBe("2026-09-14"); expect(localDate(bounds.date_to, true)).toBe("2026-09-14");
  });
  it("handles an inclusive month-end correctly and rejects inverted dates", () => {
    const bounds = dateBounds("2026-09-30", "2026-09-30");
    expect(localDate(bounds.date_to)).toBe("2026-10-01");
    expect(() => dateBounds("2026-10-01", "2026-09-30")).toThrow("开始日期不能晚于结束日期");
    expect(dateBounds("", "")).toEqual({ date_from: "", date_to: "" });
  });
});
