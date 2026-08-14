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
  const [me, setMe] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  useEffect(() => {
    api("/organizers/me").then(setMe).catch(() => undefined);
    api("/organizers/me/events").then((d) => setEvents(d.items || [])).catch(() => undefined);
  }, []);
  return (
    <Shell title="پنل برگزارکننده" items={nav}>
      <h1 className="mb-4 text-2xl font-bold">داشبورد برگزارکننده</h1>
      {me && (
        <div className="card mb-4">
          {me.display_name} | وضعیت: {me.status} | نشان تأیید: {me.verified_badge ? "بله" : "خیر"} | اعتماد: {me.trust_score}
        </div>
      )}
      <div className="card">تعداد کاستوم‌ها: {events.length}</div>
    </Shell>
  );
}
