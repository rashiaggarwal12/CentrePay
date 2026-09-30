import { router, Stack, useLocalSearchParams } from "expo-router";
import { useRef, useState } from "react";
import { Alert, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import { describeError } from "@/api/client";
import {
  useCancelInvoice,
  useCashPayment,
  useInvoice,
  useIssueInvoice,
  useTimeline,
} from "@/api/hooks";
import type { InvoiceDetail, Payment } from "@/api/types";
import { useMe } from "@/auth/AuthProvider";
import { ReasonDialog } from "@/components/ReasonDialog";
import { StatusBadge } from "@/components/StatusBadge";
import { colors, font, space } from "@/components/theme";
import { Button, Card, ErrorState, InlineError, Loading, Money, Row, SectionTitle } from "@/components/ui";
import { formatDateTime } from "@/lib/dates";
import { newIdempotencyKey } from "@/lib/idempotency";
import { formatBps, formatINR } from "@/lib/money";

const COLLECTABLE = new Set(["issued", "partially_paid"]);

export default function InvoiceDetailScreen() {
  const id = Number(useLocalSearchParams<{ id: string }>().id);
  const me = useMe();
  const invoice = useInvoice(id);
  const timeline = useTimeline(id);
  const issue = useIssueInvoice();
  const cancel = useCancelInvoice();
  const cash = useCashPayment(id);
  const [cancelOpen, setCancelOpen] = useState(false);
  // One key per "record cash" intent, reused if the request is retried.
  const [firstCashKey] = useState(newIdempotencyKey);
  const cashKey = useRef(firstCashKey);

  if (invoice.isPending) return <Loading />;
  if (invoice.isError) return <ErrorState error={invoice.error} onRetry={() => invoice.refetch()} />;
  const inv = invoice.data;

  const canCancel =
    inv.amount_paid_paise === 0 &&
    (inv.status === "draft" || (inv.status === "issued" && me.role === "manager"));

  const confirmCash = () =>
    Alert.alert(
      "Record cash payment",
      `Confirm you received ${formatINR(inv.amount_due_paise)} in cash.`,
      [
        { text: "Back", style: "cancel" },
        {
          text: "Received",
          onPress: () =>
            cash.mutate(
              { idempotencyKey: cashKey.current },
              { onSuccess: () => (cashKey.current = newIdempotencyKey()) },
            ),
        },
      ],
    );

  return (
    <View style={styles.screen}>
      <Stack.Screen options={{ title: inv.number ?? "Draft invoice" }} />
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            refreshing={invoice.isRefetching}
            onRefresh={() => {
              invoice.refetch();
              timeline.refetch();
            }}
            colors={[colors.brand]}
          />
        }
      >
        <Card>
          <View style={styles.headRow}>
            <View style={{ flex: 1 }}>
              <Text style={font.title}>{inv.customer.name}</Text>
              <Text style={font.small}>{inv.customer.phone}</Text>
            </View>
            <StatusBadge status={inv.status} />
          </View>
          <View style={styles.divider} />
          <Row label="Total" value={<Money paise={inv.total_paise} style={styles.big} />} strong />
          {inv.amount_paid_paise ? <Row label="Paid" value={formatINR(inv.amount_paid_paise)} /> : null}
          {inv.amount_refunded_paise ? (
            <Row label="Refunded" value={formatINR(inv.amount_refunded_paise)} />
          ) : null}
          {COLLECTABLE.has(inv.status) ? (
            <Row label="Due" value={<Money paise={inv.amount_due_paise} style={styles.due} />} strong />
          ) : null}
          {inv.cancel_reason ? <Text style={font.small}>Cancelled: {inv.cancel_reason}</Text> : null}
        </Card>

        <Actions
          inv={inv}
          onIssue={() => issue.mutate({ id, version: inv.version })}
          issuing={issue.isPending}
          onCash={confirmCash}
          cashing={cash.isPending}
        />
        <InlineError error={issue.error ?? cash.error} />

        <SectionTitle>Items</SectionTitle>
        <Card>
          {inv.items.map((item) => (
            <View key={item.id} style={styles.item}>
              <View style={{ flex: 1 }}>
                <Text style={font.body}>
                  {item.qty} × {item.description}
                </Text>
                <Text style={font.small}>
                  {formatINR(item.unit_price_paise)} each
                  {item.gst_rate_bps ? ` · GST ${formatBps(item.gst_rate_bps)}` : ""}
                </Text>
              </View>
              <Money paise={item.line_total_paise} />
            </View>
          ))}
          <View style={styles.divider} />
          <Row label="Subtotal" value={formatINR(inv.subtotal_paise)} />
          {inv.discount_paise ? <Row label="Discount" value={`− ${formatINR(inv.discount_paise)}`} /> : null}
          <Row label="GST" value={formatINR(inv.tax_paise)} />
        </Card>

        {inv.payments.length ? (
          <>
            <SectionTitle>Payments</SectionTitle>
            <Card>
              {inv.payments.map((p) => (
                <PaymentRow key={p.id} payment={p} invoiceId={inv.id} />
              ))}
            </Card>
          </>
        ) : null}

        {inv.refunds.length ? (
          <>
            <SectionTitle>Refunds</SectionTitle>
            <Card>
              {inv.refunds.map((r) => (
                <View key={r.id} style={styles.item}>
                  <View style={{ flex: 1, gap: 2 }}>
                    <Text style={font.body}>{r.reason}</Text>
                    <Text style={font.small}>
                      {r.requested_by ?? "Outside CentrePay"} · {formatDateTime(r.created_at)}
                      {r.decision_note ? ` · “${r.decision_note}”` : ""}
                    </Text>
                    <StatusBadge status={r.status} />
                  </View>
                  <Money paise={r.amount_paise} />
                </View>
              ))}
            </Card>
          </>
        ) : null}

        <SectionTitle>Timeline</SectionTitle>
        <Card>
          {timeline.isPending ? (
            <Text style={font.small}>Loading…</Text>
          ) : timeline.isError ? (
            <Text style={font.small}>{describeError(timeline.error)}</Text>
          ) : (
            timeline.data.map((e, i) => (
              <View key={`${e.action}-${i}`} style={styles.event}>
                <View style={styles.dot} />
                <View style={{ flex: 1 }}>
                  <Text style={font.body}>
                    {e.label}
                    {e.detail ? ` · ${e.detail}` : ""}
                    {e.amount_paise != null ? ` · ${formatINR(e.amount_paise)}` : ""}
                  </Text>
                  <Text style={font.small}>
                    {formatDateTime(e.at)} · {e.actor}
                  </Text>
                </View>
              </View>
            ))
          )}
        </Card>

        {canCancel ? (
          <Button title="Cancel invoice" variant="danger" onPress={() => setCancelOpen(true)} />
        ) : null}
      </ScrollView>

      <ReasonDialog
        visible={cancelOpen}
        title="Cancel invoice?"
        message={inv.status === "issued" ? "Live payment links will be cancelled too." : undefined}
        confirmLabel="Cancel invoice"
        destructive
        loading={cancel.isPending}
        error={cancel.error}
        onClose={() => setCancelOpen(false)}
        onConfirm={(reason) =>
          cancel.mutate({ id, reason }, { onSuccess: () => setCancelOpen(false) })
        }
      />
    </View>
  );
}

