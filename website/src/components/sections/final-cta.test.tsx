import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FinalCta } from "@/components/sections/final-cta";

function mockFetch(response: { ok: boolean; status?: number; body?: unknown }) {
  const fn = vi.fn().mockResolvedValue({
    ok: response.ok,
    status: response.status ?? (response.ok ? 200 : 500),
    json: async () => response.body ?? {},
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function fillAndSubmit(email: string) {
  fireEvent.change(screen.getByLabelText("Work email"), {
    target: { value: email },
  });
  fireEvent.submit(document.querySelector("form")!);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FinalCta", () => {
  it("posts the email to /api/contact and shows the success state on 2xx", async () => {
    const fetchMock = mockFetch({ ok: true, body: { success: true } });
    render(<FinalCta />);
    fillAndSubmit("ops@acme.com");

    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent(/we.ll be in touch/i);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/contact");
    expect(init.method).toBe("POST");
    const payload = JSON.parse(init.body as string) as Record<string, string>;
    expect(payload.email).toBe("ops@acme.com");
    expect(payload.message).toMatch(/strategy call/i);
  });

  it("shows the API's error message and keeps the form on a non-2xx response", async () => {
    mockFetch({ ok: false, status: 400, body: { error: "Invalid email address" } });
    render(<FinalCta />);
    fillAndSubmit("ops@acme.com");

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Invalid email address"),
    );
    expect(screen.getByLabelText("Work email")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("surfaces a message when the request itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<FinalCta />);
    fillAndSubmit("ops@acme.com");

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByLabelText("Work email")).toBeInTheDocument();
  });

  it("does not call the API for a blank email", () => {
    const fetchMock = mockFetch({ ok: true });
    render(<FinalCta />);
    fillAndSubmit("   ");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
