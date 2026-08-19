import type { Metadata, Viewport } from "next";
import { Inter, Inter_Tight, Geist_Mono } from "next/font/google";
import { MotionProvider } from "@/components/skylize/motion-provider";
import { SITE_URL } from "@/lib/site";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const interTight = Inter_Tight({
  variable: "--font-inter-tight",
  subsets: ["latin"],
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "Skylize — Governance Layer for AI Agents",
    template: "%s · Skylize",
  },
  description:
    "A cryptographic permission layer for AI agents. Every action carries a signed token naming its scope, budget ceiling, and expiry — verified at the call site before it reaches your systems.",
  keywords: [
    "AI agent governance",
    "agent permissions",
    "AI audit trail",
    "agent kill switch",
    "AI budget enforcement",
    "human in the loop approval",
  ],
  authors: [{ name: "Skylize" }],
  creator: "Skylize",
  openGraph: {
    type: "website",
    locale: "en_US",
    url: SITE_URL,
    siteName: "Skylize",
    title: "Skylize — Governance Layer for AI Agents",
    description:
      "A cryptographic permission layer for AI agents: signed authority, enforced budgets, approval gates, and a replayable audit trail.",
  },
  twitter: {
    card: "summary_large_image",
    title: "Skylize — Governance Layer for AI Agents",
    description:
      "A cryptographic permission layer for AI agents: signed authority, enforced budgets, approval gates, and a replayable audit trail.",
  },
  robots: {
    index: true,
    follow: true,
  },
};

export const viewport: Viewport = {
  themeColor: "#08090A",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${interTight.variable} ${geistMono.variable} h-full`}
    >
      <body className="min-h-full bg-background text-foreground antialiased">
        <MotionProvider>{children}</MotionProvider>
      </body>
    </html>
  );
}
