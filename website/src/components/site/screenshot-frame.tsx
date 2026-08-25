import Image from "next/image";
import { cn } from "@/lib/utils";

/**
 * A framed product screenshot with its provenance caption.
 *
 * The caption is not optional and is not a prop. Every product image on this
 * site is a seeded sample workspace, and saying so under every frame is the
 * same honesty the rest of the page is built on — a page selling verifiable
 * authority cannot imply a customer deployment it does not have. Making the
 * disclosure structural means it cannot be forgotten at a call site.
 *
 * Until a capture lands, the frame renders a placeholder in the same box, so
 * the layout is final before the assets are.
 */

export const SAMPLE_DATA_CAPTION = "Sample workspace, illustrative data.";

interface ScreenshotFrameProps {
  /** Public path of the capture. Omit to render the pending placeholder. */
  src?: string;
  alt?: string;
  /** Route the screen lives at, shown on the caption's right. */
  route: string;
  /** Why the capture is not here yet — shown inside the placeholder. */
  pendingNote?: string;
  /** Frame aspect ratio. Captures are 16/10 unless told otherwise. */
  ratio?: string;
  className?: string;
  /** The first frame on a page should not lazy-load. */
  priority?: boolean;
}

export function ScreenshotFrame({
  src,
  alt,
  route,
  pendingNote,
  ratio = "16 / 10",
  className,
  priority = false,
}: ScreenshotFrameProps) {
  return (
    <figure className={cn("mt-[clamp(1.75rem,3.5vw,3rem)]", className)}>
      <div
        className="relative overflow-hidden border border-border bg-card"
        style={{ aspectRatio: ratio }}
      >
        {src ? (
          <Image
            src={src}
            alt={alt ?? ""}
            fill
            priority={priority}
            sizes="(min-width: 1180px) 1116px, 100vw"
            className="object-cover object-top"
          />
        ) : (
          <PendingCapture note={pendingNote} />
        )}
      </div>

      <figcaption className="mt-3 flex flex-wrap justify-between gap-x-5 gap-y-2 font-mono text-[11px] tracking-[0.12em] text-muted-foreground uppercase">
        <span>{SAMPLE_DATA_CAPTION}</span>
        <span>{route}</span>
      </figcaption>
    </figure>
  );
}

/**
 * The placeholder that stands in for a capture that has not landed yet.
 * It states that it is a placeholder rather than dressing up as a screenshot —
 * a fake console frame here would be exactly the kind of proof-by-mockup this
 * page argues against.
 */
function PendingCapture({ note }: { note?: string }) {
  return (
    <div
      className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-8 text-center"
      style={{
        backgroundImage:
          "repeating-linear-gradient(45deg, transparent, transparent 9px, rgb(8 9 10 / 0.025) 9px, rgb(8 9 10 / 0.025) 18px)",
      }}
    >
      <span className="border border-border bg-card px-2.5 py-1 font-mono text-[10px] tracking-[0.16em] text-muted-foreground uppercase">
        Screenshot pending
      </span>
      {note ? (
        <span className="max-w-[46ch] text-sm leading-relaxed text-muted-foreground">
          {note}
        </span>
      ) : null}
    </div>
  );
}
