import { useState } from "react";
import { KeyboardAvoidingView, Modal, Platform, StyleSheet, Text, View } from "react-native";

import { Button, Field, InlineError } from "./ui";
import { colors, font, radius, space } from "./theme";

/** A small "why?" prompt (Alert.prompt is iOS-only, and most front desks use Android). */
export function ReasonDialog({
  visible,
  title,
  message,
  confirmLabel,
  destructive,
  loading,
  error,
  onConfirm,
  onClose,
}: {
  visible: boolean;
  title: string;
  message?: string;
  confirmLabel: string;
  destructive?: boolean;
  loading?: boolean;
  error?: unknown;
  onConfirm: (reason: string) => void;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const trimmed = reason.trim();

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.backdrop}
      >
        <View style={styles.sheet}>
          <Text style={font.heading}>{title}</Text>
          {message ? <Text style={[font.small, { marginVertical: space.sm }]}>{message}</Text> : null}
          <Field label="Reason" value={reason} onChangeText={setReason} autoFocus maxLength={255} />
          <InlineError error={error} />
          <View style={styles.actions}>
            <Button title="Back" variant="secondary" onPress={onClose} style={{ flex: 1 }} />
            <Button
              title={confirmLabel}
              variant={destructive ? "danger" : "primary"}
              onPress={() => onConfirm(trimmed)}
              disabled={!trimmed}
              loading={loading}
              style={{ flex: 1 }}
            />
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "rgba(16,24,40,0.45)", justifyContent: "center", padding: space.lg },
  sheet: { backgroundColor: colors.card, borderRadius: radius.md, padding: space.lg },
  actions: { flexDirection: "row", gap: space.md },
});
