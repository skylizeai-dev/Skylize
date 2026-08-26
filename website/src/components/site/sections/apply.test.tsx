import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Apply } from "@/components/site/sections/apply";

afterEach(() => {
  cleanup();
});

describe("Apply", () => {
  it("renders a direct mailto link instead of a form", () => {
    render(<Apply />);

    expect(screen.getByText("Apply as a Design Partner")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "skylize.ai@gmail.com" });
    expect(link).toHaveAttribute("href", "mailto:skylize.ai@gmail.com");
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
  });
});
