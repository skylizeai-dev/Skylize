import type { Metadata, Viewport } from "next";
import { Inter, Inter_Tight, Geist_Mono } from "next/font/google";
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

const SITE_URL = "https://skylize.com";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "Skylize — Operational Altitude for Modern Companies",
    template: "%s · Skylize",
  },
  description:
    "AI systems and operational infrastructure that eliminate repetitive work and unlock growth. Less manual work. More revenue. More scale.",
  keywords: [
    "AI infrastructure",
    "operational systems",
    "AI agents",
    "workflow automation",
    "revenue operations",
    "enterprise AI",
  ],
  authors: [{ name: "Skylize" }],
  creator: "Skylize",
  openGraph: {
    type: "website",
    locale: "en_US",
    url: SITE_URL,
    siteName: "Skylize",
    title: "Skylize — Operational Altitude for Modern Companies",
    description:
      "AI systems and operational infrastructure that eliminate repetitive work and unlock growth.",
  },
  twitter: {
    card: "summary_large_image",
    title: "Skylize — Operational Altitude",
    description:
      "AI systems and operational infrastructure that eliminate repetitive work and unlock growth.",
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
        {children}
      </body>
    </html>
  );
}
