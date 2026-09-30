import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

export interface Tokens {
  access: string;
  refresh: string;
}

const KEY = "centrepay.tokens";

// Kept in memory for every request; persisted in the OS keystore (SecureStore) so a
// restart doesn't log staff out. Never in AsyncStorage: that's plain text on disk.
// SecureStore doesn't exist on web; the web build is only for development/testing, so
// there it falls back to sessionStorage (cleared when the tab closes).
const storage =
  Platform.OS === "web"
    ? {
        get: async () => globalThis.sessionStorage?.getItem(KEY) ?? null,
        set: async (v: string) => globalThis.sessionStorage?.setItem(KEY, v),
        del: async () => globalThis.sessionStorage?.removeItem(KEY),
      }
    : {
        get: () => SecureStore.getItemAsync(KEY),
        set: (v: string) => SecureStore.setItemAsync(KEY, v),
        del: () => SecureStore.deleteItemAsync(KEY),
      };

let current: Tokens | null = null;

export function getTokens(): Tokens | null {
  return current;
}

export async function loadTokens(): Promise<Tokens | null> {
  const raw = await storage.get();
  current = raw ? (JSON.parse(raw) as Tokens) : null;
  return current;
}

export async function saveTokens(tokens: Tokens): Promise<void> {
  current = tokens;
  await storage.set(JSON.stringify(tokens));
}

export async function clearTokens(): Promise<void> {
  current = null;
  await storage.del();
}
