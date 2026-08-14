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
  const [data, setData] = useState<any>({});
  useEffect(() => {
    api("/admin/settings").then(setData).catch(() => undefined);
  }, []);
  async function save(key: string, value: any) {
    await api("/admin/settings", { method: "PUT", body: JSON.stringify({ key, value }) });
    setData(await api("/admin/settings"));
  }
  return (
    <Shell title="پنل مالک ربات" items={nav}>
      <h1 className="mb-4 text-2xl font-bold">تنظیمات</h1>
      <div className="card space-y-3">
        <label className="flex items-center justify-between gap-4">
          نیاز به تأیید کاستوم
          <input
            type="checkbox"
            checked={!!data.event_approval_required}
            onChange={(e) => save("event_approval_required", e.target.checked)}
          />
        </label>
        <label>
          سقف کاستوم هر برگزارکننده
          <input
            type="number"
            defaultValue={data.max_events_per_organizer || 10}
            onBlur={(e) => save("max_events_per_organizer", Number(e.target.value))}
          />
        </label>
        <label>
          سقف دعوت لازم
          <input
            type="number"
            defaultValue={data.max_required_referrals || 20}
            onBlur={(e) => save("max_required_referrals", Number(e.target.value))}
          />
        </label>
        <label className="flex items-center justify-between gap-4">
          حالت تعمیرات
          <input type="checkbox" checked={!!data.maintenance_mode} onChange={(e) => save("maintenance_mode", e.target.checked)} />
        </label>
      </div>
    </Shell>
  );
}
