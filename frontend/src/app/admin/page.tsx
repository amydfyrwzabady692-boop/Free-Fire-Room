"use client";

import { useEffect, useState } from "react";
import { ADMIN_NAV, EmptyBox, ErrorBox, Loading, Shell } from "@/components/Shell";
import { api } from "@/lib/api";

/** The dashboard used to render raw keys like "deliveries_failed" on the cards. */
const LABELS: Record<string, { fa: string; hint?: string; accent?: boolean }> = {
  users: { fa: "کل کاربران" },
  new_users_24h: { fa: "کاربر جدید (۲۴ ساعت)" },
  dau: { fa: "فعال امروز" },
  wau: { fa: "فعال این هفته" },
  mau: { fa: "فعال این ماه" },
  banned: { fa: "بن فعال" },
  organizers: { fa: "برگزارکننده‌ها" },
  pending_organizers: { fa: "منتظر تأیید", hint: "برگزارکننده", accent: true },
  active_events: { fa: "کاستوم فعال" },
  pending_events: { fa: "کاستوم منتظر تأیید", accent: true },
  open_reports: { fa: "گزارش تخلف باز", accent: true },
  confirmed_registrations: { fa: "ثبت‌نام قطعی" },
  deliveries_sent: { fa: "ارسال موفق مشخصات" },
  deliveries_failed: { fa: "ارسال ناموفق" },
};

const ORDER = Object.keys(LABELS);

export default function AdminHome() {
  const [data, setData] = useState<Record<string, number> | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api("/admin/dashboard")
      .then(setData)
      .catch((e) => setErr(e.message));
  }, []);

  const keys = data ? ORDER.filter((k) => k in data).concat(Object.keys(data).filter((k) => !(k in LABELS))) : [];

  return (
    <Shell title="پنل مالک ربات" items={ADMIN_NAV}>
      <h1 className="mb-4 text-2xl font-bold">داشبورد</h1>
      <ErrorBox message={err} />
      {!data && !err && <Loading />}
      {data && keys.length === 0 && <EmptyBox message="هنوز داده‌ای برای نمایش نیست." />}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {data &&
          keys.map((key) => {
            const meta = LABELS[key];
            const value = Number(data[key] ?? 0);
            const highlight = meta?.accent && value > 0;
            return (
              <div
                key={key}
                className={`card ${highlight ? "border-accent" : ""}`}
                title={meta?.hint || meta?.fa || key}
              >
                <div className="text-sm text-white/50">{meta?.fa || key}</div>
                <div className={`text-2xl font-bold ${highlight ? "text-accent" : ""}`}>{value}</div>
              </div>
            );
          })}
      </div>
    </Shell>
  );
}
