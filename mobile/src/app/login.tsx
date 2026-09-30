import { Redirect } from "expo-router";
import { useState } from "react";
import { KeyboardAvoidingView, Platform, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { ApiError } from "@/api/client";
import { apiBaseUrl } from "@/api/config";
import { useAuth } from "@/auth/AuthProvider";
import { colors, font, space } from "@/components/theme";
import { Button, Field, InlineError, Loading } from "@/components/ui";

export default function LoginScreen() {
  const { state, signIn } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (state.status === "loading") return <Loading />;
  if (state.status === "signedIn") return <Redirect href="/" />;

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await signIn(username.trim(), password);
    } catch (e) {
      setError(
        e instanceof ApiError && e.code === "AUTHENTICATION_FAILED"
          ? new ApiError(401, e.code, "Wrong username or password.")
          : e,
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.container}
      >
        <View style={styles.brand}>
          <Text style={styles.logo}>CentrePay</Text>
          <Text style={font.small}>Front desk billing</Text>
        </View>

        {state.reason ? <Text style={styles.notice}>{state.reason}</Text> : null}

        <Field
          label="Username"
          value={username}
          onChangeText={setUsername}
          autoCapitalize="none"
          autoCorrect={false}
          autoComplete="username"
          textContentType="username"
          returnKeyType="next"
        />
        <Field
          label="Password"
          value={password}
          onChangeText={setPassword}
          secureTextEntry
          autoComplete="current-password"
          textContentType="password"
          returnKeyType="go"
          onSubmitEditing={submit}
        />
        <InlineError error={error} />
        <Button
          title="Sign in"
          onPress={submit}
          loading={submitting}
          disabled={!username.trim() || !password}
        />
        {__DEV__ ? <Text style={styles.server}>Server: {apiBaseUrl()}</Text> : null}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  container: { flex: 1, justifyContent: "center", padding: space.xl },
  brand: { alignItems: "center", marginBottom: space.xl },
  logo: { fontSize: 32, fontWeight: "800", color: colors.brand },
  notice: {
    backgroundColor: colors.warnSoft,
    color: colors.warn,
    padding: space.md,
    borderRadius: 8,
    marginBottom: space.md,
  },
  server: { ...font.small, textAlign: "center", marginTop: space.lg },
});
