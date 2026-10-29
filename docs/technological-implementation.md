[← Back to README](../README.md)

# Technological Implementation — how we use Nemotron and how we measured it

## Summary

We gave each agent one task, and each agent its own NVIDIA Nemotron model on Nebius Token Factory: **Nano** reviews the
findings and spots personal spending, **Super** checks merchants on the web and translates the bookkeeper's notes, and
**3.5 Super VL** drafts the rules. We did not pick them by eye: in every seat we tried the five Nemotron models Token
Factory offers, three times each, with the rule to win written before we started. Token Factory made that fast: all five
models sit behind the same OpenAI-compatible API, so changing an agent's model is changing one variable
(`AGENT_MODEL_*`), with no code change. And we put them to the test with **blind exams**: errors hidden in a book, with
the answers kept where the agents cannot see them. Our fixed checks in code, **without AI**, found **8 of the 22**; with
the agents, **14–15 of the 22**. Then, on a real book with 197 current transactions from 2026, a bookkeeper hid **12
errors**: code without AI found **none**, and with the agents **7** came up. The surprise was in the seat that reviews
each finding: **the smallest model was the best judge.** The larger ones were more cautious, not more accurate: on
another exam on the same real book, Super, Ultra and 3.5 Super VL marked 6 or 7 true errors as "legitimate", letting them
through. That is why the reviewer is **Nano**. A review of three books costs **$0.012**, and the model never received an
amount or a person's name.

![The AI team panel in Audit, from the guided tour. 1. Audit: the auditor (code, no AI); Reads your notes, Nemotron 3 Super, 1 call, 1.4 s, $0.00051; Spots personal spending, Nemotron 3 Nano, 28 merchants, 2 calls, 23.8 s, $0.00054; Reviews each finding, Nemotron 3 Nano, 13 findings, 2 calls, 22.6 s, $0.00073. 2. Check on the web: Tavily + Nemotron 3 Super, 1 web search, 3.3 s, 1 credit. 3. Draft rules: Nemotron 3.5 Super VL, 7 proposed, 5 passed the validator, 2 calls, 3.0 s, $0.00314. Last run: 9 calls, $0.00530, 1 credit.](img/team.png)

*The AI team panel in Audit: each step with the model that really did it, its time and its cost, taken from the log of the agents' calls.*

---

## 1. The agents

ContaBu reviews a client's books with an **auditor** written in code, with no AI, and a **team of agents**. The auditor
finds what fixed rules can find: one merchant in two categories, money that came in booked as an expense, a row that no
longer follows its rule. The agents do what a fixed rule cannot: judge, investigate and propose. Each one has a seat on
the team:

- **The reviewer.** Reads each of the auditor's findings and says whether it is a problem, legitimate or unsure, and
  why, in English and in Spanish.
- **The merchant verifier.** Searches the web (with Tavily) for what a suspicious merchant is, and Nemotron Super reads
  the results and picks its business type from a closed list. The other agents receive only that type and a confidence.
  If the model fails, a keyword table in code classifies instead.
- **The statement reconciler (under construction).** It will store bank statements and check images, reconcile every
  transaction in the book against the PDF statement, and put the payee's name on every check, automatically.
- **The rule drafter.** For whatever no rule covers (Pending), it proposes new rules. It never categorizes: the
  bookkeeper approves each rule and the engine applies it.
- **The personal-spending agent.** Looks at the merchants booked as business expenses and flags the ones that look like
  the owner's personal spending.
