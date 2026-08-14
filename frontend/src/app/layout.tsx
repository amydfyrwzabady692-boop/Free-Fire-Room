import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Free Fire Room | پنل مدیریت",
  description: "پنل مدیریت کاستوم جایزه‌دار — غیررسمی و مستقل از Garena",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fa" dir="rtl">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
