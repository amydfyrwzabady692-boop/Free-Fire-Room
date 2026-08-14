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

export default function UsersPage() {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<any[]>([]);
  const load = () => api(`/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`).then((d) => setItems(d.items || []));
  useEffect(() => {
    load().catch(() => undefined);
  }, []);
  async function ban(id: string) {
    const reason = prompt("دلیل مسدودسازی؟");
    if (!reason) return;
    await api(`/admin/users/${id}/ban`, { method: "POST", body: JSON.stringify({ scope: "bot", reason }) });
    await load();
  }
  return (
    <Shell title="پنل مالک ربات" items={nav}>
      <h1 className="mb-4 text-2xl font-bold">کاربران</h1>
      <div className="mb-4 flex gap-2">
        <input placeholder="جستجو با ID یا یوزرنیم" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn" onClick={() => load()}>
          جستجو
        </button>
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-white/50">
              <th className="p-2">تلگرام</th>
              <th>نام</th>
              <th>وضعیت</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((u) => (
              <tr key={u.id} className="border-t border-line">
                <td className="p-2">{u.telegram_id}</td>
                <td>{u.first_name} @{u.username || "-"}</td>
                <td>{u.status}</td>
                <td>
                  <button className="btn-ghost" onClick={() => ban(u.id)}>
                    Ban
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Shell>
  );
}
