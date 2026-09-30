import * as Sentry from "@sentry/react-native";

import { ApiError } from "@/api/client";
import type { Me } from "@/api/types";

const dsn = process.env.EXPO_PUBLIC_SENTRY_DSN;

/** Crash and error reporting. A no-op until EXPO_PUBLIC_SENTRY_DSN is set. */
export function initSentry() {
  if (!dsn) return;
  Sentry.init({
    dsn,
    environment: __DEV__ ? "development" : "production",
    sendDefaultPii: false, // no IPs, no customer phone numbers
    tracesSampleRate: 0.1,
  });
}

/** Tag errors with who hit them, without personal data: staff id, role and centre. */
export function identify(me: Me | null) {
  if (!dsn) return;
  Sentry.setUser(me ? { id: String(me.id) } : null);
  Sentry.setTag("role", me?.role ?? "signed_out");
  Sentry.setTag("centre", me?.centre.code ?? "none");
}

/**
 * Report failures worth a developer's attention: crashes, 5xx responses, and responses we
 * couldn't understand. Expected API errors (validation, 404, 409...) are shown to the user
 * and not reported, or Sentry would drown in "wrong password".
 */
export function reportError(error: unknown) {
  if (!dsn) return;
  if (error instanceof ApiError && error.status > 0 && error.status < 500) return;
  if (error instanceof ApiError && error.code === "NETWORK_ERROR") return; // flaky Wi-Fi
  Sentry.captureException(error);
}

export const wrap = Sentry.wrap;
