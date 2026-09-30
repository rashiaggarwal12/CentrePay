import * as Crypto from "expo-crypto";

/**
 * One key per user intent (e.g. one tap of "Collect"). Reuse it when retrying that same
 * intent, so the server can recognise the retry and return the original result.
 */
export function newIdempotencyKey(): string {
  return Crypto.randomUUID();
}
