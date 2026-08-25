# Skylize — Frontend

The marketing site for Skylize: operational infrastructure and AI systems that
take manual work off the team. Built to feel like a category-defining
infrastructure company — precise, restrained, premium.

## Design language — Precision Altitude

| Principle | Implementation |
|-----------|----------------|
| **Palette** | Exactly three values — ink `#08090A`, paper `#F7F8F8`, blue `#0047FF`. Every grey is derived from ink + paper with `color-mix`, so the palette never drifts. Dark-first, single identity (no theme toggle). |
| **The Altitude Line** | The signature 0.5px hairline. Lives in `components/skylize/altitude-line.tsx` (`fade` / `solid` / `accent` variants) and recurs in the nav, hero, cards, the dashboard, How-It-Works track, and footer. |
| **Typography** | `Inter Tight` for display headlines (Neue Haas Grotesk Display analog), `Inter` for body, `Geist Mono` for technical labels. Type carries the design — large, tight, aggressive whitespace. |
| **Motion — Precision Ascent** | Everything rises into place on `cubic-bezier(0.16, 1, 0.3, 1)`. Never bounces, never falls. Framer Motion only; tokens in `lib/motion.ts`. Honors `prefers-reduced-motion`. |

No gradients (flat fills only), no glassmorphism, no glow, no neon.

## Stack

- **Next.js 16** (App Router, Turbopack) · **React 19** · **TypeScript**
- **Tailwind CSS v4** (`@theme` tokens in `app/globals.css`)
- **Framer Motion** for all animation
- **base-ui** + shadcn-style primitives for accessible UI

## Structure

```
src/
├── app/
│   ├── globals.css        # brand theme tokens + base layer
│   └── layout.tsx         # fonts, metadata, viewport
├── components/
│   ├── skylize/           # design-system primitives
│   │   ├── altitude-line  # the signature hairline
│   │   ├── container      # measure + section rhythm
│   │   └── eyebrow / counter / cta-button / reveal / logo
│   └── sections/          # the 9 landing-page sections
│       ├── navigation / hero / hero-workflow-demo
│       ├── problem / solution / how-it-works / controls
│       └── status / faq / final-cta / footer
├── hooks/                 # use-intersection, use-media-query
├── lib/                   # cn(), motion tokens
└── styles/tokens.css      # supplemental (timing, z-index, rhythm)
```

The hero centerpiece (`sections/hero-workflow-demo.tsx`) reuses the REAL
console components — the same build dial and ActionCard — driven by a scripted
local timeline. It is provably inert: no fetch, no live build controller, only
pure routing helpers. That is deliberate, and the reason it may simulate
timing: it is the one surface with no backend action behind it.

Public copy carries no customer names, no measured results, and no compliance
badges. Trust signals on this site must be properties of the architecture
(signed tokens, kill switch, audit trail), which hold before the first
customer exists. See `sections/controls.tsx` for the standing rule.

## Develop

```bash
npm install
npm run dev      # http://localhost:3000
npm run build    # production build (statically prerendered)
npm run lint
```
