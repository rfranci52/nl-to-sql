# Tony's Pizza: a natural-language-to-SQL demo built to be attacked

**Live: [demo.rakimfrancis.com](https://demo.rakimfrancis.com)**

Anyone can place an order, then ask questions about the shop's data in plain English. Claude translates each question into a single SQL query, the query runs live against a real Postgres database, and the visitor sees the exact SQL alongside the results.

That last part is the point. This is a public LLM with database access, the exact thing engineering teams are nervous about deploying. This demo exists to show that the nervousness is solvable with layered, boring, verifiable controls.

## Security considerations

Every layer assumes the one above it can fail.

1. **The model proposes; the code disposes.** The LLM never executes anything. It returns a candidate query, and `is_safe_select()` (in `frontend/api/ask.py`) rejects anything that is not a single statement starting with `SELECT` or `WITH`: no statement chaining, no semicolons, no comments, and a denylist covering write keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `GRANT`, ...), the system catalogs (`pg_*`, `information_schema`), `U&` escaped names, and the built-in functions that run SQL passed in as text (`query_to_xml` and its family, `ts_stat`) or change server state (`set_config`, advisory locks). It is a text screen rather than a SQL parser, so it is one layer of several. Safety is enforced in code, never delegated to the model.

2. **Even if that check missed something, the database refuses.** Queries run through `pizza_ro`, a Postgres role with read-only grants on the shop's tables, inside a read-only transaction that is always rolled back, with a 5-second statement timeout, through a server-side cursor that hands back at most 200 rows. A write to a shop table that somehow survived the string check would be rejected twice more, by the transaction and by the role's permissions, and rows in the other schemas on this database are refused by those permissions too. The limits are worth stating: the rollback undoes transactional changes and `SET LOCAL` settings, but a session advisory lock or a terminated connection outlives it, and `pizza_ro` can read the Postgres system catalogs like any login. Only the screen in layer 1 refuses those.

3. **The model is grounded.** The prompt carries an explicit schema catalog and the rule "use only these tables and columns"; if the model invents a column anyway, Postgres rejects the query and nothing runs. Output is a structured JSON schema (`understood` / `interpretation` / `sql` / `clarifying_question`): an ambiguous or off-topic question produces a clarifying question back to the visitor, not a guessed query.

4. **Nothing is hidden.** Every answer shows the model's plain-English interpretation of the question and the exact SQL that ran. A visitor can audit each query as they go.

5. **Free-form input is minimized and sanitized.** Ordering is menu-driven with quantity caps. Questions are capped at 500 characters. The one free-text field, the customer name, is labeled as public, capped at 30 characters, stripped of invisible formatting characters, and limited to plain letters, digits, spaces and `. ' -`, so lookalike letters from other alphabets can't pass (accents fold to the base letter). A name that doesn't fit, that looks like a web address or phone number, or that contains a blocked word falls back to `Guest`. Every run of 3 or more letters, with digit swaps and repeated letters undone, is checked against a blocklist of slurs stored as SHA-256 hashes, so this repo doesn't spell them out (`clean_name()` in `frontend/api/order.py`). Orders execute in a single atomic transaction with row-level stock checks and are capped at $200. One name can carry at most $250 of spend; past that its orders still go through, saved as `Guest`, so nobody can type their way onto the top-customer list.

6. **Abuse is rate-limited and the cost is bounded.** `/api/ask` and `/api/order` accept only JSON from this demo's own pages, which stops other websites driving them through their visitors' browsers (a script can forge that, which is what the limits are for). Per-visitor and global daily limits (Upstash Redis, checked per request, per-visitor first, so one visitor can't use up everyone's allowance), result sets capped at 200 rows, a capped `max_tokens` on every model call, and the API key draws from a fixed monthly balance. `/api/menu` is cached at the edge and refuses query strings, so polling it can't keep the database awake. There is no separate dollar counter, so the worst case is the balance on that one key. If every other layer failed, the demo would simply stop answering until the limits reset or the balance renews. The failure mode is an inconvenience, not an incident.

Deliberate trade-off: the rate limiter fails closed for questions and open for orders. If Redis is unreachable, questions pause, because each one costs money and an attacker who could knock the limiter over would otherwise remove the cap. Orders carry on, because they cost nothing and the order and per-name caps still apply.

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
