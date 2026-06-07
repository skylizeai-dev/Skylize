# Cult UI Pattern Analysis for Skylize

> Cult UI is a collection of high-craft, animation-forward React components built for bold product-led SaaS brands. This document extracts transferable patterns for Skylize without direct copying — adapted to the enterprise-grade, precision aesthetic Skylize requires.

---

## 1. Spacing Philosophy

**What Cult UI does:**
- Uses extreme whitespace — padding values of 80–120px on sections
- Tight inner spacing (4–8px) within components, massive gaps between sections
- Content blocks feel "breathing" — nothing is compressed

**Skylize recommendation:**
- Section vertical padding: `py-24 md:py-32 lg:py-40`
- Component inner padding: `p-6` standard, `p-8` for hero/feature cards
- Gap between grid items: `gap-6 md:gap-8`
- Use `space-y-4` within cards, `space-y-16` between section subsystems

---

## 2. Typography Patterns

**What Cult UI does:**
- Large display type — 72px–96px on hero headlines with tight tracking (`-0.04em`)
- Heavy weight contrast: bold display + light body copy
- Mono for technical/data elements
- Gradient text on key words only — never whole paragraphs
- Mix of serif display (luxury) with sans body (utility)

**Skylize recommendation:**
- Hero headlines: `text-6xl md:text-7xl lg:text-8xl font-bold tracking-tight`
- Body copy: `text-lg text-muted-foreground leading-relaxed`
- Technical labels: `font-mono text-sm`
- Gradient accent: apply sparingly to 1–2 words in a headline
- Do not use serif — Skylize is precision-forward, not luxury-editorial

---

## 3. Card Patterns

**What Cult UI does:**
- Cards have 1px borders with subtle background (`background: rgba(255,255,255,0.03)`)
- Strong hover states — border brightens, slight translateY(-2px), shadow emerges
- Content stacked vertically: icon → title → description → CTA arrow
- No box shadows on resting state — border is the affordance
- Cards rarely have images; rely on iconography + color accents

**Skylize recommendation:**
- Resting card: `border border-border bg-card rounded-xl`
- Hover: `hover:border-primary/40 hover:-translate-y-0.5 hover:shadow-lg transition-all duration-200`
- Icon placement: top-left, 40×40px container with subtle tinted background
- CTA: ghost link with arrow `→` — never a full button inside a card grid
- Glassmorphism optional: `bg-white/5 backdrop-blur-sm` for dark mode overlays

---

## 4. Interaction Patterns

**What Cult UI does:**
- Spring-based animations (Framer Motion `type: "spring"`) on mount and hover
- Staggered children — list items appear 50–100ms apart
- Cursor-tracked magnetic effects on CTAs
- Scroll-triggered reveals using Intersection Observer or Framer `whileInView`
- No abrupt transitions — everything eases, fades, or springs

**Skylize recommendation:**

### Scroll reveal (standard):
```tsx
<motion.div
  initial={{ opacity: 0, y: 20 }}
  whileInView={{ opacity: 1, y: 0 }}
  viewport={{ once: true }}
  transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
>
```

### Stagger container:
```tsx
const container = {
  hidden: {},
  show: { transition: { staggerChildren: 0.08 } }
};
const item = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] } }
};
```

### Button hover lift:
```tsx
whileHover={{ scale: 1.02, y: -1 }}
whileTap={{ scale: 0.98 }}
transition={{ type: "spring", stiffness: 400, damping: 20 }}
```

---

## 5. Recommended Easing Curves

| Name | Curve | Use case |
|------|-------|----------|
| Expo out | `[0.16, 1, 0.3, 1]` | Entrance animations |
| Smooth | `[0.4, 0, 0.2, 1]` | State transitions |
| Snappy spring | `stiffness: 400, damping: 30` | Interactive feedback |
| Gentle spring | `stiffness: 200, damping: 40` | Hover lifts |

---

## 6. Dark Mode Strategy

**Cult UI observation:**
- Dark mode is primary — components are designed dark-first
- Background layers: `#0a0a0a` → `#111111` → `#1a1a1a` (depth through subtle value steps)
- Borders at 8–12% white opacity: `rgba(255,255,255,0.08)`
- Text: near-white `#fafafa` primary, `#a1a1aa` muted
- Never pure black backgrounds — use `#09090b` (zinc-950)

**Skylize recommendation:**
- Dark bg: `bg-zinc-950` (`#09090b`) as page background
- Cards: `bg-zinc-900` with `border-zinc-800`
- Muted text: `text-zinc-400`
- Accent glow: subtle radial gradient behind hero sections

---

## 7. Altitude Line Concept (Skylize-specific)

Inspired by Cult UI's use of precise 0.5px lines as section dividers and spatial anchors:
- Use a custom `AltitudeLine` component (see `/components/skylize/altitude-line.tsx`)
- Lines should be `0.5px` height, full-width, with configurable opacity
- Create visual "floors" between sections — the line signals a new altitude of information
- Can be horizontal or vertical — vertical variant for column separators in feature grids

---

## 8. What NOT to import from Cult UI

- Playful rounded bubble components — Skylize is enterprise-grade
- Heavy particle systems / canvas animations — performance risk, off-brand
- Hand-drawn or sketchy illustration styles — misaligns with precision brand
- Oversized emoji or mascot elements
- Chunky, rounded-corner cards (prefer sharp-to-medium radius: `rounded-lg` or `rounded-xl`, not `rounded-3xl`)

---

## Summary

Cult UI's core value for Skylize lies in:
1. **Generous breathing room** — adopt extreme whitespace discipline
2. **Precision micro-interactions** — spring animations on every user interaction
3. **Depth through value steps** — dark backgrounds differentiated by 5–8% lightness
4. **Typography contrast** — oversized display type + minimal body
5. **Honest affordances** — hover states that feel physical and immediate

These patterns will be baked into the design tokens (`/styles/tokens.css`) and the Skylize component library rather than copied directly.
