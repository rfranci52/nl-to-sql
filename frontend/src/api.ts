// Thin client for the FastAPI backend. Same shapes as the engine + orders modules.

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

export interface MenuItem {
  id: number;
  name: string;
  category: string;
  price: number;
  in_stock: number;
}

export async function getMenu(): Promise<MenuItem[]> {
  const res = await fetch("/api/menu");
  if (!res.ok) throw new Error("Couldn't load the menu");
  return (await res.json()).menu;
}

export interface OrderLine {
  name: string;
  quantity: number;
  price: number;
  subtotal: number;
}

export interface OrderResult {
  ok: boolean;
  order_id?: number;
  customer?: string;
  lines?: OrderLine[];
  total?: number;
  error?: string;
}

export async function placeOrder(
  items: { menu_item_id: number; quantity: number }[],
  name: string,
): Promise<OrderResult> {
  const res = await fetch("/api/order", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items, name }),
  });
  if (!res.ok) throw new Error(`Order failed (${res.status})`);
  return res.json();
}
