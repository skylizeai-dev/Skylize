import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Apply } from "@/components/site/sections/apply";

function mockFetch(response: { ok: boolean; status?: number; body?: unknown }) {
  const fn = vi.fn().mockResolvedValue({
    ok: response.ok,
    status: response.status ?? (response.ok ? 200 : 500),
    json: async () => response.body ?? {},
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function fill(values: Partial<Record<string, string>>) {
  for (const [label, value] of Object.entries(values)) {
    fireEvent.change(screen.getByLabelText(label), {
      target: { value },
    });
  }
}

function submit() {
  fireEvent.submit(document.querySelector("form")!);
}

const complete = {
  Name: "Dana Okonkwo",
  "Work email": "ops@acme.com",
  Company: "Acme",
  "What your agents do": "They reconcile invoices against the ledger.",
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Apply", () => {
  it("posts the full application to Web3Forms and reports success", async () => {
    const fetchMock = mockFetch({ ok: true, body: { success: true } });
    render(<Apply />);
    fill(complete);
    submit();

    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent(/received/i);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.web3forms.com/submit");
    expect(init.method).toBe("POST");

    const payload = JSON.parse(init.body as string) as Record<string, string>;
    expect(payload.name).toBe("Dana Okonkwo");
    expect(payload.email).toBe("ops@acme.com");
    expect(payload.company).toBe("Acme");
    expect(payload.message).toMatch(/reconcile invoices/i);
  });

  it("shows the API's error message and keeps the form on a non-2xx response", async () => {
    mockFetch({ ok: false, status: 400, body: { success: false, message: "Invalid email address" } });
    render(<Apply />);
    fill(complete);
    submit();

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Invalid email address"),
    );
    expect(screen.getByLabelText("Work email")).toBeInTheDocument();
  });

  it("surfaces a message when the request itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<Apply />);
    fill(complete);
    submit();

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByLabelText("Work email")).toBeInTheDocument();
  });

  it("does not call the API when a required field is blank", () => {
    const fetchMock = mockFetch({ ok: true, body: { success: true } });
    render(<Apply />);
    fill({ ...complete, Name: "   " });
    submit();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
