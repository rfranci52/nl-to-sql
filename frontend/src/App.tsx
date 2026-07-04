import { useEffect, useState } from "react";
import { ask, getExamples, type AskResult } from "./api";

export default function App() {
  const [shop, setShop] = useState("Tony's Pizza");
  const [examples, setExamples] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<AskResult | null>(null);

  useEffect(() => {
    getExamples()
      .then((d) => {
        setShop(d.shop);
        setExamples(d.examples);
      })
      .catch(() => {});
  }, []);

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

  return (
    <div className="wrap">
      <header>
        <h1>{shop}</h1>
        <p className="tag">
          Ask the data in plain English — it's translated to SQL and run live
          against the database.
        </p>
      </header>

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
          placeholder="e.g. which pizza sells the most?"
          autoFocus
        />
        <button type="submit" disabled={loading}>
          {loading ? "Thinking…" : "Ask"}
        </button>
      </form>

      {examples.length > 0 && (
        <div className="examples">
          {examples.map((ex) => (
            <button
              key={ex}
              className="chip"
              onClick={() => run(ex)}
              disabled={loading}
            >
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
  if (v === null || v === undefined) return "—";
  if (typeof v === "number")
    return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}
