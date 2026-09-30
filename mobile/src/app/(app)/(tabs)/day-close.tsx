import { useState } from "react";
import { RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import { useDailyCollection } from "@/api/hooks";
import { colors, font, space } from "@/components/theme";
import { Button, Card, ErrorState, Loading, Money, Row, SectionTitle } from "@/components/ui";
import { addDays, formatDay, isoDate } from "@/lib/dates";
import { formatINR } from "@/lib/money";

const METHOD_LABELS: Record<string, string> = {
  upi: "UPI",
  card: "Card",
  cash: "Cash",
  netbanking: "Net banking",
  wallet: "Wallet",
};

export default function DayCloseScreen() {
  const today = isoDate();
  const [date, setDate] = useState(today);
  const report = useDailyCollection(date);

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={report.isRefetching} onRefresh={() => report.refetch()}
          colors={[colors.brand]} />
      }
    >
      <View style={styles.dateBar}>
        <Button title="‹" variant="secondary" onPress={() => setDate(addDays(date, -1))}
          style={styles.arrow} />
        <Text style={[font.heading, { flex: 1, textAlign: "center" }]}>
          {date === today ? "Today" : formatDay(date)}
        </Text>
        <Button title="›" variant="secondary" disabled={date >= today}
          onPress={() => setDate(addDays(date, 1))} style={styles.arrow} />
      </View>

      {report.isPending ? (
        <Loading />
      ) : report.isError ? (
        <ErrorState error={report.error} onRetry={() => report.refetch()} />
      ) : (
        <>
          <Card style={styles.hero}>
            <Text style={font.small}>Net collection</Text>
            <Money paise={report.data.net_collection_paise} style={styles.net} />
            <Text style={font.small}>
              {formatINR(report.data.collections.total_paise)} collected −{" "}
              {formatINR(report.data.refunds.total_paise)} refunded
            </Text>
          </Card>

          <SectionTitle>By payment method</SectionTitle>
          <Card>
            {Object.keys(report.data.collections.by_method).length === 0 ? (
              <Text style={font.small}>No payments on this day.</Text>
            ) : (
              Object.entries(report.data.collections.by_method).map(([method, m]) => (
                <Row
                  key={method}
                  label={`${METHOD_LABELS[method] ?? method} (${m.count})`}
                  value={formatINR(m.amount_paise)}
                />
              ))
            )}
            <View style={styles.divider} />
            <Row label="Total collected" value={formatINR(report.data.collections.total_paise)} strong />
          </Card>

          <SectionTitle>Refunds & fees</SectionTitle>
          <Card>
            <Row label={`Refunds (${report.data.refunds.count})`}
              value={formatINR(report.data.refunds.total_paise)} />
            <Row label="Gateway fees (incl. GST)" value={formatINR(report.data.gateway_fees_paise)} />
            {report.data.pending_refund_approvals ? (
              <Text style={[font.small, { color: colors.warn, marginTop: space.sm }]}>
                {report.data.pending_refund_approvals} refund request(s) still awaiting approval.
              </Text>
            ) : null}
          </Card>

          <SectionTitle>Invoices</SectionTitle>
          <Card>
            <Row label="Created" value={String(report.data.invoices.created)} />
            <Row label="Issued" value={String(report.data.invoices.issued)} />
            <Row label="Cancelled" value={String(report.data.invoices.cancelled)} />
          </Card>
        </>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: space.lg, paddingBottom: space.xl * 2 },
  dateBar: { flexDirection: "row", alignItems: "center", gap: space.md, marginBottom: space.md },
  arrow: { width: 48, paddingHorizontal: 0 },
  hero: { alignItems: "center", gap: space.xs },
  net: { fontSize: 32, fontWeight: "800" },
  divider: { height: 1, backgroundColor: colors.line, marginVertical: space.sm },
});
