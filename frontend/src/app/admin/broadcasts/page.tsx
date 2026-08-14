"use client";

import { useState } from "react";
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
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [id, setId] = useState("");
  const [msg, setMsg] = useState("");
  async function create() {
    const row = await api("/admin/broadcasts", { method: "POST", body: JSON.stringify({ title, body }) });
    setId(row.id);
    setMsg("پیش‌نویس ساخته شد. برای ارسال نهایی دوباره تأیید کنید.");
  }
  async function confirmSend() {
    if (!window.confirm("ارسال همگانی قطعی می‌شود. مطمئن هستید؟")) return;
    await api(`/admin/broadcasts/${id}/confirm`, { method: "POST" });
    setMsg("کمپین تأیید و صف‌بندی شد.");
  }
  return (
    <Shell title="پنل مالک ربات" items={nav}>
      <h1 className="mb-4 text-2xl font-bold">ارسال همگانی</h1>
      <div className="card space-y-3">
        <input placeholder="عنوان داخلی" value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea rows={6} placeholder="متن پیام" value={body} onChange={(e) => setBody(e.target.value)} />
        <button className="btn" onClick={create}>
          پیش‌نویس
        </button>
        <button className="btn-ghost" onClick={confirmSend} disabled={!id}>
          تأیید نهایی ارسال
        </button>
        {msg && <p>{msg}</p>}
      </div>
    </Shell>
  );
}
