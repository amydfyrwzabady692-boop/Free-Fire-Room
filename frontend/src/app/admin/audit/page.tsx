"use client";

import { useEffect, useState } from "react";
import { ADMIN_NAV, EmptyBox, ErrorBox, Loading, Shell } from "@/components/Shell";
import { api } from "@/lib/api";

type Row = {
  id: string;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  created_at: string;
  actor?: string | null;
};

/** The log stored raw action codes; the page printed them verbatim. */
const ACTION_FA: Record<string, string> = {
  admin_login: "ورود مدیر به پنل",
  user_banned: "بن کاربر",
  user_ban_updated: "به‌روزرسانی بن کاربر",
  user_unbanned: "رفع بن کاربر",
  event_submitted: "ثبت کاستوم",
  event_approved: "تأیید کاستوم",
  event_rejected: "رد کاستوم",
  event_cancelled: "لغو کاستوم",
  organizer_approved: "تأیید برگزارکننده",
  organizer_rejected: "رد برگزارکننده",
  room_credentials_updated: "ثبت/تغییر ROOM ID و PASS",
  broadcast_created: "ساخت پیش‌نویس ارسال همگانی",
  broadcast_confirmed: "تأیید ارسال همگانی",
  setting_changed: "تغییر تنظیمات",
  setting_toggled: "تغییر تنظیمات",
  global_channel_added: "افزودن کانال اجباری",
  global_channel_toggled: "تغییر وضعیت کانال اجباری",
  global_channel_removed: "حذف کانال اجباری",
  announcement_hidden: "مخفی کردن اطلاع‌رسانی",
  report_updated: "به‌روزرسانی گزارش تخلف",
  report_closed: "بستن گزارش تخلف",
  winner_claim_reviewed: "بررسی ادعای برنده",
};

function when(iso: string) {
  try {
    return new Intl.DateTimeFormat("fa-IR", {
      dateStyle: "short",
      timeStyle: "short",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

export default function Page() {
  const [items, setItems] = useState<Row[] | null>(null);
  const [err, setErr] = useState("");
  const [filter, setFilter] = useState("");

  useEffect(() => {
    api("/admin/audit")
      .then((d) => setItems(d.items || []))
      .catch((e) => setErr(e.message));
  }, []);

  const shown = (items ?? []).filter((r) => {
    if (!filter) return true;
    const label = ACTION_FA[r.action] || r.action;
    return label.includes(filter) || r.action.includes(filter);
  });

  return (
    <Shell title="پنل مالک ربات" items={ADMIN_NAV}>
      <h1 className="mb-4 text-2xl font-bold">لاگ حسابرسی</h1>
      <p className="mb-3 text-sm text-white/50">این رکوردها از طریق API قابل ویرایش یا حذف نیستند.</p>
      <ErrorBox message={err} />
      <input
        className="mb-4"
        placeholder="فیلتر بر اساس نوع عملیات"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      {items === null && <Loading />}
      {items?.length === 0 && <EmptyBox message="هنوز رویدادی ثبت نشده." />}
      {items && items.length > 0 && shown.length === 0 && (
        <EmptyBox message="هیچ رویدادی با این فیلتر پیدا نشد." />
      )}
      {shown.map((r) => (
        <div key={r.id} className="card mb-2 flex flex-wrap justify-between gap-2 text-sm">
          <div>
            <span className="font-bold">{ACTION_FA[r.action] || r.action}</span>
            {r.entity_type && <span className="text-white/50"> — {r.entity_type}</span>}
          </div>
          <div className="text-white/50">{when(r.created_at)}</div>
        </div>
      ))}
    </Shell>
  );
}
