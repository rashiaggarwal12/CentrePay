import { ApiError, describeError, request, setSessionExpiredHandler } from "@/api/client";
import { getTokens, saveTokens } from "@/auth/tokenStore";

jest.mock("expo-secure-store", () => {
  const store = new Map<string, string>();
  return {
    getItemAsync: jest.fn(async (k: string) => store.get(k) ?? null),
    setItemAsync: jest.fn(async (k: string, v: string) => void store.set(k, v)),
    deleteItemAsync: jest.fn(async (k: string) => void store.delete(k)),
  };
});

jest.mock("expo-constants", () => ({ expoConfig: { hostUri: "192.168.1.5:8081" } }));

type Call = { url: string; init: RequestInit };
let calls: Call[];

function respond(...responses: { status: number; body?: unknown }[]) {
  const queue = [...responses];
  global.fetch = jest.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    const next = queue.shift();
    if (!next) throw new Error("unexpected fetch");
    return new Response(next.body === undefined ? "" : JSON.stringify(next.body), {
      status: next.status,
    });
  }) as typeof fetch;
}

function header(call: Call, name: string): string | undefined {
  return (call.init.headers as Record<string, string>)[name];
}

beforeEach(async () => {
  calls = [];
  await saveTokens({ access: "old-access", refresh: "old-refresh" });
});

it("targets the laptop running the Expo dev server, on Django's port", async () => {
  respond({ status: 200, body: [] });
  await request("/api/v1/services/");
  expect(calls[0].url).toBe("http://192.168.1.5:8000/api/v1/services/");
  expect(header(calls[0], "Authorization")).toBe("Bearer old-access");
});

it("sends the idempotency key", async () => {
  respond({ status: 201, body: { id: 1 } });
  await request("/api/v1/invoices/1/collect/", { method: "POST", body: {}, idempotencyKey: "k-1" });
  expect(header(calls[0], "Idempotency-Key")).toBe("k-1");
  expect(calls[0].init.method).toBe("POST");
});

it("turns the error envelope into an ApiError", async () => {
  respond({
    status: 409,
    body: { error: { code: "INVALID_TRANSITION", message: "Cannot refund a draft." } },
  });
  await expect(request("/x/")).rejects.toMatchObject({
    status: 409,
    code: "INVALID_TRANSITION",
    message: "Cannot refund a draft.",
  });
});

it("refreshes an expired access token once and retries", async () => {
  respond(
    { status: 401, body: { error: { code: "NOT_AUTHENTICATED", message: "expired" } } },
    { status: 200, body: { access: "new-access", refresh: "new-refresh" } },
    { status: 200, body: { ok: true } },
  );
  await expect(request("/api/v1/invoices/")).resolves.toEqual({ ok: true });
  expect(calls.map((c) => c.url.replace("http://192.168.1.5:8000", ""))).toEqual([
    "/api/v1/invoices/",
    "/api/v1/auth/token/refresh/",
    "/api/v1/invoices/",
  ]);
  expect(header(calls[2], "Authorization")).toBe("Bearer new-access");
  expect(getTokens()).toEqual({ access: "new-access", refresh: "new-refresh" });
});

it("signs out when the refresh token is rejected too", async () => {
  const expired = jest.fn();
  setSessionExpiredHandler(expired);
  respond(
    { status: 401, body: { error: { code: "NOT_AUTHENTICATED", message: "expired" } } },
    { status: 401, body: { error: { code: "AUTHENTICATION_FAILED", message: "bad refresh" } } },
  );
  await expect(request("/api/v1/invoices/")).rejects.toBeInstanceOf(ApiError);
  expect(expired).toHaveBeenCalledTimes(1);
  expect(getTokens()).toBeNull();
});

it("shares one refresh between concurrent 401s", async () => {
  let refreshCalls = 0;
  global.fetch = jest.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const auth = (init?.headers as Record<string, string>)?.Authorization;
    if (String(url).endsWith("/token/refresh/")) {
      refreshCalls += 1;
      await new Promise((r) => setTimeout(r, 10));
      return new Response(JSON.stringify({ access: "fresh", refresh: "r2" }), { status: 200 });
    }
    return auth === "Bearer fresh"
      ? new Response(JSON.stringify({ ok: true }), { status: 200 })
      : new Response("", { status: 401 });
  }) as typeof fetch;

  await Promise.all([request("/a/"), request("/b/"), request("/c/")]);
  expect(refreshCalls).toBe(1);
});

it("reports network failures clearly", async () => {
  global.fetch = jest.fn(async () => {
    throw new TypeError("Network request failed");
  }) as typeof fetch;
  await expect(request("/x/")).rejects.toMatchObject({ code: "NETWORK_ERROR", status: 0 });
});

it("describes validation errors by field", () => {
  const err = new ApiError(400, "VALIDATION_ERROR", "Invalid input.", {
    phone: ["Enter a valid 10-digit Indian mobile number."],
  });
  expect(describeError(err)).toBe("phone: Enter a valid 10-digit Indian mobile number.");
  expect(describeError(new Error("boom"))).toBe("Something went wrong. Please try again.");
});
