"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { logout } from "@/lib/api";

export const ADMIN_NAV = [
  { href: "/admin", label: "داشبورد" },
  { href: "/admin/users", label: "کاربران" },
  { href: "/admin/organizers", label: "برگزارکنندگان" },
  { href: "/admin/events", label: "کاستوم‌ها" },
  { href: "/admin/channels", label: "کانال‌های اجباری" },
  { href: "/admin/broadcasts", label: "ارسال همگانی" },
  { href: "/admin/reports", label: "گزارش تخلف" },
  { href: "/admin/audit", label: "لاگ حسابرسی" },
  { href: "/admin/settings", label: "تنظیمات" },
];

export function Shell({
  title,
  items,
  children,
}: {
  title: string;
  items: { href: string; label: string }[];
  children: React.ReactNode;
}) {
  const path = usePathname();
  const router = useRouter();
  return (
    <div className="grid min-h-screen grid-cols-[240px_1fr]">
      <aside className="border-l border-line bg-panel p-4">
        <div className="mb-6 text-lg font-bold text-accent">{title}</div>
        <nav className="space-y-1">
          {items.map((it) => (
            <Link
              key={it.href}
              href={it.href}
              className={`block rounded-xl px-3 py-2 ${
                path === it.href ? "bg-accent text-black" : "hover:bg-white/5"
              }`}
            >
              {it.label}
            </Link>
          ))}
        </nav>
        <button
          className="btn-ghost mt-8 w-full"
          onClick={async () => {
            await logout();
            router.push("/login");
          }}
        >
          خروج
        </button>
      </aside>
      <main className="p-6">{children}</main>
    </div>
  );
}

export function Loading() {
  return <p className="text-white/50">در حال بارگذاری…</p>;
}

export function ErrorBox({ message }: { message: string }) {
  if (!message) return null;
  return (
    <div className="mb-4 rounded-xl border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">
      {message}
    </div>
  );
}

export function EmptyBox({ message }: { message: string }) {
  return <p className="rounded-xl border border-line p-6 text-center text-white/50">{message}</p>;
}
