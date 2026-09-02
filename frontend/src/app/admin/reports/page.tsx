"use client";

import { useEffect, useState } from "react";
import { ADMIN_NAV, EmptyBox, ErrorBox, Loading, Shell } from "@/components/Shell";
import { api } from "@/lib/api";

type Row = {
  id: string;
  reason: string;
  reason_label?: string;
  status: string;
  event_title?: string | null;
  organizer?: string | null;
  reporter?: string | null;
  body?: string | null;
};

export default function Page() {
  const [items, setItems] = useState<Row[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () =>
    api("/admin/reports")
      .then((d) => setItems(d.items || []))
      .catch((e) => setErr(e.message));

  useEffect(() => {
    load();
  }, []);

  async function setStatus(id: string, status: string, note: string) {
    setErr("");
    setBusy(true);
    try {
      const params = new URLSearchParams({ status, note });
      await api(`/admin/reports/${id}/status?${params.toString()}`, { method: "POST" });
      await load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const open = items?.filter((r) => r.status === "new") ?? [];
  const rest = items?.filter((r) => r.status !== "new") ?? [];

  return (
    <Shell title="پنل مالک ربات" items={ADMIN_NAV}>
      <h1 className="mb-4 text-2xl font-bold">گزارش تخلف</h1>
      <ErrorBox message={err} />
      {items === null && <Loading />}
      {items?.length === 0 && <EmptyBox message="گزارشی ثبت نشده." />}
      {items && items.length > 0 && open.length === 0 && (
        <EmptyBox message="گزارش بازی وجود ندارد — همه رسیدگی شده‌اند." />
      )}
      {[...open, ...rest].map((r) => (
        <div key={r.id} className="card mb-2">
          <div className="font-bold">
            {r.reason_label || r.reason}
            {r.status !== "new" && <span className="mr-2 text-sm text-white/50">(بسته شده)</span>}
          </div>
          <p className="text-sm text-white/80">کاستوم: {r.event_title || "—"}</p>
          <p className="text-sm text-white/80">برگزارکننده: {r.organizer || "—"}</p>
          <p className="text-sm text-white/80">گزارش‌دهنده: {r.reporter || "—"}</p>
          <p className="mt-2 whitespace-pre-wrap text-sm text-white/70">{r.body}</p>
          {r.status === "new" && (
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                className="btn-ghost text-red-400"
                disabled={busy}
                onClick={() => {
                  if (!confirm("تخلف تأیید و از اعتبار برگزارکننده کسر شود؟")) return;
                  setStatus(r.id, "confirmed", "تخلف تأیید شد");
                }}
              >
                تأیید تخلف و بستن
              </button>
              <button
                className="btn-ghost"
                disabled={busy}
                onClick={() => setStatus(r.id, "closed", "بسته شد بدون اقدام")}
              >
                بستن بدون اقدام
              </button>
            </div>
          )}
        </div>
      ))}
    </Shell>
  );
}
