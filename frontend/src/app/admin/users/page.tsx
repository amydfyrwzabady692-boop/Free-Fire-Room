"use client";

import { useEffect, useState } from "react";
import { ADMIN_NAV, EmptyBox, ErrorBox, Loading, Shell } from "@/components/Shell";
import { api } from "@/lib/api";

type Row = {
  id: string;
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  status: string;
  is_banned?: boolean;
};

const STATUS_FA: Record<string, string> = {
  active: "فعال",
  banned: "بن‌شده",
  deleted: "حذف‌شده",
};

const PAGE_SIZE = 50;

export default function UsersPage() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [items, setItems] = useState<Row[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async (nextPage = page, query = q) => {
    const params = new URLSearchParams({ page: String(nextPage), size: String(PAGE_SIZE) });
    if (query.trim()) params.set("q", query.trim());
    const d = await api(`/admin/users?${params.toString()}`);
    setItems(d.items || []);
    setTotal(d.total ?? (d.items || []).length);
    setPage(nextPage);
  };

  useEffect(() => {
    load(0).catch((e) => setErr(e.message));
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

  const ban = (u: Row) =>
    run(async () => {
      const reason = prompt(`دلیل مسدودسازی ${u.first_name || u.telegram_id}؟`);
      if (!reason) return;
      await api(`/admin/users/${u.id}/ban`, {
        method: "POST",
        body: JSON.stringify({ scope: "bot", reason }),
      });
    });

  const banOrganize = (u: Row) =>
    run(async () => {
      const reason = prompt("دلیل ممنوعیت برگزاری کاستوم؟");
      if (!reason) return;
      await api(`/admin/users/${u.id}/ban`, {
        method: "POST",
        body: JSON.stringify({ scope: "organize", reason }),
      });
    });

  // the API had an unban route from the start; the UI never called it
  const unban = (u: Row) =>
    run(async () => {
      if (!confirm(`محدودیت ${u.first_name || u.telegram_id} برداشته شود؟`)) return;
      await api(`/admin/users/${u.id}/unban`, { method: "POST" });
    });

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <Shell title="پنل مالک ربات" items={ADMIN_NAV}>
      <h1 className="mb-4 text-2xl font-bold">کاربران</h1>
      <ErrorBox message={err} />
      <div className="mb-4 flex gap-2">
        <input
          placeholder="جستجو با شناسه تلگرام، یوزرنیم یا نام"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run(() => load(0, q))}
        />
        <button className="btn" disabled={busy} onClick={() => run(() => load(0, q))}>
          جستجو
        </button>
      </div>
      {items === null && <Loading />}
      {items?.length === 0 && <EmptyBox message="کاربری با این مشخصات پیدا نشد." />}
      {items && items.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-white/50">
                <th className="p-2 text-right">تلگرام</th>
                <th className="text-right">نام</th>
                <th className="text-right">وضعیت</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((u) => {
                const banned = u.is_banned ?? u.status === "banned";
                return (
                  <tr key={u.id} className="border-t border-line">
                    <td className="p-2">{u.telegram_id}</td>
                    <td>
                      {u.first_name || "—"} {u.username ? `@${u.username}` : ""}
                    </td>
                    <td className={banned ? "text-red-400" : ""}>{STATUS_FA[u.status] || u.status}</td>
                    <td className="flex flex-wrap justify-end gap-1 p-2">
                      {banned ? (
                        <button className="btn-ghost text-green-400" disabled={busy} onClick={() => unban(u)}>
                          رفع بن
                        </button>
                      ) : (
                        <>
                          <button className="btn-ghost" disabled={busy} onClick={() => banOrganize(u)}>
                            بن برگزاری
                          </button>
                          <button className="btn-ghost text-red-400" disabled={busy} onClick={() => ban(u)}>
                            بن کامل
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <div className="mt-4 flex items-center justify-center gap-3 text-sm">
          <button className="btn-ghost" disabled={page === 0 || busy} onClick={() => run(() => load(page - 1))}>
            قبلی
          </button>
          <span className="text-white/60">
            صفحه {page + 1} از {pages} ({total} کاربر)
          </span>
          <button
            className="btn-ghost"
            disabled={page + 1 >= pages || busy}
            onClick={() => run(() => load(page + 1))}
          >
            بعدی
          </button>
        </div>
      )}
    </Shell>
  );
}
