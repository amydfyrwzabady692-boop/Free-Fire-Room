"use client";

import { useEffect, useState } from "react";
import { ADMIN_NAV, EmptyBox, ErrorBox, Loading, Shell } from "@/components/Shell";
import { api } from "@/lib/api";

type Row = {
  id: string;
  title: string;
  status: string;
  capacity: number;
  confirmed_count: number;
  organizer_name?: string | null;
  starts_at?: string;
};

const STATUS_FA: Record<string, string> = {
  draft: "پیش‌نویس",
  pending_approval: "منتظر تأیید",
  published: "منتشرشده",
  full: "تکمیل",
  started: "شروع‌شده",
  finished: "تمام‌شده",
  cancelled: "لغوشده",
  rejected: "ردشده",
};

type Funnel = Record<string, number>;

const FUNNEL_FA: [string, string][] = [
  ["viewed", "کارت را دیدند"],
  ["started", "وارد ثبت‌نام شدند"],
  ["confirmed", "شرایط را کامل کردند"],
  ["delivered", "مشخصات را گرفتند"],
];

export default function EventsAdmin() {
  const [items, setItems] = useState<Row[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [funnel, setFunnel] = useState<{ id: string; data: Funnel } | null>(null);

  const load = () =>
    api("/admin/events")
      .then((d) => setItems(d.items || []))
      .catch((e) => setErr(e.message));

  useEffect(() => {
    load();
  }, []);

  async function act(id: string, path: string, prompt_text?: string) {
    setErr("");
    let extra: any = {};
    if (prompt_text) {
      const reason = prompt(prompt_text);
      if (!reason) return;
      extra = { reason };
    }
    if (!confirm("این عملیات روی بازیکن‌های ثبت‌نام‌کرده اثر می‌گذارد. ادامه می‌دهید؟")) return;
    setBusy(true);
    try {
      await api(`/admin/events/${id}/${path}`, { method: "POST", body: JSON.stringify(extra) });
      await load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function showFunnel(id: string) {
    setErr("");
    try {
      setFunnel({ id, data: await api(`/admin/events/${id}/funnel`) });
    } catch (e: any) {
      setErr(e.message);
    }
  }

  return (
    <Shell title="پنل مالک ربات" items={ADMIN_NAV}>
      <h1 className="mb-4 text-2xl font-bold">کاستوم‌ها</h1>
      <ErrorBox message={err} />
      {items === null && <Loading />}
      {items?.length === 0 && <EmptyBox message="هنوز کاستومی ثبت نشده." />}
      <div className="space-y-3">
        {items?.map((e) => (
          <div key={e.id} className="card">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="font-bold">{e.title}</div>
                <div className="text-sm text-white/50">
                  {STATUS_FA[e.status] || e.status} · {e.confirmed_count}/{e.capacity} نفر
                  {e.organizer_name ? ` · ${e.organizer_name}` : ""}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <button className="btn-ghost" onClick={() => showFunnel(e.id)}>
                  قیف
                </button>
                {e.status === "pending_approval" && (
                  <button className="btn" disabled={busy} onClick={() => act(e.id, "approve")}>
                    تأیید
                  </button>
                )}
                {e.status === "pending_approval" && (
                  <button className="btn-ghost" disabled={busy} onClick={() => act(e.id, "reject", "دلیل رد؟")}>
                    رد
                  </button>
                )}
                {!["cancelled", "finished", "rejected"].includes(e.status) && (
                  <button
                    className="btn-ghost text-red-400"
                    disabled={busy}
                    onClick={() => act(e.id, "cancel", "دلیل لغو؟")}
                  >
                    لغو اضطراری
                  </button>
                )}
              </div>
            </div>
            {funnel?.id === e.id && (
              <div className="mt-3 border-t border-line pt-3 text-sm">
                {FUNNEL_FA.map(([key, label]) => (
                  <div key={key} className="flex justify-between py-1">
                    <span className="text-white/70">{label}</span>
                    <span className="font-bold">{funnel.data[key] ?? 0}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </Shell>
  );
}
