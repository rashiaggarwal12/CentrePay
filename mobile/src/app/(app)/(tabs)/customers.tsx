import { router } from "expo-router";
import { useEffect, useState } from "react";
import { FlatList, Pressable, StyleSheet, Text, View } from "react-native";

import { useCreateCustomer, useCustomers } from "@/api/hooks";
import type { Customer } from "@/api/types";
import { colors, font, space } from "@/components/theme";
import { Button, Card, Empty, ErrorState, Field, InlineError, Loading } from "@/components/ui";

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

function startInvoice(customer: Customer) {
  router.push({
    pathname: "/invoice/new",
    params: { customerId: String(customer.id), customerName: customer.name },
  });
}

export default function CustomersScreen() {
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const query = useDebounced(search.trim(), 300);
  const customers = useCustomers(query);

  if (creating) {
    return (
      <NewCustomerForm
        initialName={/\d/.test(search) ? "" : search}
        initialPhone={/\d/.test(search) ? search : ""}
        onCancel={() => setCreating(false)}
      />
    );
  }

  return (
    <View style={styles.screen}>
      <View style={styles.searchBar}>
        <Field
          label="Find customer"
          placeholder="Name or phone"
          value={search}
          onChangeText={setSearch}
          autoCorrect={false}
          returnKeyType="search"
        />
      </View>

      {customers.isPending ? (
        <Loading />
      ) : customers.isError ? (
        <ErrorState error={customers.error} onRetry={() => customers.refetch()} />
      ) : (
        <FlatList
          data={customers.data.results}
          keyExtractor={(c) => String(c.id)}
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={styles.list}
          renderItem={({ item }) => (
            <Pressable
              accessibilityRole="button"
              accessibilityHint="Starts a new invoice for this customer"
              onPress={() => startInvoice(item)}
              style={({ pressed }) => [styles.row, pressed && { opacity: 0.7 }]}
            >
              <Text style={font.heading}>{item.name}</Text>
              <Text style={font.small}>{item.phone}</Text>
            </Pressable>
          )}
          ListEmptyComponent={
            <Empty
              title={query ? `No customer matches “${query}”` : "No customers yet"}
              hint="Add them below."
            />
          }
        />
      )}

      <View style={styles.footer}>
        <Button title="+ New customer" variant="secondary" onPress={() => setCreating(true)} />
      </View>
    </View>
  );
}

function NewCustomerForm({
  initialName,
  initialPhone,
  onCancel,
}: {
  initialName: string;
  initialPhone: string;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initialName);
  const [phone, setPhone] = useState(initialPhone);
  const [email, setEmail] = useState("");
  const create = useCreateCustomer();

  // Field-level messages from the server's VALIDATION_ERROR details.
  const details = (create.error as { details?: Record<string, string[]> } | null)?.details ?? {};

  const submit = () =>
    create.mutate(
      { name: name.trim(), phone: phone.trim(), ...(email.trim() ? { email: email.trim() } : {}) },
      { onSuccess: startInvoice },
    );

  return (
    <View style={[styles.screen, { padding: space.lg }]}>
      <Card>
        <Text style={[font.heading, { marginBottom: space.md }]}>New customer</Text>
        <Field label="Name" value={name} onChangeText={setName} autoFocus error={details.name?.[0]} />
        <Field
          label="Mobile number"
          value={phone}
          onChangeText={setPhone}
          keyboardType="phone-pad"
          placeholder="98123 45678"
          error={details.phone?.[0]}
        />
        <Field
          label="Email (optional)"
          value={email}
          onChangeText={setEmail}
          keyboardType="email-address"
          autoCapitalize="none"
          error={details.email?.[0]}
        />
        {create.error && !Object.keys(details).length ? <InlineError error={create.error} /> : null}
        <View style={{ gap: space.sm }}>
          <Button
            title="Save and start invoice"
            onPress={submit}
            loading={create.isPending}
            disabled={!name.trim() || !phone.trim()}
          />
          <Button title="Cancel" variant="ghost" onPress={onCancel} />
        </View>
      </Card>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  searchBar: { paddingHorizontal: space.lg, paddingTop: space.md },
  list: { paddingHorizontal: space.lg, flexGrow: 1 },
  row: {
    backgroundColor: colors.card,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.line,
    padding: space.lg,
    marginBottom: space.sm,
    gap: 2,
  },
  footer: { padding: space.lg, borderTopWidth: 1, borderTopColor: colors.line, backgroundColor: colors.card },
});
