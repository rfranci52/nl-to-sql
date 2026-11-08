# Tony's Pizza: a natural-language-to-SQL demo built to be attacked

**Live: [demo.rakimfrancis.com](https://demo.rakimfrancis.com)**

Anyone can place an order, then ask questions about the shop's data in plain English. Claude translates each question into a single SQL query, the query runs live against a real Postgres database, and the visitor sees the exact SQL alongside the results.

That last part is the point. This is a public LLM with database access, the exact thing engineering teams are nervous about deploying. This demo exists to show that the nervousness is solvable with layered, boring, verifiable controls.

## Security considerations

Every layer assumes the one above it can fail.

1. **The model proposes; the code disposes.** The LLM never executes anything. It returns a candidate query, and `is_safe_select()` (in `frontend/api/ask.py`) rejects anything that is not a single statement starting with `SELECT` or `WITH`: no statement chaining, no semicolons, and a keyword denylist (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `GRANT`, ...) as a belt-and-suspenders check. Safety is enforced in code, never delegated to the model.

2. **Even if that check missed something, the database refuses.** Queries run through `pizza_ro`, a Postgres role with read-only grants, inside a read-only transaction that is always rolled back, with a 5-second statement timeout, through a server-side cursor that hands back at most 200 rows. A write that somehow survived the string check would be rejected twice more, by the transaction and by the role's permissions, and nothing a query changes (session settings included) outlives it. The screen also refuses the few functions a rollback can't undo, such as advisory locks and terminating other sessions.

3. **The model is grounded.** The prompt carries an explicit schema catalog and the rule "use only these tables and columns"; if the model invents a column anyway, Postgres rejects the query and nothing runs. Output is a structured JSON schema (`understood` / `interpretation` / `sql` / `clarifying_question`): an ambiguous or off-topic question produces a clarifying question back to the visitor, not a guessed query.

4. **Nothing is hidden.** Every answer shows the model's plain-English interpretation of the question and the exact SQL that ran. A visitor can audit each query as they go.

5. **Free-form input is minimized and sanitized.** Ordering is menu-driven with quantity caps. Questions are capped at 500 characters. The one free-text field, the customer name, is labeled as public, capped at 30 characters, stripped of invisible formatting characters, and checked against a blocklist of slurs stored as SHA-256 hashes, so this repo doesn't spell them out (`clean_name()` in `frontend/api/order.py`). Orders execute in a single atomic transaction with row-level stock checks.

6. **Abuse is rate-limited and the blast radius is financial zero.** Per-visitor and global daily limits (Upstash Redis, checked per request, per-visitor first, so one visitor can't use up everyone's allowance), result sets capped at 200 rows, a capped `max_tokens` on every model call, and the API key draws from a fixed monthly balance. If every other layer failed, the demo would simply stop answering until the limits reset or the balance renews. The failure mode is an inconvenience, not an incident.

Deliberate trade-off: the rate limiter fails open. If Redis is unreachable, the demo stays up, because for a portfolio demo availability beats strictness, and the cost cap in layer 6 still bounds the downside. In a production system with real data, that dial flips.

## Measured accuracy

[`eval/run_eval.py`](eval/run_eval.py) sends 23 questions through the same `answer()` function the live endpoint calls and grades each answer against a reference query run on the same data at the same moment, so it keeps working as the data grows. It covers revenue and ranking questions, time-of-day and date-range questions, ambiguous and off-topic questions, and attack prompts.

| Date | Model | Passed | Median answer time | Cost per question |
|---|---|---|---|---|
| 2026-11-05 | Claude Haiku 4.5 (previous model; earlier 20-question set and prompt) | 17/20 | 2.3 s | $0.002 |
| 2026-11-05 | Claude Opus 5.5, low effort | 23/23 | 2.7 s | $0.005 |

Haiku's three misses were two queries Postgres rejected (a `GROUP BY` after `LIMIT`, and a column that doesn't exist) and one unnecessary clarifying question. To rerun: `uv run python eval/run_eval.py` (needs `ANTHROPIC_API_KEY` and `PIZZA_RO_URL`; read-only, about $0.11 a run).

## Architecture

- **Frontend:** React + TypeScript (Vite), deployed on Vercel
- **API:** Python serverless functions on Vercel (stdlib + `psycopg` only, self-contained per function)
- **Database:** Postgres on Neon, least-privilege roles per concern (read-only `pizza_ro` for queries; writes only through the order endpoint's own path)
- **LLM:** Claude Opus 5.5 at low effort, with structured JSON output and prompt caching for the NL-to-SQL translation
- **Rate limiting:** Upstash Redis (per-visitor and global daily counters)
- **Data hygiene:** a daily cron (`frontend/api/restock.py`) resets inventory to baseline and adds the previous day's synthetic orders, so the sales history stays current; an order that leaves an item at or under 20 percent of its stock restocks it on the spot. Times are New York time. Details in [`db/README.md`](db/README.md).

## Why it's built this way

This demo is the public evolution of a natural-language report builder I built for a firm in a heavily regulated industry, where the LLM is bounded by a real field catalog so it cannot invent a field, and non-technical staff run their own reports. Same grounding discipline, same production instincts: least privilege, read-only paths, audit visibility, and a hard cap on the worst case.
