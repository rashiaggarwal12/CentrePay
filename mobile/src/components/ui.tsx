import type { ReactNode } from "react";
import {
  ActivityIndicator,
  Pressable,
  type StyleProp,
  StyleSheet,
  Text,
  TextInput,
  type TextInputProps,
  View,
  type ViewStyle,
} from "react-native";

import { describeError } from "@/api/client";
import { formatINR } from "@/lib/money";

import { colors, font, radius, space } from "./theme";

// --- Buttons ------------------------------------------------------------------------------------

type Variant = "primary" | "secondary" | "danger" | "ghost";

/** Disabled while `loading`, so a second tap can't fire the same request twice. */
export function Button({
  title,
  onPress,
  variant = "primary",
  loading = false,
  disabled = false,
  style,
}: {
  title: string;
  onPress: () => void;
  variant?: Variant;
  loading?: boolean;
  disabled?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const inactive = disabled || loading;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: inactive, busy: loading }}
      onPress={onPress}
      disabled={inactive}
      style={({ pressed }) => [
        styles.button,
        styles[variant],
        inactive && styles.buttonDisabled,
        pressed && !inactive && styles.buttonPressed,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={variant === "primary" ? "#fff" : colors.brand} />
      ) : (
        <Text style={[styles.buttonText, variant === "primary" && styles.buttonTextPrimary,
          variant === "danger" && styles.buttonTextDanger]}>
          {title}
        </Text>
      )}
    </Pressable>
  );
}

// --- Layout -------------------------------------------------------------------------------------

export function Card({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

export function Row({ label, value, strong }: { label: string; value: ReactNode; strong?: boolean }) {
  return (
    <View style={styles.row}>
      <Text style={[styles.rowLabel, strong && styles.strong]}>{label}</Text>
      {typeof value === "string" ? (
        <Text style={[styles.rowValue, strong && styles.strong]}>{value}</Text>
      ) : (
        value
      )}
    </View>
  );
}

export function Money({ paise, style }: { paise: number; style?: object }) {
  return <Text style={[font.body, font.money, style]}>{formatINR(paise)}</Text>;
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <Text style={styles.sectionTitle}>{children}</Text>;
}

// --- States -------------------------------------------------------------------------------------

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <View style={styles.center}>
      <ActivityIndicator color={colors.brand} />
      <Text style={[font.small, { marginTop: space.sm }]}>{label}</Text>
    </View>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <View style={styles.center}>
      <Text style={[font.heading, { textAlign: "center" }]}>Couldn’t load this</Text>
      <Text style={[font.small, styles.centerText]}>{describeError(error)}</Text>
      {onRetry && <Button title="Try again" variant="secondary" onPress={onRetry} />}
    </View>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <View style={styles.center}>
      <Text style={[font.heading, { textAlign: "center" }]}>{title}</Text>
      {hint && <Text style={[font.small, styles.centerText]}>{hint}</Text>}
    </View>
  );
}

export function InlineError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <View style={styles.inlineError} accessibilityRole="alert">
      <Text style={{ color: colors.bad }}>{describeError(error)}</Text>
    </View>
  );
}

// --- Inputs -------------------------------------------------------------------------------------

export function Field({ label, error, ...props }: TextInputProps & { label: string; error?: string }) {
  return (
    <View style={{ marginBottom: space.md }}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput
        placeholderTextColor={colors.muted}
        style={[styles.input, error && { borderColor: colors.bad }]}
        {...props}
      />
      {error ? <Text style={styles.fieldError}>{error}</Text> : null}
    </View>
  );
}

// --- Chips --------------------------------------------------------------------------------------

export function Chip({ label, selected, onPress }: { label: string; selected: boolean; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected }}
      onPress={onPress}
      style={[styles.chip, selected && styles.chipSelected]}
    >
      <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    minHeight: 48,
    paddingHorizontal: space.lg,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
  },
  primary: { backgroundColor: colors.brand, borderColor: colors.brand },
  secondary: { backgroundColor: colors.card, borderColor: colors.line },
  danger: { backgroundColor: colors.card, borderColor: colors.badSoft },
  ghost: { backgroundColor: "transparent", borderColor: "transparent" },
  buttonDisabled: { opacity: 0.5 },
  buttonPressed: { opacity: 0.85 },
  buttonText: { fontSize: 16, fontWeight: "600", color: colors.brand },
  buttonTextPrimary: { color: "#fff" },
  buttonTextDanger: { color: colors.bad },
  card: {
    backgroundColor: colors.card,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.line,
    padding: space.lg,
    marginBottom: space.md,
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: space.xs,
    gap: space.md,
  },
  rowLabel: { ...font.body, color: colors.muted, flexShrink: 1 },
  rowValue: { ...font.body, ...font.money, textAlign: "right" },
  strong: { fontWeight: "700", color: colors.text },
  sectionTitle: {
    ...font.small,
    fontWeight: "600",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginTop: space.md,
    marginBottom: space.sm,
  },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: space.xl, gap: space.sm },
  centerText: { textAlign: "center", marginBottom: space.md },
  inlineError: {
    backgroundColor: colors.badSoft,
    borderRadius: radius.sm,
    padding: space.md,
    marginBottom: space.md,
  },
  fieldLabel: { ...font.small, fontWeight: "600", marginBottom: space.xs },
  input: {
    minHeight: 48,
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: radius.sm,
    paddingHorizontal: space.md,
    fontSize: 16,
    backgroundColor: colors.card,
    color: colors.text,
  },
  fieldError: { color: colors.bad, fontSize: 13, marginTop: space.xs },
  chip: {
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.card,
  },
  chipSelected: { backgroundColor: colors.brand, borderColor: colors.brand },
  chipText: { fontSize: 14, color: colors.text },
  chipTextSelected: { color: "#fff", fontWeight: "600" },
});
