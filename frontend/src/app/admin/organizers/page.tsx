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
  const load = () => api("/admin/organizers").then((d) => setItems(d.items || []));
  useEffect(() => {
    load().catch(() => undefined);
  }, []);
  return (
    <Shell title="پنل مالک ربات" items={nav}>
      <h1 className="mb-4 text-2xl font-bold">برگزارکنندگان</h1>
      {items.map((o) => (
        <div key={o.id} className="card mb-2 flex justify-between">
          <div>
            {o.display_name} | {o.status} | اعتماد: {o.trust_score} | تأییدشده: {o.verified_badge ? "بله" : "خیر"}
          </div>
          <div className="flex gap-2">
            <button className="btn" onClick={() => api(`/admin/organizers/${o.id}/approve`, { method: "POST" }).then(load)}>
              تأیید
            </button>
            <button
              className="btn-ghost"
              onClick={() =>
                api(`/admin/organizers/${o.id}/reject`, { method: "POST", body: JSON.stringify({ reason: "عدم احراز" }) }).then(load)
              }
            >
              رد
            </button>
          </div>
        </div>
      ))}
    </Shell>
  );
}
