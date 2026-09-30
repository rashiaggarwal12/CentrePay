import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { request } from "./client";
import type {
  Customer,
  DailyCollection,
  InvoiceDetail,
  InvoiceListItem,
  InvoicePreview,
  InvoiceStatus,
  Paginated,
  Payment,
  PaymentAttempt,
  Refund,
  RefundStatus,
  Service,
  TimelineEntry,
} from "./types";

export const keys = {
  invoices: (filters: object) => ["invoices", filters] as const,
  invoice: (id: number) => ["invoice", id] as const,
  timeline: (id: number) => ["invoice", id, "timeline"] as const,
  customers: (search: string) => ["customers", search] as const,
  services: ["services"] as const,
  preview: (body: object) => ["preview", body] as const,
  refunds: (status?: string) => ["refunds", status ?? "all"] as const,
  dailyCollection: (date: string) => ["daily-collection", date] as const,
};

// --- Invoices ---------------------------------------------------------------------------------

export function useInvoices(filters: { date?: string; status?: InvoiceStatus }) {
  const params = new URLSearchParams();
  if (filters.date) params.set("date", filters.date);
  if (filters.status) params.set("status", filters.status);
  return useQuery({
    queryKey: keys.invoices(filters),
    queryFn: () => request<Paginated<InvoiceListItem>>(`/api/v1/invoices/?${params}`),
  });
}

export function useInvoice(
  id: number,
  options: { pollMs?: (invoice: InvoiceDetail | undefined) => number | false } = {},
) {
  const { pollMs } = options;
  return useQuery({
    queryKey: keys.invoice(id),
    queryFn: () => request<InvoiceDetail>(`/api/v1/invoices/${id}/`),
    // Decided from the latest data, so polling stops the moment the invoice is paid.
    refetchInterval: pollMs ? (query) => pollMs(query.state.data) : false,
  });
}

export function useTimeline(id: number) {
  return useQuery({
    queryKey: keys.timeline(id),
    queryFn: () => request<TimelineEntry[]>(`/api/v1/invoices/${id}/timeline/`),
  });
}

export interface LineInput {
  service: number;
  qty: number;
}

export function useInvoicePreview(lines: LineInput[], discountPaise: number) {
  const body = { items: lines, discount_paise: discountPaise };
  return useQuery({
    queryKey: keys.preview(body),
    queryFn: () =>
      request<InvoicePreview>("/api/v1/invoices/preview/", { method: "POST", body }),
    placeholderData: keepPreviousData, // keep showing the last total while recalculating
    staleTime: 60_000,
  });
}

/** After any change to an invoice, refresh everything that shows it. */
function useInvalidateInvoice() {
  const qc = useQueryClient();
  return (id: number) => {
    qc.invalidateQueries({ queryKey: ["invoice", id] });
    qc.invalidateQueries({ queryKey: ["invoices"] });
    qc.invalidateQueries({ queryKey: ["daily-collection"] });
  };
}

export function useCreateInvoice() {
  return useMutation({
    mutationFn: (body: { customer: number; items: LineInput[]; discount_paise: number }) =>
      request<InvoiceDetail>("/api/v1/invoices/", { method: "POST", body }),
  });
}

export function useIssueInvoice() {
  const invalidate = useInvalidateInvoice();
  return useMutation({
    mutationFn: ({ id, version }: { id: number; version?: number }) =>
      request<InvoiceDetail>(`/api/v1/invoices/${id}/issue/`, {
        method: "POST",
        body: version ? { version } : {},
      }),
    onSuccess: (invoice) => invalidate(invoice.id),
  });
}

export function useCancelInvoice() {
  const invalidate = useInvalidateInvoice();
  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) =>
      request<InvoiceDetail>(`/api/v1/invoices/${id}/cancel/`, { method: "POST", body: { reason } }),
    onSuccess: (invoice) => invalidate(invoice.id),
  });
}

export function useCollect(invoiceId: number) {
  const invalidate = useInvalidateInvoice();
  return useMutation({
    mutationFn: ({ idempotencyKey }: { idempotencyKey: string }) =>
      request<PaymentAttempt>(`/api/v1/invoices/${invoiceId}/collect/`, {
        method: "POST",
        body: {},
        idempotencyKey,
      }),
    onSuccess: () => invalidate(invoiceId),
  });
}

export function useCashPayment(invoiceId: number) {
  const invalidate = useInvalidateInvoice();
  return useMutation({
    mutationFn: ({ idempotencyKey }: { idempotencyKey: string }) =>
      request<Payment>(`/api/v1/invoices/${invoiceId}/cash/`, {
        method: "POST",
        body: {},
        idempotencyKey,
      }),
    onSuccess: () => invalidate(invoiceId),
  });
}

// --- Customers & services -----------------------------------------------------------------------

export function useCustomers(search: string) {
  return useQuery({
    queryKey: keys.customers(search),
    queryFn: () =>
      request<Paginated<Customer>>(`/api/v1/customers/?search=${encodeURIComponent(search)}`),
    placeholderData: keepPreviousData,
  });
}

export function useCreateCustomer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; phone: string; email?: string }) =>
      request<Customer>("/api/v1/customers/", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["customers"] }),
  });
}

export function useServices() {
  return useQuery({
    queryKey: keys.services,
    queryFn: () => request<Service[]>("/api/v1/services/"),
    staleTime: 5 * 60_000,
  });
}

// --- Refunds ------------------------------------------------------------------------------------

export function useRefunds(status?: RefundStatus, options: { enabled?: boolean } = {}) {
  return useQuery({
    enabled: options.enabled ?? true,
    queryKey: keys.refunds(status),
    queryFn: () =>
      request<Paginated<Refund>>(`/api/v1/refunds/${status ? `?status=${status}` : ""}`),
  });
}

export function useRequestRefund(invoiceId: number) {
  const invalidate = useInvalidateInvoice();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { paymentId: number; amount_paise: number; reason: string }) =>
      request<Refund>(`/api/v1/payments/${body.paymentId}/refunds/`, {
        method: "POST",
        body: { amount_paise: body.amount_paise, reason: body.reason },
      }),
    onSuccess: () => {
      invalidate(invoiceId);
      qc.invalidateQueries({ queryKey: ["refunds"] });
    },
  });
}

export function useDecideRefund() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, approve, note }: { id: number; approve: boolean; note?: string }) =>
      request<Refund>(`/api/v1/refunds/${id}/${approve ? "approve" : "reject"}/`, {
        method: "POST",
        body: approve ? {} : { note },
      }),
    onSuccess: (refund) => {
      qc.invalidateQueries({ queryKey: ["refunds"] });
      qc.invalidateQueries({ queryKey: ["invoice", refund.invoice] });
      qc.invalidateQueries({ queryKey: ["invoices"] });
      qc.invalidateQueries({ queryKey: ["daily-collection"] });
    },
  });
}

// --- Reports ------------------------------------------------------------------------------------

export function useDailyCollection(date: string) {
  return useQuery({
    queryKey: keys.dailyCollection(date),
    queryFn: () => request<DailyCollection>(`/api/v1/reports/daily-collection/?date=${date}`),
  });
}
