export const colors = {
  bg: "#F6F7F9",
  card: "#FFFFFF",
  text: "#1A1D21",
  muted: "#667085",
  line: "#E4E7EC",
  brand: "#2F6FED",
  brandSoft: "#EEF4FF",
  ok: "#12805C",
  okSoft: "#E7F6EE",
  warn: "#B54708",
  warnSoft: "#FEF0C7",
  bad: "#C4320A",
  badSoft: "#FEE4E2",
  neutralSoft: "#F2F4F7",
};

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 };
export const radius = { sm: 8, md: 12, pill: 999 };

export const font = {
  title: { fontSize: 22, fontWeight: "700" as const, color: colors.text },
  heading: { fontSize: 17, fontWeight: "600" as const, color: colors.text },
  body: { fontSize: 16, color: colors.text },
  small: { fontSize: 14, color: colors.muted },
  money: { fontVariant: ["tabular-nums" as const] },
};
