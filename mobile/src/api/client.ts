import { clearTokens, getTokens, saveTokens, type Tokens } from "@/auth/tokenStore";

import { apiBaseUrl } from "./config";

/** Every API error has the same envelope: {"error": {"code", "message", "details"}}. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH";
  body?: unknown;
  /** Sent as the Idempotency-Key header (collect, cash). */
  idempotencyKey?: string;
  /** Set false for login/refresh. */
  auth?: boolean;
  timeoutMs?: number;
}

let onSessionExpired: () => void = () => {};

/** The auth provider registers what to do when the refresh token is also rejected. */
export function setSessionExpiredHandler(handler: () => void) {
  onSessionExpired = handler;
}

// One refresh at a time: if five requests get a 401 together, they share one refresh call.
let refreshing: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const tokens = getTokens();
  if (!tokens) return false;
  refreshing ??= (async () => {
    try {
      const fresh = await request<Partial<Tokens>>("/api/v1/auth/token/refresh/", {
        method: "POST",
        body: { refresh: tokens.refresh },
        auth: false,
      });
      // Refresh tokens rotate: the server sends a new one each time.
      await saveTokens({ access: fresh.access!, refresh: fresh.refresh ?? tokens.refresh });
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

function toApiError(status: number, body: unknown): ApiError {
  const err = (body as { error?: { code?: string; message?: string; details?: unknown } })?.error;
  if (err?.code) return new ApiError(status, err.code, err.message ?? "Request failed", err.details);
  return new ApiError(status, "HTTP_" + status, `The server returned an error (${status}).`);
}

/** First field error from a VALIDATION_ERROR, e.g. "phone: Enter a valid mobile number." */
export function describeError(error: unknown): string {
  if (!(error instanceof ApiError)) return "Something went wrong. Please try again.";
  if (error.code === "VALIDATION_ERROR" && error.details && typeof error.details === "object") {
    const [field, messages] = Object.entries(error.details as Record<string, unknown>)[0] ?? [];
    const first = Array.isArray(messages) ? messages[0] : messages;
    if (typeof first === "string") {
      return field === "non_field_errors" || field === "detail" ? first : `${field}: ${first}`;
    }
  }
  return error.message;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, idempotencyKey, auth = true, timeoutMs = 15000 } = options;

  const send = async (): Promise<Response> => {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    const tokens = getTokens();
    if (auth && tokens) headers.Authorization = `Bearer ${tokens.access}`;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(`${apiBaseUrl()}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
    } catch {
      throw new ApiError(
        0,
        "NETWORK_ERROR",
        "Can't reach the server. Check the Wi-Fi connection and try again.",
      );
    } finally {
      clearTimeout(timer);
    }
  };

  let response = await send();
  if (response.status === 401 && auth && getTokens()) {
    if (await refreshTokens()) {
      response = await send();
    } else {
      await clearTokens();
      onSessionExpired();
    }
  }

  const text = await response.text();
  const data = text ? safeJson(text) : null;
  if (!response.ok) throw toApiError(response.status, data);
  return data as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}
