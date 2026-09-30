import { router, useLocalSearchParams } from "expo-router";
import { useMemo, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { request } from "@/api/client";
import { type LineInput, useCreateInvoice, useInvoicePreview, useServices } from "@/api/hooks";
import type { InvoiceDetail, Service } from "@/api/types";
import { colors, font, radius, space } from "@/components/theme";
import {
  Button,
  Card,
  ErrorState,
  Field,
  InlineError,
  Loading,
  Money,
  Row,
  SectionTitle,
} from "@/components/ui";
import { formatBps, formatINR, parseRupees } from "@/lib/money";

export default function NewInvoiceScreen() {
  const { customerId, customerName } = useLocalSearchParams<{
    customerId: string;
    customerName?: string;
  }>();
  const services = useServices();
  const [qty, setQty] = useState<Record<number, number>>({});
  const [discountText, setDiscountText] = useState("");
  const [saving, setSaving] = useState<"draft" | "issue" | null>(null);
  const [saveError, setSaveError] = useState<unknown>(null);
  const create = useCreateInvoice();

  const lines: LineInput[] = useMemo(
    () =>
      Object.entries(qty)
        .filter(([, q]) => q > 0)
        .map(([service, q]) => ({ service: Number(service), qty: q })),
    [qty],
  );
  const discountPaise = discountText.trim() ? parseRupees(discountText) : 0;
  const discountInvalid = discountPaise === null;
  // The server prices everything (GST per line, discount spread across lines).
  const preview = useInvoicePreview(lines, discountPaise ?? 0);

  if (!customerId) return <ErrorState error={new Error("No customer selected")} />;
  if (services.isPending) return <Loading />;
  if (services.isError) return <ErrorState error={services.error} onRetry={() => services.refetch()} />;

  const change = (service: Service, delta: number) =>
    setQty((q) => ({ ...q, [service.id]: Math.max(0, Math.min(99, (q[service.id] ?? 0) + delta)) }));

  const save = async (andIssue: boolean) => {
    if (discountPaise === null) return;
    setSaving(andIssue ? "issue" : "draft");
    setSaveError(null);
    let invoice: InvoiceDetail;
    try {
      invoice = await create.mutateAsync({
        customer: Number(customerId),
        items: lines,
        discount_paise: discountPaise,
      });
    } catch (e) {
      setSaveError(e);
      setSaving(null);
      return;
    }
    if (!andIssue) {
      router.replace(`/invoice/${invoice.id}`);
      return;
    }
    try {
      await request<InvoiceDetail>(`/api/v1/invoices/${invoice.id}/issue/`, {
        method: "POST",
        body: { version: invoice.version },
      });
      router.replace(`/invoice/${invoice.id}/collect`);
    } catch {
      // The draft is saved; open it (it has its own Issue button) rather than letting a
      // retry here create a second draft.
      router.replace(`/invoice/${invoice.id}`);
    }
  };

  const total = preview.data?.total_paise ?? 0;

  return (
    <View style={styles.screen}>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text style={font.small}>Customer</Text>
        <Text style={[font.title, { marginBottom: space.md }]}>{customerName ?? `#${customerId}`}</Text>

        <SectionTitle>Services</SectionTitle>
        {services.data.map((service) => {
          const q = qty[service.id] ?? 0;
          return (
            <View key={service.id} style={[styles.service, q > 0 && styles.serviceSelected]}>
              <View style={{ flex: 1 }}>
                <Text style={font.body}>{service.name}</Text>
                <Text style={font.small}>
                  {formatINR(service.price_paise)}
                  {service.gst_rate_bps ? ` + ${formatBps(service.gst_rate_bps)} GST` : " · GST exempt"}
                </Text>
              </View>
              <View style={styles.stepper}>
                <StepButton label="−" disabled={q === 0} onPress={() => change(service, -1)}
                  a11y={`Remove one ${service.name}`} />
                <Text style={styles.qty} accessibilityLabel={`Quantity ${q}`}>{q}</Text>
                <StepButton label="+" onPress={() => change(service, 1)} a11y={`Add one ${service.name}`} />
              </View>
            </View>
          );
        })}

        <SectionTitle>Discount</SectionTitle>
        <Field
          label="Discount (₹)"
          value={discountText}
          onChangeText={setDiscountText}
          keyboardType="decimal-pad"
          placeholder="0"
          error={discountInvalid ? "Enter an amount like 150 or 150.50" : undefined}
        />

        <Card>
          <View style={styles.totalsHeader}>
            <Text style={font.heading}>Total</Text>
            {preview.isFetching ? <ActivityIndicator size="small" color={colors.brand} /> : null}
          </View>
          {preview.data ? (
            <>
              <Row label="Subtotal" value={formatINR(preview.data.subtotal_paise)} />
              {preview.data.discount_paise ? (
                <Row label="Discount" value={`− ${formatINR(preview.data.discount_paise)}`} />
              ) : null}
              <Row label="GST" value={formatINR(preview.data.tax_paise)} />
              <View style={styles.divider} />
              <Row label="To pay" value={<Money paise={total} style={styles.grand} />} strong />
            </>
          ) : null}
          {preview.isError ? <InlineError error={preview.error} /> : null}
        </Card>

        <InlineError error={saveError} />
      </ScrollView>

      <View style={styles.footer}>
        <Button
          title="Save draft"
          variant="secondary"
          onPress={() => save(false)}
          loading={saving === "draft"}
          disabled={!lines.length || discountInvalid || preview.isError || saving !== null}
          style={{ flex: 1 }}
        />
        <Button
          title={total ? `Issue & collect ${formatINR(total)}` : "Issue & collect"}
          onPress={() => save(true)}
          loading={saving === "issue"}
          disabled={!lines.length || discountInvalid || preview.isError || !total || saving !== null}
          style={{ flex: 2 }}
        />
      </View>
    </View>
  );
}

function StepButton({ label, onPress, disabled, a11y }: {
  label: string; onPress: () => void; disabled?: boolean; a11y: string;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={a11y}
      onPress={onPress}
      disabled={disabled}
      hitSlop={8}
      style={[styles.step, disabled && { opacity: 0.35 }]}
    >
      <Text style={styles.stepText}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: space.lg, paddingBottom: space.xl },
  service: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: radius.md,
    padding: space.md,
    marginBottom: space.sm,
    gap: space.md,
  },
  serviceSelected: { borderColor: colors.brand },
  stepper: { flexDirection: "row", alignItems: "center", gap: space.sm },
  step: {
    width: 40,
    height: 40,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: colors.line,
    alignItems: "center",
    justifyContent: "center",
  },
  stepText: { fontSize: 22, color: colors.brand, fontWeight: "600" },
  qty: { minWidth: 24, textAlign: "center", fontSize: 17, fontWeight: "600", ...font.money },
  totalsHeader: { flexDirection: "row", justifyContent: "space-between", marginBottom: space.sm },
  divider: { height: 1, backgroundColor: colors.line, marginVertical: space.sm },
  grand: { fontSize: 20, fontWeight: "700" },
  footer: {
    flexDirection: "row",
    gap: space.sm,
    padding: space.lg,
    borderTopWidth: 1,
    borderTopColor: colors.line,
    backgroundColor: colors.card,
  },
});