- **The note translator.** Turns what the bookkeeper writes about a client ("purchases over $200 at this store are
  materials") into a check that the auditor applies on every review, without calling the model again.

No agent computes or changes a number: totals come from the database, and the category is set by a rule or by the
bookkeeper.

## 2. How the agents talk to Nemotron

All the agents use one client (`backend/nemotron.py`) against Token Factory's OpenAI-compatible API, with `httpx` and no
SDK. Every call asks for **strict JSON** with its response schema (`json_schema` with `strict`), and **code still
validates** every answer before using it: the schema guarantees the shape, not that the content is right. **Reasoning is
off** (`chat_template_kwargs: {"enable_thinking": false}`): we measured that a minimal call spent 230–247 tokens
reasoning against 18 without, and it is turned on only where the measurement justified it. Transient service errors
(429 and 5xx) are retried, and running out of tokens is its own error, not an "invalid JSON". **Every call is logged**
with its model, tokens, cost and latency, never with its content, and each office has a daily cap on calls and searches.
Before sending, people's names are replaced by pseudonyms, and a test checks what actually went out to the model, so
privacy is measured, not assumed. What we learned about the platform along the way is in our
[feedback on Token Factory and Nemotron](nebius-feedback.md).

## 3. How we chose the model for each seat

For each seat we tried every Nemotron language model on Token Factory's public catalog: five (Nano, 3.5 Lightning,
Super, Ultra and 3.5 Super VL), each with reasoning on and off: **ten variants per seat**. We changed **one seat at a
time**, with the others fixed on the best team so far, and ran each variant **three times**, because the same model does
not always answer the same way. The rule to switch a model was written before running: a candidate **wins** only if its
**worst** run beats the current model's **best**; if the results cross, it is a tie and nothing changes. Runs affected by
provider errors were discarded and repeated.

## 4. What a blind exam is, and what we asked

In a **blind exam** we plant bookkeeping errors in a copy of a book and keep the answers in a key the agents never see.
Then we run the full review, the way a user runs it, and code counts how many errors the team found. An error counts as
found if an alert points at its row and the reviewer does not dismiss it as legitimate. That answers three questions:

1. **Does the team find errors that code without AI does not see?**
2. **Does the AI respect the bookkeeper?** It never categorizes or computes; is what it proposes right?
3. **Was each Nemotron model chosen by measurement?**

## 5. The test sets

We name them after the book they run on: **D** are the **demo** books and **R** is the **real** book.

**D1 and D2 — the demo books.** Three made-up businesses (a heating and cooling contractor, a cleaning company and a
taqueria), with three months of transactions each, the same ones any demo visitor sees. **D1** has 32 planted errors and
91 Pending transactions with their answers; we used it to **tune** the agents and choose the models, so it **is not
blind**. **D2** has another 22 errors, written separately and **never used to tune anything**: it is the demo's blind
exam.

**R1, R2 and R3 — a real book.** The bank account of a small business, used with its owner's permission (*an authorized
client account*), with its **197 transactions from 2026**, categorized by a bookkeeper.
- **R1** has 18 errors planted in 20 rows chosen at random by a program: wrong categories, one merchant in two accounts,
  reversed signs, drifted rules, a personal expense, a card payment booked as an expense, a sale booked as an owner
  contribution.
- **R2** was designed by a practicing bookkeeper, with the errors they see at work: **12 blind** ones, almost all hidden
  personal spending (athletic wear, beauty, underwear, an amusement park) plus an ATM withdrawal and a gas station
  booked wrong. Since the book had no purchases of those kinds, they were added to the copy. It also has **4 errors
  that are not blind**, purchases on the wrong side of an amount threshold, counted separately.
- **R3** plants nothing: of the **118 Pending transactions** of 2026, it compares the rules the rule drafter proposes
  with what the bookkeeper actually decided, without and with one of their notes.

## 6. Results with the current team

**The model that won each seat, and why that one:**

