// Shapes returned by the CentrePay API (backend/apps/*/serializers.py).

export type Role = "front_desk" | "manager";

export interface Me {
  id: number;
  username: string;
  name: string;
  role: Role;
  centre: { id: number; name: string; code: string };
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface Customer {
  id: number;
  name: string;
  phone: string;
  email: string;
  created_at: string;
}

export interface Service {
  id: number;
  name: string;
  price_paise: number;
  gst_rate_bps: number;
  is_active: boolean;
}

export type InvoiceStatus =
  | "draft"
  | "issued"
  | "partially_paid"
  | "paid"
  | "partially_refunded"
  | "refunded"
  | "cancelled";

export interface InvoiceListItem {
  id: number;
  number: string | null;
  status: InvoiceStatus;
  customer: { id: number; name: string; phone: string };
  total_paise: number;
  amount_paid_paise: number;
  amount_refunded_paise: number;
  amount_due_paise: number;
  created_at: string;
  issued_at: string | null;
  version: number;
}

export interface InvoiceItem {
  id: number;
  service: number;
  description: string;
  qty: number;
  unit_price_paise: number;
  gst_rate_bps: number;
  line_total_paise: number;
  discount_paise: number;
  tax_paise: number;
}

export type PaymentAttemptStatus =
  | "pending"
  | "created"
  | "paid"
  | "expired"
  | "cancelled"
  | "failed";

export interface PaymentAttempt {
  id: number;
  amount_paise: number;
  status: PaymentAttemptStatus;
  short_url: string;
  gateway_link_id: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface Payment {
  id: number;
  gateway_payment_id: string;
  amount_paise: number;
  method: string;
  status: "authorized" | "captured" | "failed";
  captured_at: string | null;
  error_description: string;
  refundable_paise: number;
  created_at: string;
}

export type RefundStatus =
  | "requested"
  | "approved"
  | "processing"
  | "processed"
  | "failed"
  | "rejected";

export interface Refund {
  id: number;
  invoice: number;
  invoice_number: string | null;
  payment: number;
  payment_method: string;
  amount_paise: number;
  reason: string;
  status: RefundStatus;
  requested_by: string | null;
  approved_by: string | null;
  decision_note: string;
  decided_at: string | null;
  processed_at: string | null;
  last_error: string;
  created_at: string;
}

export interface InvoiceDetail extends InvoiceListItem {
  items: InvoiceItem[];
  payments: Payment[];
  payment_attempts: PaymentAttempt[];
  refunds: Refund[];
  subtotal_paise: number;
  discount_paise: number;
  tax_paise: number;
  created_by: string;
  cancelled_at: string | null;
  cancel_reason: string;
  updated_at: string;
}

export interface PreviewLine {
  service: number;
  description: string;
  qty: number;
  unit_price_paise: number;
  gst_rate_bps: number;
  line_total_paise: number;
  discount_paise: number;
  tax_paise: number;
}

export interface InvoicePreview {
  subtotal_paise: number;
  discount_paise: number;
  tax_paise: number;
  total_paise: number;
  lines: PreviewLine[];
}

export interface TimelineEntry {
  at: string;
  action: string;
  label: string;
  detail: string;
  amount_paise: number | null;
  actor: string;
}

export interface DailyCollection {
  date: string;
  centre: { id: number; name: string; code: string };
  collections: {
    by_method: Record<string, { count: number; amount_paise: number; gateway_fees_paise: number }>;
    count: number;
    total_paise: number;
  };
  refunds: { count: number; total_paise: number };
  net_collection_paise: number;
  gateway_fees_paise: number;
  invoices: { created: number; issued: number; cancelled: number };
  pending_refund_approvals: number;
}
