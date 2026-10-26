# ContaBu

**AI-native bookkeeping auditor. The code finds, the AI judges, the bookkeeper decides. EN/ES**

*Built with NVIDIA Nemotron 3 Nano, Nemotron 3 Super and Nemotron 3.5 Super VL on Nebius Token Factory, plus Tavily.*

**Live demo:** [demo.contabu.com](https://demo.contabu.com) · **Video:** `<VIDEO LINK — Jose adds it>` · **License:** [MPL 2.0](LICENSE)

**Big-firm AI, for the small bookkeeper.** The United States has
[71,408 bookkeeping and non-CPA accounting offices](https://data.bls.gov/cew/data/api/2024/a/industry/541219.csv), with 4 employees on average, and bookkeeping jobs are projected to
[fall 6% from 2025 to 2035](https://www.bls.gov/ooh/office-and-administrative-support/bookkeeping-accounting-and-auditing-clerks.htm), while the country has [36.4 million businesses, and nearly one in six is
Hispanic-owned](https://www.census.gov/newsroom/press-releases/2025/business-owner-characteristics.html), even more among the smallest: that is why ContaBu is in English and Spanish. It was born from the
daily work of a bookkeeper in that market: a team of agents on NVIDIA Nemotron, on Nebius Token Factory, plus
[Tavily web search](docs/tavily.md), reviews each client's books and explains what looks wrong. **The AI never
categorizes and never computes:** it judges, checks suspicious merchants on the web and proposes rules that the
bookkeeper approves, and [each model won its seat by measurement](docs/technological-implementation.md). **What we
got:** on a [blind exam](docs/technological-implementation.md), code alone finds 8 of 22 errors and with the agents,
**14–15 of 22**; on a [real book](docs/potential-impact.md), from **0 to 7 of 12** errors planted by a bookkeeper, and a
single note from that bookkeeper takes the proposed rules from **7 to 15 of 15** correct. Reviewing that whole book costs
**less than a cent**, without sending the model a single amount or a person's name.

## Try it

- **Guided tour** — *Take the guided tour* on the demo's sign-in page. No account, read-only, with the agents' review
  already done; it uses no AI credits.
- **Live demo** — *Continue with Google*, then the access code from our Devpost testing instructions. You get your own
  office with three synthetic client books, and you run the agents yourself.

The guided tour opens with a welcome screen and a quick guide through the real screens. Every screen is in English and
Spanish.

---

## Technological Implementation

We gave each agent one task, and each agent its own NVIDIA Nemotron model on Nebius Token Factory: **Nano** reviews the
findings and spots personal spending, **Super** checks merchants on the web and translates the bookkeeper's notes, and
**3.5 Super VL** drafts the rules. We did not pick them by eye: we tried all five Nemotron models in every seat, three
times each, with the rule to win written before we started. And we put them to the test with **blind exams**: errors
hidden in a book, with the answers kept where the agents cannot see them. Code alone found **8 of 22**; with the agents,
**14 to 15**. Then, on a real book, a bookkeeper hid **12 errors**: code alone found **none**, and with the agents **7**
came up. The surprise was in the seat that reviews each finding: the larger models were more cautious, not more
accurate, and on the real book Super, Ultra and 3.5 Super VL marked 6 or 7 true errors as "legitimate", letting them
through. That is why the reviewer is **Nano**, the smallest. A review of three books costs **$0.012**, and the model
never received an amount or a person's name. → [The full write-up](docs/technological-implementation.md)

## Tavily (the ② verifier)

To know whether an expense belongs to the business, sometimes you need to know what the merchant is. The ② verifier
looks it up on **Tavily** at review time, and **Nemotron Super** reads the results and picks the business type from a
list of 30: so the reviewer can say *"this is a gym booked as an office expense"*. It searches only what is suspicious
and **never people**, and web text cannot give orders to anyone: a single model reads it, and that model can only return
a type. On the real book, Super got **19–20 merchants right against 14** for our keyword table. And it spends little: a
new office uses **33–35 credits** on its first review and **0** on the second, because it remembers every merchant. →
[The full write-up](docs/tavily.md)

## Design

We wanted a bookkeeper to be able to trust the AI without giving up the review. So everything lives in one flow, like
their daily work: the client's accounts connect through **Plaid** and their transactions update with one button (or come
in from a statement), the rules categorize them, whatever no rule covers stays **Pending** in plain sight, the agents
review and propose, and the bookkeeper decides. The Bookkeeping sheet works like a spreadsheet, with the tools a
bookkeeper uses every day: a formula bar, right-click to create a rule from a row, color labels ("ask the client",
"done"), filters by date, account and Pending, undo, a pivot and export to Excel. Every AI claim comes with its evidence,
and every action shows its effect before it runs: select a row and a panel tells its whole story, and every proposed rule
shows, before you approve it, the rows it would change. The P&L never hides what is Pending. And because the books of
many clients live in one console, an automated test goes through the **86 server actions** and confirms that each user
sees and changes only what is theirs. → [The full write-up](docs/design.md)

## Potential Impact

The United States has **71,408 bookkeeping offices**, with 4 people on average, and the trade is shrinking **6%** toward
2035, while their clients are still there: **36.4 million businesses**, **5.8 million** of them Hispanic-owned. ContaBu
is built for that small bookkeeper, in English and Spanish, and it also serves the owner of a business that is just
starting out and wants to keep their own books, with AI that costs them pennies. We tested it where it matters, on a
real book (*an authorized client account*): a bookkeeper hid **12 errors** of the kind they see at work; code alone found
none, and the full system found **7**, all personal spending. With a single note from the bookkeeper, the rules the AI
proposes went from **7 to 15 right out of 15**. Reviewing that whole book costs **less than a cent**: about one second of
a bookkeeper's wage. → [The full write-up](docs/potential-impact.md)

## Quality of the Idea

The obvious move would be to ask the AI to categorize every transaction. We did the opposite: **the AI never categorizes
and never computes.** Each book is a **formula**, an ordered list of rules like an Excel IF, and the AI only drafts rows
for that formula; the bookkeeper approves them and an engine with no AI applies them. So every number on the P&L has an
explanation a bookkeeper can sign. What the bookkeeper knows about a client is written once in a note, the AI turns it
into a check, and that check runs on every review without calling the model again. And nothing the model returns is
executed: a validator checks every row before it reaches the book (**26 test cases, 0 failures**). →
[The full write-up](docs/quality-of-the-idea.md)

---

Each write-up ends with its **limits and how we will address them**. ContaBu is a bookkeeping tool, not tax advice:
every account carries its IRS Schedule C line to guide the tax preparer.

---

## Run it locally

Requires **Python 3.14**, **Node 24** and **PostgreSQL 18** (the versions it is developed on).

**1 · Database**

```bash
createuser contabu --pwprompt && createdb contabu -O contabu
```

**2 · Backend**

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/alembic upgrade head
```

Fill in `backend/.env`:

| Variable | Needed for |
|---|---|
| `DATABASE_URL` | everything — `postgresql+psycopg://contabu:PASSWORD@localhost:5432/contabu` |
| `NEBIUS_API_KEY` | the agents (Nemotron on Token Factory). `NEBIUS_MODEL` and `NEBIUS_BASE_URL` are optional |
| `TAVILY_API_KEY` | the ② verifier |
| `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV=sandbox`, `BANK_TOKEN_KEY` | bank feeds (optional) |
| `AGENT_MODEL_*`, `AGENT_REASONING_*` | change one agent's model (optional; defaults in `backend/agent_models.py`) |
| `AGENT_DAILY_CALLS`, `AGENT_DAILY_SEARCHES` | per-office daily caps (optional) |

Without `NEBIUS_API_KEY` or `TAVILY_API_KEY` the app still runs; only the agent steps report that they are unavailable.

**3 · Run** (development mode: `AUTH_DEV_MODE` accepts an `X-Dev-Uid` header instead of a Firebase token — never turn it
on in a public deployment; with `DEMO_MODE`, the backend refuses to start)

```bash
cd backend && AUTH_DEV_MODE=true .venv/bin/uvicorn main:app --reload --port 8000
```

```bash
cd frontend && npm install && echo "VITE_DEV_UID=local-dev" > .env.local && npm run dev
```

Open `http://localhost:5173`. To see a live Nemotron call from the UI, upload `backend/sample_bank.csv`: its headers are
not ones the code knows, so the column mapper asks the model — and sends it only the headers and the column profiles.

**4 · Checks**

```bash
cd backend && .venv/bin/python check_permissions.py     # every route; must report 0 leaks
cd backend && .venv/bin/python check_formula.py         # the formula validator; must report 0 failures
cd backend && .venv/bin/python check_web_classifier.py  # the ② classifier; must report 0 failures
cd frontend && npx tsc -b                               # must be clean
```

## License

[Mozilla Public License 2.0](LICENSE): you can use and modify ContaBu; changes to its files are shared under the same
license.

## Feedback on Token Factory and Nemotron

Fourteen findings from building ContaBu, each measured and reproducible with the scripts in `backend/`, are in our
Devpost submission.

## Stack

- **Frontend:** React 19 + Vite + TypeScript + Tailwind. The sheet is **RevoGrid**.
- **Backend:** Python + FastAPI. **Database:** PostgreSQL + SQLModel + Alembic.
- **AI:** NVIDIA Nemotron 3 Nano, 3 Super and 3.5 Super VL on **Nebius Token Factory** (OpenAI-compatible API, via
  `httpx`).
- **Web search:** **Tavily**. **Banks:** Plaid. **Sign-in:** Firebase Auth.

## Repository map

```text
backend/
  main.py              the FastAPI app and its routes
  models.py            the data model (SQLModel)
  ingestion.py         statement parsing + the rule → transfer → Pending engine
  rules.py             rule matching: priority, date windows, amount ranges; transfers
  formula.py           the book's formula: the row schema, the validator, the interpreter
  reports.py           the P&L and the pivot — every figure is a SQL aggregation
  audit.py             the deterministic auditor
  book_review.py       Review the book: the findings, the deterministic checks
  nemotron.py          the Token Factory client: strict JSON, reasoning switch, retries, cost per call
  agent_models.py      which Nemotron model (and reasoning) each agent uses
  agents.py            ④ rule drafter and ① reviewer; pseudonyms and the privacy scan
  note_checks.py       ⑥ a bookkeeper's note → an audit check
  personal_use.py      ⑤ personal for THIS business?
  merchant_verifier.py ② merchant verifier: Tavily search + Nemotron Super classifier, keyword table as fallback
  agent_calls.py       metering for every call and the daily caps
  measure.py           one review against the answer keys
  team_matrix.py       the per-seat model matrix
  check_*.py           the permissions, formula validator and web classifier tests
  demo.py · demo_data.py   the public demo: the two doors, the guided tour, the seeded books
docs/                  the five write-ups the README links to
frontend/src/
  components/          the sheet, the rule book, Audit, the side panel, the P&L, the demo guide
  locales/             en.json · es.json — every visible string in English and Spanish
```
