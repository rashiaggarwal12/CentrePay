import { Ionicons } from "@expo/vector-icons";
import * as Clipboard from "expo-clipboard";
import * as Haptics from "expo-haptics";
import { router, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { Animated, ScrollView, Share, StyleSheet, Text, View } from "react-native";
import QRCode from "react-native-qrcode-svg";

import { ApiError } from "@/api/client";
import { useCollect, useInvoice } from "@/api/hooks";
import type { PaymentAttempt } from "@/api/types";
import { colors, font, radius, space } from "@/components/theme";
import { Button, Card, ErrorState, InlineError, Loading, Money } from "@/components/ui";
import { formatCountdown, secondsUntil } from "@/lib/dates";
import { newIdempotencyKey } from "@/lib/idempotency";
import { formatINR } from "@/lib/money";

const POLL_MS = 3000;
const DONE = new Set(["paid", "partially_refunded", "refunded"]);

export default function CollectScreen() {
  const id = Number(useLocalSearchParams<{ id: string }>().id);
  const collect = useCollect(id);
  const [attempt, setAttempt] = useState<PaymentAttempt | null>(null);
  // One key per "show me a QR" intent. Retries after a network error reuse it (the server
  // returns the same link); only asking for a *new* link makes a new key.
  const [firstKey] = useState(newIdempotencyKey);
  const keyRef = useRef(firstKey);

  const invoice = useInvoice(id, {
    pollMs: (inv) => (inv && (DONE.has(inv.status) || inv.status === "cancelled") ? false : POLL_MS),
  });

  const { mutate } = collect;
  const requestLink = useCallback(
    (fresh: boolean) => {
      if (fresh) keyRef.current = newIdempotencyKey();
      mutate({ idempotencyKey: keyRef.current }, { onSuccess: setAttempt });
    },
    [mutate],
  );

  // Our first request is still talking to the gateway: wait a moment and ask again with
  // the same key (the server then returns the link that request created).
  const inProgress =
    collect.error instanceof ApiError && collect.error.code === "REQUEST_IN_PROGRESS";
  useEffect(() => {
    if (!inProgress) return;
    const t = setTimeout(() => requestLink(false), 1500);
    return () => clearTimeout(t);
  }, [inProgress, requestLink]);

  // Ask for a link once the invoice is known to be collectable.
  const status = invoice.data?.status;
  useEffect(() => {
    if (!attempt && !collect.isPending && !collect.isError &&
        (status === "issued" || status === "partially_paid")) {
      requestLink(false);
    }
  }, [status, attempt, collect.isPending, collect.isError, requestLink]);

  if (invoice.isPending) return <Loading />;
  if (invoice.isError) return <ErrorState error={invoice.error} onRetry={() => invoice.refetch()} />;
  const inv = invoice.data;

  if (DONE.has(inv.status)) {
    return <PaidView amountPaise={inv.amount_paid_paise} invoiceId={id} overpaid={inv.amount_paid_paise > inv.total_paise} />;
  }
  if (inv.status === "cancelled" || inv.status === "draft") {
    return <ErrorState error={new ApiError(409, "NOT_COLLECTABLE", `This invoice is ${inv.status}.`)} />;
  }

  // The server's view of our link (updated by webhooks) beats our local copy.
  const live = inv.payment_attempts.find((a) => a.id === attempt?.id) ?? attempt;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={[font.small, { textAlign: "center" }]}>{inv.customer.name} pays</Text>
      <Money paise={live?.amount_paise ?? inv.amount_due_paise} style={styles.amount} />
      {inv.status === "partially_paid" ? (
        <Text style={styles.partial}>
          {formatINR(inv.amount_paid_paise)} received so far · {formatINR(inv.amount_due_paise)} still due
        </Text>
      ) : null}

      {(collect.isPending || inProgress) && !live ? (
        <Card style={styles.qrCard}>
          <Loading label="Creating payment link…" />
        </Card>
      ) : collect.isError && !inProgress && !live ? (
        <Card>
          <InlineError error={collect.error} />
          <Button title="Try again" onPress={() => requestLink(false)} />
        </Card>
      ) : live?.status === "paid" ? (
        // This link was paid, but not for the full amount (e.g. a split payment).
        <Card style={styles.qrCard}>
          <Ionicons name="checkmark-circle-outline" size={40} color={colors.ok} />
          <Text style={[font.heading, { marginTop: space.sm }]}>
            {formatINR(inv.amount_due_paise)} is still due
          </Text>
          <Button
            title="Create link for the rest"
            onPress={() => requestLink(true)}
            loading={collect.isPending}
            style={{ alignSelf: "stretch", marginTop: space.md }}
          />
        </Card>
      ) : live ? (
        <LinkCard attempt={live} onNewLink={() => requestLink(true)} renewing={collect.isPending} />
      ) : null}

      <Text style={[font.small, styles.hint]}>
        This screen updates by itself when the payment arrives.
      </Text>
      <Button title="Back to invoice" variant="ghost" onPress={() => router.back()} />
    </ScrollView>
  );
}

