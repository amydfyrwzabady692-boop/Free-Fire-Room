"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [telegramId, setTelegramId] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [otp, setOtp] = useState("");
  const [mode, setMode] = useState<"password" | "otp">("password");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body =
        mode === "password"
          ? { telegram_id: Number(telegramId), password, totp_code: totp || null }
          : { telegram_id: Number(telegramId), code: otp };
      const path = mode === "password" ? "/auth/login/password" : "/auth/login/otp";
      const data = await api(path, { method: "POST", body: JSON.stringify(body) });
      localStorage.setItem("ff_token", data.access_token);
      const me = await api("/auth/me");
      if ((me.roles || []).includes("super_admin") || (me.roles || []).includes("admin")) {
        router.push("/admin");
      } else {
        router.push("/organizer");
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function requestOtp() {
    setError("");
    try {
      await api(`/auth/otp/request?telegram_id=${encodeURIComponent(telegramId)}`, { method: "POST" });
      setMode("otp");
    } catch (err: any) {
      setError(err.message);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center p-6">
      <div className="card space-y-4">
        <h1 className="text-2xl font-bold">ورود پنل</h1>
        <p className="text-sm text-white/60">
          این سامانه شریک رسمی Garena نیست. ورود Super Admin با رمز و 2FA؛ برگزارکننده با کد یک‌بارمصرف ربات.
        </p>
        <form onSubmit={submit} className="space-y-3">
          <input placeholder="Telegram ID" value={telegramId} onChange={(e) => setTelegramId(e.target.value)} />
          {mode === "password" ? (
            <>
              <input type="password" placeholder="رمز عبور" value={password} onChange={(e) => setPassword(e.target.value)} />
              <input placeholder="کد 2FA (اگر فعال است)" value={totp} onChange={(e) => setTotp(e.target.value)} />
            </>
          ) : (
            <input placeholder="کد یک‌بارمصرف ربات" value={otp} onChange={(e) => setOtp(e.target.value)} />
          )}
          {error && <div className="text-sm text-red-400">{error}</div>}
          <button className="btn w-full" disabled={busy}>
            {busy ? "در حال ورود..." : "ورود"}
          </button>
        </form>
        <button className="btn-ghost w-full" onClick={requestOtp}>
          ارسال کد به ربات
        </button>
      </div>
    </div>
  );
}
