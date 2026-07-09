import { describe, expect, it } from "vitest";

import { cn } from "@/lib/utils";

describe("cn", () => {
  it("merges conflicting tailwind classes, last one wins", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
  });

  it("drops falsy conditional values", () => {
    expect(cn("flex", false && "hidden", undefined, null)).toBe("flex");
  });

  it("flattens arrays and objects like clsx", () => {
    expect(cn(["text-sm", { "font-bold": true, italic: false }])).toBe("text-sm font-bold");
  });
});
