/**
 * Money is integer paise everywhere, exactly like the backend. These helpers only
 * format and parse; the app never adds up amounts itself (the server does).
 */

/** Indian digit grouping: 1234567 -> "12,34,567". */
function groupIndian(digits: string): string {
  if (digits.length <= 3) return digits;
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return `${rest},${last3}`;
}

/** 125000 -> "₹1,250.00" */
export function formatINR(paise: number): string {
  if (!Number.isSafeInteger(paise)) throw new Error(`Not an integer paise amount: ${paise}`);
  const sign = paise < 0 ? "-" : "";
  const abs = Math.abs(paise);
  const rupees = Math.floor(abs / 100).toString();
  const rest = (abs % 100).toString().padStart(2, "0");
  return `${sign}₹${groupIndian(rupees)}.${rest}`;
}

/**
 * Parse what staff type into paise, using string maths (no floats):
 * "1250" -> 125000, "1,250.5" -> 125050, "0.05" -> 5. Returns null if invalid.
 */
export function parseRupees(input: string): number | null {
  const cleaned = input.replace(/[₹,\s]/g, "");
  const match = /^(\d{1,9})(?:\.(\d{0,2}))?$/.exec(cleaned);
  if (!match) return null;
  const [, rupees, fraction = ""] = match;
  return Number(rupees) * 100 + Number(fraction.padEnd(2, "0"));
}

/** 125050 -> "1250.50" (for pre-filling an input). */
export function paiseToInput(paise: number): string {
  return `${Math.floor(paise / 100)}.${(paise % 100).toString().padStart(2, "0")}`;
}

/** 1800 bps -> "18%" */
export function formatBps(bps: number): string {
  return `${bps / 100}%`;
}