function LinkCard({ attempt, onNewLink, renewing }: {
  attempt: PaymentAttempt; onNewLink: () => void; renewing: boolean;
}) {
  const remaining = useCountdown(attempt.expires_at);
  const expired =
    attempt.status === "expired" || attempt.status === "cancelled" || (remaining !== null && remaining <= 0);
  const [copied, setCopied] = useState(false);

  if (expired) {
    return (
      <Card style={styles.qrCard}>
        <Ionicons name="time-outline" size={40} color={colors.muted} />
        <Text style={[font.heading, { marginTop: space.sm }]}>
          This link {attempt.status === "cancelled" ? "was cancelled" : "has expired"}
        </Text>
        <Text style={[font.small, styles.hint]}>
          The customer can’t pay it any more. Create a new one to try again.
        </Text>
        <Button title="Create new link" onPress={onNewLink} loading={renewing} />
      </Card>
    );
  }

  return (
    <Card style={styles.qrCard}>
      <View style={styles.qrFrame} accessibilityLabel="Payment QR code">
        <QRCode value={attempt.short_url} size={220} />
      </View>
      <Text style={styles.scan}>Scan with any UPI app or phone camera</Text>
      <Text selectable style={styles.link} numberOfLines={1}>{attempt.short_url}</Text>
      {remaining !== null ? (
        <Text style={[font.small, remaining < 120 && { color: colors.warn }]}>
          Link expires in {formatCountdown(remaining)}
        </Text>
      ) : null}
      <View style={styles.linkActions}>
        <Button
          title={copied ? "Copied" : "Copy link"}
          variant="secondary"
          onPress={async () => {
            await Clipboard.setStringAsync(attempt.short_url);
            setCopied(true);
          }}
          style={{ flex: 1 }}
        />
        <Button
          title="Share"
          variant="secondary"
          onPress={() => Share.share({ message: `Pay ${formatINR(attempt.amount_paise)}: ${attempt.short_url}` })}
          style={{ flex: 1 }}
        />
      </View>
    </Card>
  );
}

function useCountdown(expiresAt: string | null): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!expiresAt) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [expiresAt]);
  return expiresAt ? secondsUntil(expiresAt, now) : null;
}

function PaidView({ amountPaise, invoiceId, overpaid }: {
  amountPaise: number; invoiceId: number; overpaid: boolean;
}) {
  const [scale] = useState(() => new Animated.Value(0));
  useEffect(() => {
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    Animated.spring(scale, { toValue: 1, friction: 5, tension: 80, useNativeDriver: true }).start();
  }, [scale]);

  return (
    <View style={[styles.screen, styles.paid]}>
      <Animated.View style={[styles.tick, { transform: [{ scale }] }]}>
        <Ionicons name="checkmark" size={72} color="#fff" />
      </Animated.View>
      <Text style={[font.title, { marginTop: space.lg }]}>Payment received</Text>
      <Money paise={amountPaise} style={styles.amount} />
      {overpaid ? (
        <Text style={styles.partial}>
          The customer paid more than the total. A manager has been alerted to refund the difference.
        </Text>
      ) : null}
      <View style={{ alignSelf: "stretch", gap: space.sm, marginTop: space.xl }}>
        <Button title="Done" onPress={() => router.dismissTo("/")} />
        <Button title="View invoice" variant="secondary" onPress={() => router.replace(`/invoice/${invoiceId}`)} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: space.lg, paddingBottom: space.xl * 2 },
  amount: { fontSize: 34, fontWeight: "800", textAlign: "center", marginVertical: space.sm },
  partial: { ...font.small, textAlign: "center", color: colors.warn, marginBottom: space.md },
  qrCard: { alignItems: "center", paddingVertical: space.xl, minHeight: 320, justifyContent: "center" },
  qrFrame: { padding: space.md, backgroundColor: "#fff", borderRadius: radius.md },
  scan: { ...font.body, fontWeight: "600", marginTop: space.md },
  link: { ...font.small, color: colors.brand, marginVertical: space.xs, maxWidth: "100%" },
  linkActions: { flexDirection: "row", gap: space.sm, alignSelf: "stretch", marginTop: space.md },
  hint: { textAlign: "center", marginVertical: space.md },
  paid: { alignItems: "center", justifyContent: "center", padding: space.xl },
  tick: {
    width: 120,
    height: 120,
    borderRadius: 60,
    backgroundColor: colors.ok,
    alignItems: "center",
    justifyContent: "center",
  },
});
