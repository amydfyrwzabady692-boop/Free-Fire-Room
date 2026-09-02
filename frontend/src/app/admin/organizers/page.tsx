"use client";

import { useEffect, useState } from "react";
import { ADMIN_NAV, EmptyBox, ErrorBox, Loading, Shell } from "@/components/Shell";
import { api } from "@/lib/api";

type Row = {
  id: string;
  display_name: string | null;
  status: string;
  trust_score: number | null;
  verified_badge: boolean;
};

const STATUS_FA: Record<string, string> = {
  pending: "منتظر تأیید",
  approved: "تأییدشده",
  rejected: "ردشده",
  suspended: "معلق",
};

function trustBadge(score: number | null) {
  const value = score ?? 50;
  if (value >= 85) return { text: "بسیار مطمئن", cls: "text-green-400" };
  if (value >= 70) return { text: "مطمئن", cls: "text-green-300" };
  if (value >= 50) return { text: "معمولی", cls: "text-white/70" };
  if (value >= 30) return { text: "کم‌اعتبار", cls: "text-orange-400" };
  return { text: "پرریسک", cls: "text-red-400" };
}

export default function Page() {
  const [items, setItems] = useState<Row[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<{ id: string; events: any[] } | null>(null);

  const load = () =>
    api("/admin/organizers")
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

  async function showTrust(o: Row) {
    setErr("");
    try {
      const d = await api(`/admin/organizers/${o.id}/trust`);
      setHistory({ id: o.id, events: d.events || [] });
    } catch (e: any) {
      setErr(e.message);
    }
  }

  return (
    <Shell title="پنل مالک ربات" items={ADMIN_NAV}>
      <h1 className="mb-4 text-2xl font-bold">برگزارکنندگان</h1>
      <ErrorBox message={err} />
      {items === null && <Loading />}
      {items?.length === 0 && <EmptyBox message="هنوز برگزارکننده‌ای ثبت نشده." />}
      {items?.map((o) => {
        const badge = trustBadge(o.trust_score);
        return (
          <div key={o.id} className="card mb-2">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm">
                <div className="font-bold">{o.display_name || "بدون نام"}</div>
                <div className="text-white/60">
                  {STATUS_FA[o.status] || o.status}
                  {" · "}
                  <span className={badge.cls}>
                    اعتبار: {Math.round(o.trust_score ?? 50)}/100 ({badge.text})
                  </span>
                  {o.verified_badge ? " · تأییدشده" : ""}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <button className="btn-ghost" onClick={() => showTrust(o)}>
                  سابقهٔ اعتبار
                </button>
                {o.status !== "approved" && (
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={() => run(() => api(`/admin/organizers/${o.id}/approve`, { method: "POST" }))}
                  >
                    تأیید
                  </button>
                )}
                {o.status !== "rejected" && (
                  <button
                    className="btn-ghost text-red-400"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        const reason = prompt("دلیل رد کردن؟");
                        if (!reason) return;
                        await api(`/admin/organizers/${o.id}/reject`, {
                          method: "POST",
                          body: JSON.stringify({ reason }),
                        });
                      })
                    }
                  >
                    رد
                  </button>
                )}
              </div>
            </div>
            {history?.id === o.id && (
              <div className="mt-3 border-t border-line pt-3 text-sm">
                {history.events.length === 0 && <p className="text-white/50">هنوز رویدادی ثبت نشده.</p>}
                {history.events.map((ev, i) => (
                  <div key={i} className="flex justify-between gap-3 py-1">
                    <span className="text-white/70">{ev.reason}</span>
                    <span className={ev.delta >= 0 ? "text-green-400" : "text-red-400"}>
                      {ev.delta > 0 ? "+" : ""}
                      {ev.delta}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </Shell>
  );
}
