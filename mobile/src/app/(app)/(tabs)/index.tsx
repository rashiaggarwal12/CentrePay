import { router } from "expo-router";
import { useState } from "react";
import { FlatList, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import { useInvoices } from "@/api/hooks";
import type { InvoiceListItem, InvoiceStatus } from "@/api/types";
import { useAuth, useMe } from "@/auth/AuthProvider";
import { StatusBadge } from "@/components/StatusBadge";
import { colors, font, space } from "@/components/theme";
import { Button, Chip, Empty, ErrorState, Loading, Money } from "@/components/ui";
import { formatDay, formatTime, isoDate } from "@/lib/dates";

const FILTERS: { label: string; status?: InvoiceStatus }[] = [
  { label: "All" },
  { label: "Unpaid", status: "issued" },
  { label: "Part paid", status: "partially_paid" },
  { label: "Paid", status: "paid" },
  { label: "Drafts", status: "draft" },
  { label: "Cancelled", status: "cancelled" },
];

export default function TodayScreen() {
  const me = useMe();
  const { signOut } = useAuth();
  const [status, setStatus] = useState<InvoiceStatus | undefined>();
  const today = isoDate();
  const invoices = useInvoices({ date: today, status });

  return (
    <View style={styles.screen}>
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={font.small}>
            {formatDay(today)} · {me.name || me.username}
          </Text>
        </View>
        <Button title="Sign out" variant="ghost" onPress={signOut} />
      </View>

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.filters}
        style={{ flexGrow: 0 }}
      >
        {FILTERS.map((f) => (
          <Chip
            key={f.label}
            label={f.label}
            selected={status === f.status}
            onPress={() => setStatus(f.status)}
          />
        ))}
      </ScrollView>

      {invoices.isPending ? (
        <Loading />
      ) : invoices.isError ? (
        <ErrorState error={invoices.error} onRetry={() => invoices.refetch()} />
      ) : (
        <FlatList
          data={invoices.data.results}
          keyExtractor={(item) => String(item.id)}
          renderItem={({ item }) => <InvoiceRow invoice={item} />}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={invoices.isRefetching}
              onRefresh={() => invoices.refetch()}
              colors={[colors.brand]}
            />
          }
          ListEmptyComponent={
            <Empty
              title={status ? "Nothing here" : "No invoices yet today"}
              hint="Start one from the Customers tab."
            />
          }
        />
      )}

      <View style={styles.footer}>
        <Button title="New invoice" onPress={() => router.push("/customers")} />
      </View>
    </View>
  );
}

function InvoiceRow({ invoice }: { invoice: InvoiceListItem }) {
  return (
    <Pressable
      accessibilityRole="button"
      onPress={() => router.push(`/invoice/${invoice.id}`)}
      style={({ pressed }) => [styles.row, pressed && { opacity: 0.7 }]}
    >
      <View style={{ flex: 1, gap: 2 }}>
        <Text style={font.heading} numberOfLines={1}>
          {invoice.customer.name}
        </Text>
        <Text style={font.small}>
          {invoice.number ?? "Draft"} · {formatTime(invoice.created_at)}
        </Text>
      </View>
      <View style={{ alignItems: "flex-end", gap: 4 }}>
        <Money paise={invoice.total_paise} style={{ fontWeight: "600" }} />
        <StatusBadge status={invoice.status} />
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: space.lg,
    paddingTop: space.sm,
  },
  filters: { gap: space.sm, paddingHorizontal: space.lg, paddingVertical: space.sm },
  list: { padding: space.lg, paddingTop: space.sm, flexGrow: 1 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.card,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.line,
    padding: space.lg,
    marginBottom: space.sm,
    gap: space.md,
  },
  footer: { padding: space.lg, borderTopWidth: 1, borderTopColor: colors.line, backgroundColor: colors.card },
});
