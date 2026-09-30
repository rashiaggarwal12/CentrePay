import { router, useLocalSearchParams } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text } from "react-native";

import { useInvoice, useRequestRefund } from "@/api/hooks";
import { useMe } from "@/auth/AuthProvider";
import { colors, font, space } from "@/components/theme";
import { Button, Card, ErrorState, Field, InlineError, Loading, Row } from "@/components/ui";
import { formatDateTime } from "@/lib/dates";
import { formatINR, paiseToInput, parseRupees } from "@/lib/money";

export default function RefundRequestScreen() {
  const params = useLocalSearchParams<{ id: string; paymentId: string }>();
  const invoiceId = Number(params.id);
  const paymentId = Number(params.paymentId);
  const me = useMe();
  const invoice = useInvoice(invoiceId);
  const requestRefund = useRequestRefund(invoiceId);
  const [amountText, setAmountText] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  if (invoice.isPending) return <Loading />;
  if (invoice.isError) return <ErrorState error={invoice.error} onRetry={() => invoice.refetch()} />;
  const payment = invoice.data.payments.find((p) => p.id === paymentId);
  if (!payment) return <ErrorState error={new Error("Payment not found on this invoice.")} />;

  // The server says how much is still refundable (pending refunds already count).
  const max = payment.refundable_paise;
  const text = amountText ?? paiseToInput(max);
  const amount = parseRupees(text);
  const amountError =
    amount === null ? "Enter an amount like 500 or 500.50"
    : amount <= 0 ? "Must be more than ₹0"
    : amount > max ? `At most ${formatINR(max)} can be refunded`
    : undefined;

  const submit = () => {
    if (amountError || amount === null) return;
    requestRefund.mutate(
      { paymentId, amount_paise: amount, reason: reason.trim() },
      { onSuccess: () => router.back() },
    );
  };

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <Card>
        <Row label="Invoice" value={invoice.data.number ?? "Draft"} />
        <Row label="Customer" value={invoice.data.customer.name} />
        <Row label="Payment" value={`${payment.method.toUpperCase()} · ${formatINR(payment.amount_paise)}`} />
        <Row label="Paid on" value={formatDateTime(payment.captured_at ?? payment.created_at)} />
        <Row label="Refundable" value={formatINR(max)} strong />
      </Card>

      <Field
        label="Refund amount (₹)"
        value={text}
        onChangeText={setAmountText}
        keyboardType="decimal-pad"
        error={amountError}
      />
      <Field
        label="Reason"
        value={reason}
        onChangeText={setReason}
        placeholder="e.g. Session cancelled by therapist"
        maxLength={255}
        multiline
      />
      <Text style={styles.note}>
        {me.role === "manager"
          ? "You can approve this from the Approvals tab."
          : "A manager needs to approve this before the money is returned."}
        {payment.method === "cash" ? " Cash refunds are handed back at the desk once approved." : ""}
      </Text>
      <InlineError error={requestRefund.error} />
      <Button
        title="Request refund"
        onPress={submit}
        loading={requestRefund.isPending}
        disabled={!!amountError || !reason.trim()}
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: space.lg },
  note: { ...font.small, marginBottom: space.md },
});
