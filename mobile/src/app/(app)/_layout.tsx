import { Redirect, Stack } from "expo-router";

import { useAuth } from "@/auth/AuthProvider";
import { colors } from "@/components/theme";
import { Loading } from "@/components/ui";

/** Everything under (app) requires a signed-in staff member. */
export default function AppLayout() {
  const { state } = useAuth();
  if (state.status === "loading") return <Loading />;
  if (state.status === "signedOut") return <Redirect href="/login" />;

  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: colors.card },
        headerTintColor: colors.brand,
        headerTitleStyle: { color: colors.text },
        contentStyle: { backgroundColor: colors.bg },
      }}
    >
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      <Stack.Screen name="invoice/new" options={{ title: "New invoice" }} />
      <Stack.Screen name="invoice/[id]/index" options={{ title: "Invoice" }} />
      <Stack.Screen name="invoice/[id]/collect" options={{ title: "Collect payment" }} />
      <Stack.Screen name="invoice/[id]/refund" options={{ title: "Request refund" }} />
    </Stack>
  );
}
