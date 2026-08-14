"use client";

import { useState } from "react";
import { Shell } from "@/components/Shell";
import { api } from "@/lib/api";

const nav = [
  { href: "/organizer", label: "داشبورد" },
  { href: "/organizer/events", label: "کاستوم‌ها" },
  { href: "/organizer/channel", label: "اتصال کانال" },
];

export default function Page() {
  const [ref, setRef] = useState("");
  const [msg, setMsg] = useState("");
  async function connect() {
    try {
      const d = await api("/channels/connect", { method: "POST", body: JSON.stringify({ chat_ref: ref }) });
      setMsg(`متصل شد: ${d.title} — ادمین ربات: ${d.bot_is_admin ? "بله" : "خیر"}`);
    } catch (e: any) {
      setMsg(e.message);
    }
  }
  return (
    <Shell title="پنل برگزارکننده" items={nav}>
      <h1 className="mb-4 text-2xl font-bold">اتصال کانال</h1>
      <div className="card space-y-3">
        <p className="text-sm text-white/60">ربات را ادمین کنید، سپس @username یا شناسه عددی را وارد کنید. باید خودتان مدیر کانال باشید.</p>
        <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="@mychannel" />
        <button className="btn" onClick={connect}>
          بررسی و اتصال
        </button>
        {msg && <p>{msg}</p>}
      </div>
    </Shell>
  );
}
