"use client";

import { Marquee } from "@/components/magic-ui/marquee";
import { Container } from "@/components/skylize";

const clients = [
  "NORTHWIND",
  "MERIDIAN",
  "APEX LABS",
  "VANTAGE",
  "HELIX",
  "ORBITAL",
  "LUMEN",
  "COBALT",
];

export function ClientLogos() {
  return (
    <section className="border-y border-border py-14">
      <Container>
        <p className="text-center font-mono text-[11px] tracking-[0.2em] text-muted-foreground/80 uppercase">
          Operating systems behind teams scaling past manual work
        </p>
      </Container>

      <div
        className="relative mt-10"
        style={{
          maskImage:
            "linear-gradient(90deg, transparent, black 12%, black 88%, transparent)",
          WebkitMaskImage:
            "linear-gradient(90deg, transparent, black 12%, black 88%, transparent)",
        }}
      >
        <Marquee className="[--duration:48s] [--gap:4rem]" pauseOnHover>
          {clients.map((name) => (
            <span
              key={name}
              className="font-display text-xl font-medium tracking-tight text-foreground/45 transition-colors duration-300 hover:text-foreground/80"
            >
              {name}
            </span>
          ))}
        </Marquee>
      </div>
    </section>
  );
}
