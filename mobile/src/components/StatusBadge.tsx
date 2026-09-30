import { StyleSheet, Text, View } from "react-native";

import type { InvoiceStatus, PaymentAttemptStatus, RefundStatus } from "@/api/types";

import { colors, radius } from "./theme";

type Tone = "brand" | "ok" | "warn" | "bad" | "neutral";

const TONES: Record<Tone, { bg: string; fg: string }> = {
  brand: { bg: colors.brandSoft, fg: colors.brand },
  ok: { bg: colors.okSoft, fg: colors.ok },
  warn: { bg: colors.warnSoft, fg: colors.warn },
  bad: { bg: colors.badSoft, fg: colors.bad },
  neutral: { bg: colors.neutralSoft, fg: colors.muted },
};

const LABELS: Record<string, [string, Tone]> = {
  // invoices
  draft: ["Draft", "neutral"],
  issued: ["Unpaid", "warn"],
  partially_paid: ["Part paid", "warn"],
  paid: ["Paid", "ok"],
  partially_refunded: ["Part refunded", "brand"],
  refunded: ["Refunded", "neutral"],
  cancelled: ["Cancelled", "neutral"],
  // refunds
  requested: ["Awaiting approval", "warn"],
  approved: ["Approved", "brand"],
  processing: ["Processing", "brand"],
  processed: ["Refunded", "ok"],
  failed: ["Failed", "bad"],
  rejected: ["Rejected", "neutral"],
  // payment links
  pending: ["Creating link", "neutral"],
  created: ["Waiting for payment", "brand"],
  expired: ["Expired", "neutral"],
};

export function StatusBadge({ status }: { status: InvoiceStatus | RefundStatus | PaymentAttemptStatus }) {
  const [label, tone] = LABELS[status] ?? [status, "neutral"];
  const { bg, fg } = TONES[tone];
  return (
    <View style={[styles.badge, { backgroundColor: bg }]}>
      <Text style={[styles.text, { color: fg }]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: { paddingHorizontal: 10, paddingVertical: 3, borderRadius: radius.pill, alignSelf: "flex-start" },
  text: { fontSize: 13, fontWeight: "600" },
});
