# Tony's Pizza: a natural-language-to-SQL demo built to be attacked

**Live: [demo.rakimfrancis.com](https://demo.rakimfrancis.com)**

Anyone can place an order, then ask questions about the shop's data in plain English. Claude translates each question into a single SQL query, the query runs live against a real Postgres database, and the visitor sees the exact SQL alongside the results.

That last part is the point. This is a public LLM with database access, the exact thing engineering teams are nervous about deploying. This demo exists to show that the nervousness is solvable with layered, boring, verifiable controls.

## Security considerations

Every layer assumes the one above it can fail.

1. **The model proposes; the code disposes.** The LLM never executes anything. It returns a candidate query, and `is_safe_select()` (in `frontend/api/ask.py`) rejects anything that is not a single statement starting with `SELECT` or `WITH`: no statement chaining, no semicolons, and a keyword denylist (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `GRANT`, ...) as a belt-and-suspenders check. Safety is enforced in code, never delegated to the model.

2. **Even if that check missed something, the database refuses.** Queries run through `pizza_ro`, a Postgres role with read-only grants, and the connection itself sets `read_only = True` at the session level. A write that somehow survived the string check would be rejected twice more, by the session and by the role's permissions.

3. **The model is grounded, so it can't invent fields.** The prompt carries an explicit schema catalog and the rule "use only these tables and columns." Output is a structured JSON schema (`understood` / `interpretation` / `sql` / `clarifying_question`): an ambiguous or off-topic question produces a clarifying question back to the visitor, not a guessed query.

4. **Nothing is hidden.** Every answer shows the model's plain-English interpretation of the question and the exact SQL that ran. A visitor (or a hiring manager) can audit each query as they go.

5. **Free-form input is minimized and sanitized.** Ordering is menu-driven with quantity caps. The one free-text field, the customer name, is regex-filtered and capped at 30 characters (`clean_name()` in `frontend/api/order.py`), and orders execute in a single atomic transaction with row-level stock checks.

6. **Abuse is rate-limited and the blast radius is financial zero.** Per-visitor and global daily limits (Upstash Redis, checked per request), result sets capped at 200 rows, small `max_tokens` on every model call, and the API key draws from a small prepaid balance topped up manually. If every other layer failed, the demo would simply pause until tomorrow. The failure mode is an inconvenience, not an incident.

Deliberate trade-off: the rate limiter fails open. If Redis is unreachable, the demo stays up, because for a portfolio demo availability beats strictness, and the cost cap in layer 6 still bounds the downside. In a production system with real data, that dial flips.

## Architecture

- **Frontend:** React + TypeScript (Vite), deployed on Vercel
- **API:** Python serverless functions on Vercel (stdlib + `psycopg` only, self-contained per function)
- **Database:** Postgres on Neon, least-privilege roles per concern (read-only `pizza_ro` for queries; writes only through the order endpoint's own path)
- **LLM:** Claude (Haiku) with structured JSON output for the NL-to-SQL translation
- **Rate limiting:** Upstash Redis (per-visitor and global daily counters)
- **Data hygiene:** a daily cron (`frontend/api/restock.py`) resets inventory to baseline so the demo heals itself

## Why it's built this way

This demo is the public evolution of a natural-language report builder I built for a legal-services firm, where the LLM is bounded by a real field catalog so it cannot invent a field, and non-technical staff run their own reports. Same grounding discipline, same production instincts: least privilege, read-only paths, audit visibility, and a hard cap on the worst case.
