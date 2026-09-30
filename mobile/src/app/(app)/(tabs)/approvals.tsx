import { Redirect, router } from "expo-router";
import { useState } from "react";
import { FlatList, RefreshControl, StyleSheet, Text, View } from "react-native";

import { useDecideRefund, useRefunds } from "@/api/hooks";
import type { Refund } from "@/api/types";
import { useMe } from "@/auth/AuthProvider";
import { ReasonDialog } from "@/components/ReasonDialog";
import { colors, font, space } from "@/components/theme";
import { Button, Card, Empty, ErrorState, InlineError, Loading, Money } from "@/components/ui";
import { formatDateTime } from "@/lib/dates";

export default function ApprovalsScreen() {
  const me = useMe();
  const refunds = useRefunds("requested", { enabled: me.role === "manager" });
  const decide = useDecideRefund();
  const [rejecting, setRejecting] = useState<Refund | null>(null);

  if (me.role !== "manager") return <Redirect href="/" />; // the tab is hidden, but deep links exist
  if (refunds.isPending) return <Loading />;
  if (refunds.isError) return <ErrorState error={refunds.error} onRetry={() => refunds.refetch()} />;

  return (
    <View style={styles.screen}>
      <FlatList
        data={refunds.data.results}
        keyExtractor={(r) => String(r.id)}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl refreshing={refunds.isRefetching} onRefresh={() => refunds.refetch()}
            colors={[colors.brand]} />
        }
        ListHeaderComponent={<InlineError error={decide.error} />}
        ListEmptyComponent={<Empty title="Nothing to approve" hint="Refund requests from the desk appear here." />}
        renderItem={({ item }) => {
          const busy = decide.isPending && decide.variables?.id === item.id;
          return (
            <Card>
              <View style={styles.top}>
                <View style={{ flex: 1 }}>
                  <Text style={font.heading}>{item.reason}</Text>
                  <Text style={font.small}>
                    {item.invoice_number} · {item.payment_method.toUpperCase()} ·{" "}
                    {item.requested_by ?? "unknown"} · {formatDateTime(item.created_at)}
                  </Text>
                </View>
                <Money paise={item.amount_paise} style={styles.amount} />
              </View>
              <View style={styles.actions}>
                <Button title="Invoice" variant="ghost"
                  onPress={() => router.push(`/invoice/${item.invoice}`)} style={{ flex: 1 }} />
                <Button title="Reject" variant="danger" disabled={decide.isPending}
                  onPress={() => setRejecting(item)} style={{ flex: 1 }} />
                <Button title="Approve" loading={busy && decide.variables?.approve}
                  disabled={decide.isPending}
                  onPress={() => decide.mutate({ id: item.id, approve: true })} style={{ flex: 1.3 }} />
              </View>
            </Card>
          );
        }}
      />
      <ReasonDialog
        visible={rejecting !== null}
        title="Reject refund?"
        message="The note is shown to the staff member who asked."
        confirmLabel="Reject"
        destructive
        loading={decide.isPending}
        error={decide.error}
        onClose={() => setRejecting(null)}
        onConfirm={(note) =>
          rejecting &&
          decide.mutate(
            { id: rejecting.id, approve: false, note },
            { onSuccess: () => setRejecting(null) },
          )
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  list: { padding: space.lg, flexGrow: 1 },
  top: { flexDirection: "row", gap: space.md, marginBottom: space.md },
  amount: { fontSize: 18, fontWeight: "700" },
  actions: { flexDirection: "row", gap: space.sm },
});
