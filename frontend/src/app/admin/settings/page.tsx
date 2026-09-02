"use client";

import { useEffect, useState } from "react";
import { ADMIN_NAV, ErrorBox, Loading, Shell } from "@/components/Shell";
import { api } from "@/lib/api";

const TOGGLES = [
  {
    key: "event_approval_required",
    label: "تأیید دستی کاستوم",
    hint: "اگر روشن باشد هر کاستوم قبل از دیده شدن باید تأیید شود.",
  },
  {
    key: "auto_approve_organizers",
    label: "تأیید خودکار برگزارکننده",
    hint: "اگر روشن باشد هر کاربری بدون تأیید شما می‌تواند کاستوم بگذارد.",
  },
  {
    key: "maintenance_mode",
    label: "حالت تعمیرات",
    hint: "وقتی روشن باشد فقط مدیر ربات می‌تواند از ربات استفاده کند.",
  },
];

const NUMBERS = [
  { key: "max_events_per_organizer", label: "سقف کاستوم فعال هر برگزارکننده", min: 1, max: 100 },
  { key: "max_required_channels_per_event", label: "سقف کانال اجباری هر کاستوم", min: 1, max: 20 },
  { key: "max_required_referrals", label: "سقف دعوت لازم", min: 0, max: 100 },
];

export default function Page() {
  const [data, setData] = useState<Record<string, any> | null>(null);
  const [err, setErr] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api("/admin/settings")
      .then(setData)
      .catch((e) => setErr(e.message));
  }, []);

  async function save(key: string, value: any, label: string) {
    setErr("");
    setSaved("");
    setBusy(true);
    try {
      await api("/admin/settings", { method: "PUT", body: JSON.stringify({ key, value }) });
      setData(await api("/admin/settings"));
      setSaved(`«${label}» ذخیره شد.`);
      setTimeout(() => setSaved(""), 2500);
    } catch (e: any) {
      // silently swallowing this was why the page looked like it never saved
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="پنل مالک ربات" items={ADMIN_NAV}>
      <h1 className="mb-4 text-2xl font-bold">تنظیمات</h1>
      <ErrorBox message={err} />
      {saved && (
        <div className="mb-4 rounded-xl border border-green-500/40 bg-green-500/10 p-3 text-sm text-green-300">
          {saved}
        </div>
      )}
      {!data && !err && <Loading />}
      {data && (
        <div className="card space-y-5">
          {TOGGLES.map((t) => (
            <label key={t.key} className="flex items-start justify-between gap-4">
              <span>
                <span className="block">{t.label}</span>
                <span className="block text-xs text-white/50">{t.hint}</span>
              </span>
              <input
                type="checkbox"
                disabled={busy}
                checked={!!data[t.key]}
                onChange={(e) => save(t.key, e.target.checked, t.label)}
              />
            </label>
          ))}
          <hr className="border-line" />
          {NUMBERS.map((n) => (
            <label key={n.key} className="block">
              <span className="mb-1 block">{n.label}</span>
              <input
                type="number"
                min={n.min}
                max={n.max}
                disabled={busy}
                defaultValue={Number(data[n.key] ?? n.min)}
                onBlur={(e) => {
                  const value = Number(e.target.value);
                  if (Number.isNaN(value) || value < n.min || value > n.max) {
                    setErr(`${n.label} باید بین ${n.min} و ${n.max} باشد.`);
                    e.target.value = String(data[n.key] ?? n.min);
                    return;
                  }
                  if (value !== Number(data[n.key])) save(n.key, value, n.label);
                }}
              />
            </label>
          ))}
        </div>
      )}
    </Shell>
  );
}
