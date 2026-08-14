"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { api } from "@/lib/api";

const nav = [
  { href: "/organizer", label: "داشبورد" },
  { href: "/organizer/events", label: "کاستوم‌ها" },
  { href: "/organizer/channel", label: "اتصال کانال" },
];

export default function Page() {
  const [items, setItems] = useState<any[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [people, setPeople] = useState<any[]>([]);
  const load = () => api("/organizers/me/events").then((d) => setItems(d.items || []));
  useEffect(() => {
    load().catch(() => undefined);
  }, []);
  async function open(id: string) {
    setSelected(id);
    const d = await api(`/organizers/me/events/${id}/participants`);
    setPeople(d.items || []);
  }
  async function cancel(id: string) {
    if (!window.confirm("لغو کاستوم به همه اطلاع داده می‌شود و Jobها متوقف می‌شوند. ادامه؟")) return;
    await api(`/events/${id}/cancel`, { method: "POST", body: JSON.stringify({ reason: "لغو توسط برگزارکننده" }) });
    await load();
  }
  return (
    <Shell title="پنل برگزارکننده" items={nav}>
      <h1 className="mb-4 text-2xl font-bold">کاستوم‌های من</h1>
      {items.map((e) => (
        <div key={e.id} className="card mb-2">
          <div className="font-bold">{e.title}</div>
          <div className="text-sm text-white/60">
            {e.status} | {e.confirmed_count}/{e.capacity} | {e.deep_link}
          </div>
          <div className="mt-2 flex gap-2">
            <button className="btn-ghost" onClick={() => open(e.id)}>
              شرکت‌کنندگان
            </button>
            <button className="btn-ghost" onClick={() => cancel(e.id)}>
              لغو
            </button>
          </div>
        </div>
      ))}
      {selected && (
        <div className="card mt-4">
          <h2 className="mb-2 font-bold">شرکت‌کنندگان</h2>
          {people.map((p) => (
            <div key={p.registration_id} className="text-sm">
              {p.name} @{p.username || "-"} | {p.status} | FF: {p.ff_player_id || "-"}
            </div>
          ))}
        </div>
      )}
    </Shell>
  );
}
