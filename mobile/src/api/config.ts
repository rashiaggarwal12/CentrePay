import Constants from "expo-constants";

/**
 * Where the Django API lives.
 *
 * - EXPO_PUBLIC_API_URL wins if set (e.g. the deployed backend for a release build).
 * - In development, the phone reaches the laptop at the same address it uses for the
 *   Expo dev server (hostUri, e.g. "192.168.1.5:8081"), so we default to that host on
 *   port 8000. Run Django with `runserver 0.0.0.0:8000` so it accepts LAN connections.
 */
export function apiBaseUrl(): string {
  const fromEnv = process.env.EXPO_PUBLIC_API_URL;
  if (fromEnv) return fromEnv.replace(/\/+$/, "");
  const hostUri = Constants.expoConfig?.hostUri;
  const host = hostUri ? hostUri.split(":")[0] : "localhost";
  return `http://${host}:8000`;
}
