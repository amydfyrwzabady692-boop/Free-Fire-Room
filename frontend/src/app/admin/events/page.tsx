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

export default function EventsAdmin() {
  const [items, setItems] = useState<any[]>([]);
  const load = () => api("/admin/events").then((d) => setItems(d.items || []));
  useEffect(() => {
    load().catch(() => undefined);
  }, []);
  async function act(id: string, path: string, extra: any = { reason: "بررسی مدیریت" }) {
    if (!confirm("این عملیات حساس است. ادامه می‌دهید؟")) return;
    await api(`/admin/events/${id}/${path}`, { method: "POST", body: JSON.stringify(extra) });
    await load();
  }
  return (
    <Shell title="پنل مالک ربات" items={nav}>
      <h1 className="mb-4 text-2xl font-bold">کاستوم‌ها</h1>
      <div className="space-y-3">
        {items.map((e) => (
          <div key={e.id} className="card flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="font-bold">{e.title}</div>
              <div className="text-sm text-white/50">
                {e.status} | {e.confirmed_count}/{e.capacity} | {e.organizer_name}
              </div>
            </div>
            <div className="flex gap-2">
              <button className="btn" onClick={() => act(e.id, "approve", {})}>
                تأیید
              </button>
              <button className="btn-ghost" onClick={() => act(e.id, "reject")}>
                رد
              </button>
              <button className="btn-ghost" onClick={() => act(e.id, "cancel")}>
                لغو اضطراری
              </button>
            </div>
          </div>
        ))}
      </div>
    </Shell>
  );
}
