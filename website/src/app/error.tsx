"use client";

import { useEffect } from "react";

import { Container } from "@/components/skylize/container";
import { CtaButton } from "@/components/skylize/cta-button";
import { Eyebrow } from "@/components/skylize/eyebrow";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="flex min-h-svh items-center">
      <Container>
        <Eyebrow>Something went wrong</Eyebrow>
        <h1 className="mt-6 text-balance text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.02] tracking-[-0.03em] text-foreground">
          Turbulence at this altitude.
        </h1>
        <p className="mt-4 max-w-md text-muted-foreground">
          An unexpected error occurred
          {error.digest ? ` (ref ${error.digest})` : ""}. You can try again, or
          head back to level ground.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <CtaButton onClick={reset}>Try again</CtaButton>
          <CtaButton href="/" variant="secondary" arrow>
            Return to the homepage
          </CtaButton>
        </div>
      </Container>
    </main>
  );
}
