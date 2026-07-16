import { useEffect, useState } from "react";
import {
  ask,
  getExamples,
  getMenu,
  placeOrder,
  type AskResult,
  type MenuItem,
  type OrderResult,
} from "./api";

const MAX_QTY = 20;

export default function App() {
  const [shop, setShop] = useState("Tony's Pizza");
  const [examples, setExamples] = useState<string[]>([]);
  const [menu, setMenu] = useState<MenuItem[]>([]);
  const [cart, setCart] = useState<Record<number, number>>({});
  const [name, setName] = useState("");
  const [placing, setPlacing] = useState(false);
  const [receipt, setReceipt] = useState<OrderResult | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [menuOpen, setMenuOpen] = useState(false);

  // query state
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<AskResult | null>(null);

  useEffect(() => {
    getExamples().then((d) => { setShop(d.shop); setExamples(d.examples); }).catch(() => {});
    getMenu().then(setMenu).catch(() => {});
  }, []);

  function setQty(id: number, qty: number, cap: number) {
    const q = Math.max(0, Math.min(qty, cap));
    setCart((c) => {
      const next = { ...c };
      if (q === 0) delete next[id];
      else next[id] = q;
      return next;
    });
  }

  const cartItems = Object.entries(cart).map(([id, qty]) => ({ id: Number(id), qty }));
  const cartCount = cartItems.reduce((n, x) => n + x.qty, 0);
  const cartTotal = cartItems.reduce((t, x) => {
    const item = menu.find((m) => m.id === x.id);
    return t + (item ? item.price * x.qty : 0);
  }, 0);

  async function submitOrder() {
    if (cartCount === 0 || placing) return;
    setPlacing(true);
    setReceipt(null);
    try {
      const res = await placeOrder(
        cartItems.map((x) => ({ menu_item_id: x.id, quantity: x.qty })),
        name,
      );
      setReceipt(res);
      if (res.ok) {
        setCart({});
        getMenu().then(setMenu).catch(() => {}); // refresh stock
      }
    } catch (e) {
      setReceipt({ ok: false, error: e instanceof Error ? e.message : "Order failed." });
    } finally {
      setPlacing(false);
    }
  }

  async function run(q: string) {
    const query = q.trim();
    if (!query || loading) return;
    setQuestion(query);
    setLoading(true);
    setError("");
    setResult(null);
    try {
      setResult(await ask(query));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  const categories = [...new Set(menu.map((m) => m.category))];

  return (
    <div className="wrap">
      <header>
        <a className="backlink" href="https://rakimfrancis.com">← rakimfrancis.com</a>
        <h1>{shop}</h1>
        <p className="tag">
          Place an order, then ask about it in plain English. Your question becomes
          SQL and runs live against the database.
        </p>
      </header>

      <section className="order">
        <button
          type="button"
          className="menu-toggle"
          onClick={() => setMenuOpen((v) => !v)}
          aria-expanded={menuOpen}
        >
          <span className="cat-chevron">{menuOpen ? "▾" : "▸"}</span>
          <span className="menu-toggle-name">Menu</span>
          <span className="cat-count">
            {menu.length} item{menu.length === 1 ? "" : "s"}
            {cartCount > 0 ? ` · ${cartCount} in order` : ""}
          </span>
        </button>

        {menuOpen && (<>
        {categories.map((cat) => {
          const items = menu.filter((m) => m.category === cat);
          const inOrder = items.reduce((n, m) => n + (cart[m.id] ?? 0), 0);
          const isOpen = open[cat] ?? false;
          return (
            <div key={cat} className="cat">
              <button
                type="button"
                className="cat-header"
                onClick={() => setOpen((o) => ({ ...o, [cat]: !o[cat] }))}
                aria-expanded={isOpen}
              >
                <span className="cat-chevron">{isOpen ? "▾" : "▸"}</span>
                <span className="cat-name">{cat}</span>
                <span className="cat-count">
                  {items.length} item{items.length > 1 ? "s" : ""}
                  {inOrder > 0 ? ` · ${inOrder} in order` : ""}
                </span>
              </button>
              {isOpen && (
                <div className="cat-items">
                  {items.map((m) => {
                    const qty = cart[m.id] ?? 0;
                    const cap = Math.min(m.in_stock, MAX_QTY);
                    return (
                      <div key={m.id} className="item">
                        <div className="item-main">
                          <span className="item-name">{m.name}</span>
                          <span className="item-price">${m.price.toFixed(2)}</span>
                          <span className="item-stock">{m.in_stock} left</span>
                        </div>
                        <div className="stepper">
                          <button onClick={() => setQty(m.id, qty - 1, cap)} disabled={qty === 0}>−</button>
                          <span className="qty">{qty}</span>
                          <button onClick={() => setQty(m.id, qty + 1, cap)} disabled={qty >= cap}>+</button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}

        <div className="checkout">
          <input
            className="name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your name (optional)"
            maxLength={30}
          />
          <button className="place" onClick={submitOrder} disabled={cartCount === 0 || placing}>
            {placing
              ? "Placing…"
              : cartCount === 0
                ? "Add items to order"
                : `Place order · ${cartCount} item${cartCount > 1 ? "s" : ""} · $${cartTotal.toFixed(2)}`}
          </button>
        </div>
        </>)}

        {receipt &&
          (receipt.ok ? (
            <div className="receipt">
              <div className="receipt-head">
                Order #{receipt.order_id} placed
                {receipt.customer && receipt.customer !== "Guest" ? ` for ${receipt.customer}` : ""} 🎉
              </div>
              <ul>
                {receipt.lines?.map((l, i) => (
                  <li key={i}>
                    <span>{l.quantity} × {l.name}</span>
                    <span className="sub">${l.subtotal.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
              <div className="receipt-total">Total ${receipt.total?.toFixed(2)}</div>
              <div className="receipt-nudge">
                Now ask about it below. Try "what's in the most recent order?"
              </div>
            </div>
          ) : (
            <div className="order-error">{receipt.error}</div>
          ))}
      </section>

      <section className="query">
        <h2 className="section-title">Ask the data</h2>
        <form
          className="askbar"
          onSubmit={(e) => {
            e.preventDefault();
            run(question);
          }}
        >
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="e.g. which category makes the most money?"
          />
          <button type="submit" disabled={loading}>
            {loading ? "Thinking…" : "Ask"}
          </button>
        </form>

        {examples.length > 0 && (
          <div className="examples">
            {examples.map((ex) => (
              <button key={ex} className="chip" onClick={() => run(ex)} disabled={loading}>
                {ex}
              </button>
            ))}
          </div>
        )}

        {error && <div className="error">{error}</div>}

        {result && (
          <div className="result">
            {result.interpretation && (
              <p className="interp">
                <span className="label">Reading that as</span>
                {result.interpretation}
              </p>
            )}
            {!result.ok ? (
              <div className="clarify">{result.clarify}</div>
            ) : (
              <>
                {result.sql && (
                  <pre className="sql">
                    <code>{result.sql}</code>
                  </pre>
                )}
                <Table columns={result.columns ?? []} rows={result.rows ?? []} />
              </>
            )}
          </div>
        )}
      </section>

      <section className="query">
        <h2 className="section-title">How this demo defends itself</h2>
        <p className="tag">
          A public LLM with database access is the thing engineering teams worry
          about, so every layer here assumes the one above it can fail:
        </p>
        <p className="tag">
          <strong>The model only proposes.</strong> Code checks every query is a
          single read-only SELECT before it runs, and the connection itself uses a
          read-only database role, so a write would be refused twice.
        </p>
        <p className="tag">
          <strong>Grounded to the schema.</strong> The model works from an explicit
          field catalog and can't invent columns; an ambiguous question gets a
          clarifying question back, not a guess.
        </p>
        <p className="tag">
          <strong>Nothing hidden.</strong> The exact SQL that ran is shown with
          every answer.
        </p>
        <p className="tag">
          <strong>Constrained input.</strong> Ordering is menu-driven; the single
          free-text field is filtered and length-capped.
        </p>
        <p className="tag">
          <strong>Bounded worst case.</strong> Per-visitor and global daily limits,
          capped result sizes, and an API key on a small prepaid balance. If every
          layer failed, the demo would pause until tomorrow, an inconvenience, not
          an incident.
        </p>
      </section>
    </div>
  );
}

function Table({
  columns,
  rows,
}: {
  columns: string[];
  rows: Record<string, unknown>[];
}) {
  if (rows.length === 0) return <p className="norows">No rows matched.</p>;
  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c}>{format(row[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function format(v: unknown): string {
  if (v === null || v === undefined) return "-";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}
