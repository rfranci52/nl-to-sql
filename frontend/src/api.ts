// Thin client for the FastAPI backend. Same shape as engine.answer().

export interface AskResult {
  ok: boolean;
  interpretation?: string;
  sql?: string;
  columns?: string[];
  rows?: Record<string, unknown>[];
  clarify?: string;
}

export async function ask(question: string): Promise<AskResult> {
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return res.json();
}

export interface Examples {
  shop: string;
  examples: string[];
}

export async function getExamples(): Promise<Examples> {
  const res = await fetch("/api/examples");
  if (!res.ok) throw new Error("Couldn't load examples");
  return res.json();
}
