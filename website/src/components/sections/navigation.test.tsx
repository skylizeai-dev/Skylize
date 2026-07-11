import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Navigation } from "@/components/sections/navigation";

afterEach(cleanup);

describe("Navigation mobile menu", () => {
  it("exposes real /console/login sign-in links (not placeholder #)", () => {
    render(<Navigation />);
    const signIns = screen.getAllByRole("link", { name: "Sign in" });
    expect(signIns.length).toBeGreaterThan(0);
    for (const link of signIns) {
      expect(link).toHaveAttribute("href", "/console/login");
    }
  });

  it("opens a labelled modal dialog and moves focus into it", async () => {
    render(<Navigation />);
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));

    const dialog = screen.getByRole("dialog", { name: "Site menu" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    // focus moves to the first menu link on open
    await waitFor(() =>
      expect(dialog.querySelector("a")).toHaveFocus(),
    );
  });

  it("closes on Escape and returns focus to the trigger", async () => {
    render(<Navigation />);
    const trigger = screen.getByRole("button", { name: "Open menu" });
    fireEvent.click(trigger);
    await screen.findByRole("dialog", { name: "Site menu" });

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Site menu" })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Open menu" })).toHaveFocus();
  });
});
