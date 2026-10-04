import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FieldNet Monitor Console",
  description: "Sensor Network & Gateway Status Engine Operator Console",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-950 text-slate-100 antialiased">
        {children}
      </body>
    </html>
  );
}
