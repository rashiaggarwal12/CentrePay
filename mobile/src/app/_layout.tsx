import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useState } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { ApiError } from "@/api/client";
import { AuthProvider } from "@/auth/AuthProvider";
import { initSentry, reportError, wrap } from "@/lib/sentry";

initSentry();

function makeQueryClient() {
  return new QueryClient({
    queryCache: new QueryCache({ onError: reportError }),
    mutationCache: new MutationCache({ onError: reportError }),
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        // Retry network blips, but not 4xx: a 404 won't fix itself.
        retry: (count, error) =>
          count < 2 && (!(error instanceof ApiError) || error.status === 0 || error.status >= 500),
      },
      mutations: { retry: false }, // retries are explicit, with the same idempotency key
    },
  });
}

function RootLayout() {
  const [queryClient] = useState(makeQueryClient);
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <StatusBar style="dark" />
          <Stack screenOptions={{ headerShown: false }} />
        </AuthProvider>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}

export default wrap(RootLayout);
