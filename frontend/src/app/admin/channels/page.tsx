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

export default function ChannelsPage() {
  const [items, setItems] = useState<any[]>([]);
  const [chatRef, setChatRef] = useState("");
  const load = () => api("/admin/global-channels").then((d) => setItems(d.items || []));
  useEffect(() => {
    load().catch(() => undefined);
  }, []);
  async function add() {
    await api("/admin/global-channels", { method: "POST", body: JSON.stringify({ chat_ref: chatRef, scope: "all" }) });
    setChatRef("");
    await load();
  }
  return (
    <Shell title="پنل مالک ربات" items={nav}>
      <h1 className="mb-4 text-2xl font-bold">کانال‌های اجباری سراسری</h1>
      <p className="mb-4 text-sm text-white/60">
        ربات باید در کانال ادمین باشد؛ در غیر این صورت کانال به‌عنوان شرط پذیرفته نمی‌شود.
      </p>
      <div className="mb-4 flex gap-2">
        <input placeholder="@channel یا شناسه عددی" value={chatRef} onChange={(e) => setChatRef(e.target.value)} />
        <button className="btn" onClick={add}>
          افزودن
        </button>
      </div>
      {items.map((r) => (
        <div key={r.id} className="card mb-2 flex justify-between">
          <div>
            {r.title} @{r.username || "-"} | ادمین ربات: {r.bot_is_admin ? "بله" : "خیر"} | {r.is_active ? "فعال" : "غیرفعال"}
          </div>
          <button className="btn-ghost" onClick={() => api(`/admin/global-channels/${r.id}/toggle`, { method: "POST" }).then(load)}>
            تغییر وضعیت
          </button>
        </div>
      ))}
    </Shell>
  );
}
