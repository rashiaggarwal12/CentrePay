import { addDays, formatCountdown, secondsUntil } from "@/lib/dates";
import { formatINR, paiseToInput, parseRupees } from "@/lib/money";

describe("formatINR", () => {
  it.each([
    [125000, "₹1,250.00"],
    [0, "₹0.00"],
    [5, "₹0.05"],
    [99, "₹0.99"],
    [236000, "₹2,360.00"],
    [12345678, "₹1,23,456.78"], // Indian grouping: lakh, not hundred-thousand
    [1000000000, "₹1,00,00,000.00"],
    [-36000, "-₹360.00"],
  ])("%d paise -> %s", (paise, expected) => {
    expect(formatINR(paise)).toBe(expected);
  });

  it("refuses non-integer amounts", () => {
    expect(() => formatINR(12.5)).toThrow();
  });
});

describe("parseRupees", () => {
  it.each([
    ["1250", 125000],
    ["1,250", 125000],
    ["₹ 1,250.5", 125050],
    ["1250.50", 125050],
    ["0.05", 5],
    ["0.1", 10],
    ["12.", 1200],
    ["0", 0],
  ])("%s -> %d paise", (input, expected) => {
    expect(parseRupees(input)).toBe(expected);
  });

  it.each(["", "abc", "1.234", "-5", "1e3", "12.3.4"])("rejects %p", (input) => {
    expect(parseRupees(input)).toBeNull();
  });

  it("never goes through floats (0.1 + 0.2 style errors)", () => {
    // 1.15 * 100 === 114.99999999999999 in floating point.
    expect(parseRupees("1.15")).toBe(115);
    expect(parseRupees("4.35")).toBe(435);
  });

  it("round-trips with paiseToInput", () => {
    for (const paise of [0, 5, 100, 125050, 99999999]) {
      expect(parseRupees(paiseToInput(paise))).toBe(paise);
    }
  });
});

describe("dates", () => {
  it("adds days across month and year ends", () => {
    expect(addDays("2026-10-01", -1)).toBe("2026-09-30");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2028-02-28", 1)).toBe("2028-02-29");
  });

  it("formats countdowns and never shows negatives", () => {
    expect(formatCountdown(125)).toBe("2:05");
    expect(formatCountdown(-3)).toBe("0:00");
  });

  it("counts seconds until a timestamp", () => {
    const now = Date.parse("2026-10-01T10:00:00Z");
    expect(secondsUntil("2026-10-01T10:20:00Z", now)).toBe(1200);
  });
});
