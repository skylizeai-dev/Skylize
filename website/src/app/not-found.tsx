import type { Metadata } from "next";

import { Container } from "@/components/skylize/container";
import { CtaButton } from "@/components/skylize/cta-button";
import { Eyebrow } from "@/components/skylize/eyebrow";

export const metadata: Metadata = {
  title: "Page not found",
  robots: { index: false },
};

export default function NotFound() {
  return (
    <main className="flex min-h-svh items-center">
      <Container>
        <Eyebrow index="404">Not found</Eyebrow>
        <h1 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
          This altitude is unmapped.
        </h1>
        <p className="mt-4 max-w-md text-muted-foreground">
          The page you&rsquo;re looking for doesn&rsquo;t exist or has moved.
        </p>
        <div className="mt-8">
          <CtaButton href="/" arrow>
            Return to the homepage
          </CtaButton>
        </div>
      </Container>
    </main>
  );
}
