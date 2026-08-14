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
  useEffect(() => {
    api("/admin/audit").then((d) => setItems(d.items || [])).catch(() => undefined);
  }, []);
  return (
    <Shell title="پنل مالک ربات" items={nav}>
      <h1 className="mb-4 text-2xl font-bold">لاگ حسابرسی</h1>
      <p className="mb-3 text-sm text-white/50">این رکوردها از طریق API قابل ویرایش نیستند.</p>
      {items.map((r) => (
        <div key={r.id} className="card mb-2 text-sm">
          <b>{r.action}</b> — {r.entity_type} {r.entity_id} — {r.created_at}
        </div>
      ))}
    </Shell>
  );
}
