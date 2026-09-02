"use client";

import { useEffect, useState } from "react";
import { ADMIN_NAV, EmptyBox, ErrorBox, Loading, Shell } from "@/components/Shell";
import { api } from "@/lib/api";

type Row = {
  id: string;
  title: string | null;
  username: string | null;
  bot_is_admin: boolean;
  is_active: boolean;
};

export default function ChannelsPage() {
  const [items, setItems] = useState<Row[] | null>(null);
  const [chatRef, setChatRef] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () =>
    api("/admin/global-channels")
      .then((d) => setItems(d.items || []))
      .catch((e) => setErr(e.message));

  useEffect(() => {
    load();
  }, []);

  async function run(fn: () => Promise<unknown>) {
    setErr("");
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const add = () =>
    run(async () => {
      if (!chatRef.trim()) throw new Error("آیدی یا لینک کانال را وارد کنید.");
      await api("/admin/global-channels", {
        method: "POST",
        body: JSON.stringify({ chat_ref: chatRef.trim(), scope: "all" }),
      });
      setChatRef("");
    });

  const remove = (row: Row) =>
    run(async () => {
      const name = row.title || row.username || "این کانال";
      if (!confirm(`«${name}» از کانال‌های اجباری حذف شود؟`)) return;
      await api(`/admin/global-channels/${row.id}`, { method: "DELETE" });
    });

  return (
    <Shell title="پنل مالک ربات" items={ADMIN_NAV}>
      <h1 className="mb-4 text-2xl font-bold">کانال‌های اجباری ورود</h1>
      <p className="mb-4 text-sm text-white/60">
        ربات باید در کانال ادمین باشد؛ در غیر این صورت عضویت کاربر قابل بررسی نیست و کانال به‌عنوان شرط
        اعمال نمی‌شود.
      </p>
      <ErrorBox message={err} />
      <div className="mb-4 flex gap-2">
        <input
          placeholder="@channel یا شناسه عددی"
          value={chatRef}
          onChange={(e) => setChatRef(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <button className="btn" onClick={add} disabled={busy}>
          افزودن
        </button>
      </div>
      {items === null && <Loading />}
      {items?.length === 0 && (
        <EmptyBox message="هیچ کانال اجباری ثبت نشده — همه بدون عضویت وارد ربات می‌شوند." />
      )}
      {items?.map((r) => (
        <div key={r.id} className="card mb-2 flex items-center justify-between gap-4">
          <div className="text-sm">
            <div className="font-bold">
              {r.title || "بدون نام"} {r.username ? `@${r.username}` : ""}
            </div>
            <div className="text-white/60">
              {r.is_active ? "فعال" : "خاموش"}
              {" · "}
              {r.bot_is_admin ? "ربات ادمین است" : "⚠️ ربات ادمین نیست"}
            </div>
          </div>
          <div className="flex gap-2">
            <button
              className="btn-ghost"
              disabled={busy}
              onClick={() => run(() => api(`/admin/global-channels/${r.id}/toggle`, { method: "POST" }))}
            >
              {r.is_active ? "خاموش کردن" : "روشن کردن"}
            </button>
            <button className="btn-ghost text-red-400" disabled={busy} onClick={() => remove(r)}>
              حذف
            </button>
          </div>
        </div>
      ))}
    </Shell>
  );
}
