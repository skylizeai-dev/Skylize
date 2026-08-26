import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SiteHeader } from "@/components/site/site-header";

afterEach(cleanup);

describe("SiteHeader", () => {
  it("does not expose a sign-in link", () => {
    render(<SiteHeader />);
    expect(screen.queryByRole("link", { name: "Sign in" })).not.toBeInTheDocument();
  });

  it("links only to destinations that exist", () => {
    render(<SiteHeader />);
    // Every href is either an anchor on the home page or one of the routes
    // this app actually serves. A link to a page we have not written is the
    // regression this guards against.
    const allowed = new Set([
      "/",
      "/#how",
      "/#controls",
      "/#status",
      "/#faq",
      "/#apply",
      "/console-preview",
      "/my-day",
    ]);
    for (const link of screen.getAllByRole("link")) {
      expect(allowed).toContain(link.getAttribute("href"));
    }
  });

  it("marks the current page for assistive technology", () => {
    render(<SiteHeader current="my-day" />);
    const current = screen
      .getAllByRole("link", { name: "My Day" })
      .filter((el) => el.getAttribute("aria-current") === "page");
    expect(current.length).toBeGreaterThan(0);
  });

  it("opens a labelled modal dialog and moves focus into it", async () => {
    render(<SiteHeader />);
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));

    const dialog = screen.getByRole("dialog", { name: "Site menu" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    await waitFor(() => expect(dialog.querySelector("a")).toHaveFocus());
  });

  it("closes on Escape and returns focus to the trigger", async () => {
    render(<SiteHeader />);
    const trigger = screen.getByRole("button", { name: "Open menu" });
    fireEvent.click(trigger);
    await screen.findByRole("dialog", { name: "Site menu" });

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Site menu" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Open menu" })).toHaveFocus();
  });
});
