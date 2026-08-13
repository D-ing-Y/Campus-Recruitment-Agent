import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Campus Agent · 候选人画像工作台",
  description: "从简历证据到可追溯候选人能力画像的本地 Agent 工作台。",
  openGraph: {
    title: "Campus Agent · 候选人画像工作台",
    description: "从简历证据到可追溯候选人能力画像。",
    images: [{ url: "/og-candidate-profile.png", width: 1200, height: 630 }],
  },
  twitter: {
    card: "summary_large_image",
    images: ["/og-candidate-profile.png"],
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
