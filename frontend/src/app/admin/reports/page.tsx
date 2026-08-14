"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { api } from "@/lib/api";

const nav = [
  { href: "/admin", label: "داشبورد" },
  { href: "/admin/users", label: "کاربران" },
  { href: "/admin/organizers", label: "برگزارکنندگان" },
  { href: "/admin/events", label: "کاستوم‌ها" },
  { href: "/admin/channels", label: "کانال‌های اجباری" },
  { href: "/admin/broadcasts", label: "ارسال همگانی" },
  { href: "/admin/reports", label: "گزارش تخلف" },
  { href: "/admin/audit", label: "لاگ حسابرسی" },
  { href: "/admin/settings", label: "تنظیمات" },
];

export default function Page() {
  const [items, setItems] = useState<any[]>([]);
  const load = () => {
    api("/admin/reports")
      .then((d) => setItems(d.items || []))
      .catch(() => undefined);
  };
  useEffect(() => {
    load();
  }, []);
  const closeReport = async (id: string) => {
    await api(`/admin/reports/${id}/status?status=closed`, { method: "POST" });
    load();
  };
  return (
    <Shell title="پنل مالک ربات" items={nav}>
      <h1 className="mb-4 text-2xl font-bold">گزارش تخلف</h1>
      {items.map((r) => (
        <div key={r.id} className="card mb-2">
          <div className="font-bold">
            {r.reason_label || r.reason} — {r.status}
          </div>
          <p className="text-sm text-white/80">کاستوم: {r.event_title || "—"}</p>
          <p className="text-sm text-white/80">برگزارکننده: {r.organizer || "—"}</p>
          <p className="text-sm text-white/80">گزارش‌دهنده: {r.reporter || "—"}</p>
          <p className="text-sm text-white/70">{r.body}</p>
          {r.status === "new" ? (
            <button className="mt-2 text-sm underline" onClick={() => closeReport(r.id)}>
              بستن گزارش
            </button>
          ) : null}
        </div>
      ))}
    </Shell>
  );
}
