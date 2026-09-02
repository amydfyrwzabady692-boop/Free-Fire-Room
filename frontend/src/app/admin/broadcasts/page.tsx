"use client";

import { useState } from "react";
import { ADMIN_NAV, ErrorBox, Shell } from "@/components/Shell";
import { api } from "@/lib/api";

export default function Page() {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [id, setId] = useState("");
  const [sent, setSent] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function create() {
    setErr("");
    setMsg("");
    if (title.trim().length < 3 || body.trim().length < 3) {
      setErr("عنوان و متن هر کدام حداقل ۳ حرف باشند.");
      return;
    }
    setBusy(true);
    try {
      const row = await api("/admin/broadcasts", {
        method: "POST",
        body: JSON.stringify({ title: title.trim(), body: body.trim() }),
      });
      setId(row.id);
      setSent(false);
      setMsg("پیش‌نویس ساخته شد. متن را یک بار بخوانید و بعد ارسال نهایی را بزنید.");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmSend() {
    setErr("");
    if (!confirm("این پیام برای همهٔ کاربران ارسال می‌شود و قابل بازگشت نیست. مطمئن هستید؟")) return;
    setBusy(true);
    try {
      await api(`/admin/broadcasts/${id}/confirm`, { method: "POST" });
      // the button disables itself: confirming twice used to send everything twice
      setSent(true);
      setMsg("ارسال شروع شد.");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="پنل مالک ربات" items={ADMIN_NAV}>
      <h1 className="mb-4 text-2xl font-bold">ارسال همگانی</h1>
      <ErrorBox message={err} />
      <div className="card space-y-3">
        <label className="block text-sm text-white/60">
          عنوان داخلی (برای کاربر ارسال نمی‌شود)
          <input
            className="mt-1"
            value={title}
            disabled={busy || !!id}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label className="block text-sm text-white/60">
          متنی که کاربرها می‌بینند
          <textarea
            className="mt-1"
            rows={6}
            value={body}
            disabled={busy || !!id}
            onChange={(e) => setBody(e.target.value)}
          />
        </label>
        {!id && (
          <button className="btn" onClick={create} disabled={busy}>
            ساخت پیش‌نویس
          </button>
        )}
        {id && !sent && (
          <>
            <div className="rounded-xl border border-line p-3 text-sm whitespace-pre-wrap">{body}</div>
            <button className="btn" onClick={confirmSend} disabled={busy}>
              تأیید نهایی و ارسال
            </button>
            <button
              className="btn-ghost"
              disabled={busy}
              onClick={() => {
                setId("");
                setMsg("");
              }}
            >
              ویرایش دوباره
            </button>
          </>
        )}
        {sent && (
          <button
            className="btn-ghost"
            onClick={() => {
              setId("");
              setSent(false);
              setTitle("");
              setBody("");
              setMsg("");
            }}
          >
            ارسال همگانی جدید
          </button>
        )}
        {msg && <p className="text-sm text-green-300">{msg}</p>}
      </div>
    </Shell>
  );
}