| Seat | Model | Why this one |
|---|---|---|
| Reviewer | **Nemotron Nano**, no reasoning | No candidate beats it on the blind exams (3.5 Lightning ties). On R1, Super, Ultra and Super VL dismiss 6 or 7 true errors as "legitimate" |
| Merchant verifier | **Tavily + Nemotron Super**, no reasoning | On the same Tavily results, Super classifies the business type better than the keyword table on the real book (19–20 of 23 against 14, with 2 errors each) and ties on the demo; it was never worse in any run ([Tavily](tavily.md#4-the-classification-the-keyword-table-against-the-nemotron-models)) |
| Rule drafter | **Nemotron 3.5 Super VL**, no reasoning | The same quality as Super with reasoning, 39% cheaper and 3 seconds per call instead of 15 to 40 |
| Personal-spending agent | **Nemotron Nano**, no reasoning | The most stable: 7 of 12 on R2 in all 12 runs, with both prompts. Ultra found 10 of 12 on R2, but did not win on D2 or R1, and the best large model changed just by rewriting the prompt |
| Note translator | **Nemotron Super**, no reasoning | Ties Ultra on quality and costs 15% less |

**The errors the team found:**

| Set | Errors | Code without AI | With the agents, per run |
|---|---|---|---|
| D1 (not blind) | 32 | 24 | 31 · 30 · 31 |
| D2 (blind) | 22 | 8 | 14 · 15 · 14 |
| R1 (blind) | 18 | 9 | 11 · 10 · 11 |
| R2 (blind) | 12 | 0 | 7 · 7 · 7 |
| R2, the 4 that are not blind | 4 | 4 | 4 · 4 · 4 |
| R3, the rule drafter without the note → with the note (of 118 Pending) | — | — | decides 15, right on 7 → decides 15, right on **15** (×3 each) |

*Measured on the code we ship, with the rewritten prompts: sha1 of the backend files that decide the result (set
`ec1143d3…`), with the saved web evidence.*

**What the results say:**

- **To review each finding, the smallest model was the best judge.** As reviewers, the larger models were more cautious,
  not more accurate: on R1, Super, Ultra and 3.5 Super VL let 6 or 7 true errors through. That is why **Nano** reviews.
  For **drafting** rules, on the other hand, a larger model does pay: 3.5 Super VL without reasoning matches Super with
  reasoning.
- **The personal-spending agent is also Nano, but for stability**, not because the large models fail: Ultra found 10 of
  12 on R2, but did not win on D2 or R1, and with the earlier prompts Super VL with reasoning won, and with the new ones
  it found 0. Nano gave 7 in all 12 runs, with both prompts: we kept the most stable.
- **On R2, code without AI finds none** of the 12 errors; the agents find 7, all personal spending. What nobody finds are
  error types that have no detector yet: a wrong category on a single row, an ATM withdrawal booked as an expense, a loan
  or owner money booked as a sale.
- **Privacy, measured on what was actually sent to the model:** 0 full descriptions, 0 amounts and 0 person names, in
  every run.
- **Cost of a full review:** $0.012 for the three demo books, $0.007–0.008 for the real book.

**The merchant verifier live.** In the table above the verifier uses the saved web evidence. Live, Super classifies
freshly fetched Tavily results, so we re-ran D2 and R2 three times live, with a **control**: the keyword table on those
**same fresh results**.

| Set | Saved evidence | Live (Super) | Control (table, same results) |
|---|---|---|---|
| D2 (blind, 22) | 14 · 15 · 14 | 14 · 13 · 14 | 15 · 13 · 16 |
| R2 blind (12) | 7 · 7 · 7 | 7 · 7 · 5 | 5 · 5 · 5 |
| R2, the 4 that are not blind | 4 · 4 · 4 | 4 · 4 · 4 | 4 · 4 · 4 |

Live, the team **drops** a little (one D2 run gives 13 and one R2 run gives 5), and the control shows the cause is the new
evidence, not Super: on the same results, the table also gives 13 on D2 and 5 · 5 · 5 on R2, while Super scores more than
the table on R2 in two of three runs and the same in the third. Privacy was 0 · 0 · 0 live as well. The Tavily details,
including the test with the verifier off, are in its [write-up](tavily.md#7-with-and-without-tavily).

## 7. Limits, and how we will address them

| Limit | How we will address it |
|---|---|
| The D1 and D2 errors were written by an AI, and R1's were picked at random by a program; only R2's were planted by a bookkeeper | Exams written by practicing bookkeepers |
| R2's personal purchases were added to the copy: the real book had none of those kinds | Measure on books that contain those expenses for real |
| A single real book | More authorized clients, each with its own exam |
| Each agent uses one prompt for every model; we did not write a prompt tailored to each model | Repeat the seat comparison with a prompt written for each candidate model |
| Errors with no detector (a wrong category on a single row, an ATM withdrawal as an expense, a loan or owner money as a sale) | Build those detectors and measure them on the same exams |
| A correct store type does not say what was bought: with the merchant verifier live, D1 (not blind) drops to 27 · 28 · 31 (31 · 30 · 31 with the saved evidence), because Super labels a department store or a supermarket with high confidence and the personal-spending agent downgraded 2 planted personal purchases | At a general store, the alert should not go away but move to "ask the client"; the two fixes we measured did not pass our rule ([Tavily §7](tavily.md#7-with-and-without-tavily)) |
