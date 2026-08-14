"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { api } from "@/lib/api";

const items = [
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

export default function AdminHome() {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api("/admin/dashboard")
      .then(setData)
      .catch((e) => setErr(e.message));
  }, []);
  return (
    <Shell title="پنل مالک ربات" items={items}>
      <h1 className="mb-4 text-2xl font-bold">داشبورد</h1>
      {err && <p className="text-red-400">{err}</p>}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {data &&
          Object.entries(data).map(([k, v]) => (
            <div key={k} className="card">
              <div className="text-sm text-white/50">{k}</div>
              <div className="text-2xl font-bold">{String(v)}</div>
            </div>
          ))}
      </div>
    </Shell>
  );
}
