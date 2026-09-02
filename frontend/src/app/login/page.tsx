"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ErrorBox } from "@/components/Shell";
import { api, saveTokens } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [telegramId, setTelegramId] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [otp, setOtp] = useState("");
  const [mode, setMode] = useState<"password" | "otp">("password");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const numeric = (value: string) => /^[0-9]+$/.test(value.trim());

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (!numeric(telegramId)) throw new Error("شناسه تلگرام باید عدد باشد.");
      const body =
        mode === "password"
          ? { telegram_id: Number(telegramId), password, totp_code: totp || null }
          : { telegram_id: Number(telegramId), code: otp };
      const path = mode === "password" ? "/auth/login/password" : "/auth/login/otp";
      const data = await api(path, { method: "POST", body: JSON.stringify(body) });
      saveTokens(data);
      const me = await api("/auth/me");
      const roles = me.roles || [];
      router.push(roles.includes("super_admin") || roles.includes("admin") ? "/admin" : "/organizer");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function requestOtp() {
    setError("");
    setNote("");
    setBusy(true);
    try {
      if (!numeric(telegramId)) throw new Error("اول شناسه عددی تلگرام را وارد کنید.");
      await api("/auth/otp/request", {
        method: "POST",
        body: JSON.stringify({ telegram_id: Number(telegramId) }),
      });
      setMode("otp");
      setNote("اگر این شناسه ربات را استارت کرده باشد، کد ورود در تلگرام برایش ارسال شد.");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center p-6">
      <div className="card space-y-4">
        <h1 className="text-2xl font-bold">ورود پنل</h1>
        <p className="text-sm text-white/60">
          این سامانه شریک رسمی Garena نیست. ورود مالک ربات با رمز و ۲FA؛ برگزارکننده با کد یک‌بارمصرف ربات.
        </p>
        <form onSubmit={submit} className="space-y-3">
          <input
            placeholder="شناسه عددی تلگرام"
            inputMode="numeric"
            value={telegramId}
            onChange={(e) => setTelegramId(e.target.value)}
          />
          {mode === "password" ? (
            <>
              <input
                type="password"
                placeholder="رمز عبور"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <input
                placeholder="کد ۲FA (اگر فعال است)"
                inputMode="numeric"
                value={totp}
                onChange={(e) => setTotp(e.target.value)}
              />
            </>
          ) : (
            <input
              placeholder="کد یک‌بارمصرف ربات"
              inputMode="numeric"
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
            />
          )}
          <ErrorBox message={error} />
          {note && <div className="text-sm text-green-300">{note}</div>}
          <button className="btn w-full" disabled={busy}>
            {busy ? "در حال ورود…" : "ورود"}
          </button>
        </form>
        <button className="btn-ghost w-full" type="button" disabled={busy} onClick={requestOtp}>
          ارسال کد یک‌بارمصرف به ربات
        </button>
        {mode === "otp" && (
          <button className="btn-ghost w-full" type="button" onClick={() => setMode("password")}>
            بازگشت به ورود با رمز
          </button>
        )}
      </div>
    </div>
  );
}
