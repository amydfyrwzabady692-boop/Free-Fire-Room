"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

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
              className={`block rounded-xl px-3 py-2 ${path === it.href ? "bg-accent text-black" : "hover:bg-white/5"}`}
            >
              {it.label}
            </Link>
          ))}
        </nav>
        <button
          className="btn-ghost mt-8 w-full"
          onClick={() => {
            localStorage.removeItem("ff_token");
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
