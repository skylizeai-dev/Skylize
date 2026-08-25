export type ContactSubmission = {
  name: string;
  email: string;
  message: string;
  company?: string;
};

/**
 * Web3Forms rejects server-side requests on the free plan (403 "Use our
 * API in client side"), so this posts directly from the browser. The
 * access key is meant to be public client-side per their docs.
 */
export async function submitToWeb3Forms(
  data: ContactSubmission,
  signal?: AbortSignal,
): Promise<void> {
  const accessKey = process.env.NEXT_PUBLIC_WEB3FORMS_ACCESS_KEY;
  if (!accessKey) {
    throw new Error("Contact form is not configured. Please try again later.");
  }

  const res = await fetch("https://api.web3forms.com/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      access_key: accessKey,
      subject: `New contact form submission from ${data.name}`,
      name: data.name,
      email: data.email,
      company: data.company || "(not provided)",
      message: data.message,
    }),
    signal,
  });

  const payload = (await res.json().catch(() => null)) as {
    success?: boolean;
    message?: string;
  } | null;

  if (!res.ok || !payload?.success) {
    throw new Error(payload?.message ?? "Something went wrong. Please try again.");
  }
}
