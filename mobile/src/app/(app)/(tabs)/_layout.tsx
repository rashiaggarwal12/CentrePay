import { Ionicons } from "@expo/vector-icons";
import { Tabs } from "expo-router";
import type { ComponentProps } from "react";
import type { ColorValue } from "react-native";

import { useRefunds } from "@/api/hooks";
import { useMe } from "@/auth/AuthProvider";
import { colors } from "@/components/theme";

type IconName = ComponentProps<typeof Ionicons>["name"];

function icon(name: IconName) {
  return function TabBarIcon({ color, size }: { color: ColorValue; size: number }) {
    return <Ionicons name={name} color={color as string} size={size} />;
  };
}

export default function TabsLayout() {
  const me = useMe();
  const isManager = me.role === "manager";
  // Badge the approvals tab with how many refunds are waiting (managers only).
  const pending = useRefunds("requested", { enabled: isManager });
  const pendingCount = isManager ? (pending.data?.count ?? 0) : 0;

  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: colors.brand,
        tabBarInactiveTintColor: colors.muted,
        headerStyle: { backgroundColor: colors.card },
        headerTitleStyle: { color: colors.text },
        sceneStyle: { backgroundColor: colors.bg },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{ title: "Today", headerTitle: me.centre.name, tabBarIcon: icon("receipt-outline") }}
      />
      <Tabs.Screen
        name="customers"
        options={{ title: "Customers", tabBarIcon: icon("people-outline") }}
      />
      <Tabs.Screen
        name="approvals"
        options={{
          title: "Approvals",
          tabBarIcon: icon("checkmark-done-outline"),
          tabBarBadge: pendingCount > 0 ? pendingCount : undefined,
          href: isManager ? undefined : null, // hidden for front desk
        }}
      />
      <Tabs.Screen
        name="day-close"
        options={{ title: "Day close", tabBarIcon: icon("stats-chart-outline") }}
      />
    </Tabs>
  );
}