function Actions({ inv, onIssue, issuing, onCash, cashing }: {
  inv: InvoiceDetail; onIssue: () => void; issuing: boolean; onCash: () => void; cashing: boolean;
}) {
  if (inv.status === "draft") {
    return <Button title="Issue invoice" onPress={onIssue} loading={issuing} style={styles.action} />;
  }
  if (COLLECTABLE.has(inv.status)) {
    return (
      <View style={[styles.actionsRow, styles.action]}>
        <Button
          title="Cash"
          variant="secondary"
          onPress={onCash}
          loading={cashing}
          style={{ flex: 1 }}
        />
        <Button
          title={`Collect ${formatINR(inv.amount_due_paise)}`}
          onPress={() => router.push(`/invoice/${inv.id}/collect`)}
          style={{ flex: 2 }}
        />
      </View>
    );
  }
  return null;
}

function PaymentRow({ payment, invoiceId }: { payment: Payment; invoiceId: number }) {
  return (
    <View style={styles.item}>
      <View style={{ flex: 1, gap: 2 }}>
        <Text style={font.body}>
          {payment.method.toUpperCase() || "Payment"}
          {payment.status !== "captured" ? ` · ${payment.status}` : ""}
        </Text>
        <Text style={font.small}>
          {formatDateTime(payment.captured_at ?? payment.created_at)}
          {payment.error_description ? ` · ${payment.error_description}` : ""}
        </Text>
        {payment.refundable_paise > 0 ? (
          <Button
            title="Request refund"
            variant="ghost"
            onPress={() =>
              router.push({
                pathname: "/invoice/[id]/refund",
                params: { id: String(invoiceId), paymentId: String(payment.id) },
              })
            }
            style={styles.refundLink}
          />
        ) : null}
      </View>
      <Money paise={payment.amount_paise} />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: space.lg, paddingBottom: space.xl * 2 },
  headRow: { flexDirection: "row", alignItems: "flex-start", gap: space.md },
  divider: { height: 1, backgroundColor: colors.line, marginVertical: space.sm },
  big: { fontSize: 20, fontWeight: "700" },
  due: { fontSize: 18, fontWeight: "700", color: colors.warn },
  action: { marginBottom: space.md },
  actionsRow: { flexDirection: "row", gap: space.sm },
  item: { flexDirection: "row", alignItems: "flex-start", paddingVertical: space.sm, gap: space.md },
  refundLink: { alignSelf: "flex-start", minHeight: 36, paddingHorizontal: 0 },
  event: { flexDirection: "row", gap: space.md, paddingVertical: space.xs },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.brand, marginTop: 7 },
});
