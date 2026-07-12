/**
 * Canonical site origin — the single source of truth for metadata, robots,
 * and the sitemap, so they can never advertise different domains again.
 * Override per environment with NEXT_PUBLIC_SITE_URL.
 */
export const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://skylize.ai";
