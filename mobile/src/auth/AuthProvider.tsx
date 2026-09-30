import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";

import { request, setSessionExpiredHandler } from "@/api/client";
import type { Me } from "@/api/types";
import { identify } from "@/lib/sentry";

import { clearTokens, loadTokens, saveTokens, type Tokens } from "./tokenStore";

type AuthState =
  | { status: "loading" }
  | { status: "signedOut"; reason?: string }
  | { status: "signedIn"; me: Me };

interface AuthContextValue {
  state: AuthState;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const queryClient = useQueryClient();

  useEffect(() => {
    identify(state.status === "signedIn" ? state.me : null);
  }, [state]);

  const signOut = useCallback(async () => {
    await clearTokens();
    queryClient.clear(); // never show one user's data to the next
    setState({ status: "signedOut" });
  }, [queryClient]);

  useEffect(() => {
    setSessionExpiredHandler(() => {
      queryClient.clear();
      setState({ status: "signedOut", reason: "Your session expired. Please sign in again." });
    });

    (async () => {
      const tokens = await loadTokens();
      if (!tokens) return setState({ status: "signedOut" });
      try {
        const me = await request<Me>("/api/v1/auth/me/");
        setState({ status: "signedIn", me });
      } catch {
        // Offline at launch or tokens rejected: ask to sign in rather than guess.
        setState({ status: "signedOut" });
      }
    })();
  }, [queryClient]);

  const signIn = useCallback(async (username: string, password: string) => {
    const tokens = await request<Tokens>("/api/v1/auth/token/", {
      method: "POST",
      body: { username, password },
      auth: false,
    });
    await saveTokens(tokens);
    try {
      const me = await request<Me>("/api/v1/auth/me/");
      setState({ status: "signedIn", me });
    } catch (error) {
      await clearTokens(); // e.g. a user with no staff profile: don't keep a half session
      throw error;
    }
  }, []);

  return <AuthContext.Provider value={{ state, signIn, signOut }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

/** The signed-in staff member. Only use under the (app) layout, which guarantees sign-in. */
export function useMe(): Me {
  const { state } = useAuth();
  if (state.status !== "signedIn") throw new Error("useMe used while signed out");
  return state.me;
}
