# Technological Implementation — how we use Nemotron and how we measured it

## Summary

We gave each agent one task, and each agent its own NVIDIA Nemotron model on Nebius Token Factory: **Nano** reviews the
findings and spots personal spending, **Super** checks merchants on the web and translates the bookkeeper's notes, and
**3.5 Super VL** drafts the rules. We did not pick them by eye: we tried all five Nemotron models in every seat, three
times each, with the rule to win written before we started. And we put them to the test with **blind exams**: errors
hidden in a book, with the answers kept where the agents cannot see them. Code alone found **8 of 22**; with the agents,
**14 to 15**. Then, on a real book, a bookkeeper hid **12 errors**: code alone found **none**, and with the agents **7**
came up. The surprise was in the seat that reviews each finding: the larger models were more cautious, not more
accurate, and on the real book Super, Ultra and 3.5 Super VL marked 6 or 7 true errors as "legitimate", letting them
through. That is why the reviewer is **Nano**, the smallest. A review of three books costs **$0.012**, and the model
never received an amount or a person's name.

---

## 1. The agents

ContaBu reviews a client's books with an **auditor** written in code, with no AI, and a **team of agents**. The auditor
finds what fixed rules can find: one merchant in two categories, money that came in booked as an expense, a row that no
longer follows its rule. The agents do what a fixed rule cannot: judge, investigate and propose. Each one has a number,
which is its seat on the team:

- **① Reviewer.** Reads each of the auditor's findings and says whether it is a problem, legitimate or unsure, and why,
  in English and in Spanish.
- **② Verifier.** Searches the web (with Tavily) for what a suspicious merchant is, and Nemotron Super reads the results
  and picks its business type from a closed list. The other agents receive only that type and a confidence. If the
  model fails, a keyword table in code classifies instead.
- **③ Reconciler (under construction).** It will store bank statements and check images, reconcile every transaction in
  the book against the PDF statement, and put the payee's name on every check, automatically.
- **④ Rule drafter.** For whatever no rule covers (Pending), it proposes new rules. It never categorizes: the bookkeeper
  approves each rule and the engine applies it.
- **⑤ Personal?** Looks at the merchants booked as business expenses and flags the ones that look like the owner's
  personal spending.
- **⑥ Note translator.** Turns what the bookkeeper writes about a client ("purchases over $200 at this store are
  materials") into a check that the auditor applies on every review, without calling the model again.

No agent computes or changes a number: totals come from the database, and the category is set by a rule or by the
bookkeeper.

## 2. How we chose the model for each seat

For each seat we tried the five Nemotron models available (Nano, 3.5 Lightning, Super, Ultra and 3.5 Super VL), each with
reasoning on and off: **ten variants per seat**. We changed **one seat at a time**, with the others fixed on the best
team so far, and ran each variant **three times**, because the same model does not always answer the same way. The rule
to switch a model was written before running: a candidate **wins** only if its **worst** run beats the current model's
**best**; if the results cross, it is a tie and nothing changes. Runs affected by provider errors were discarded and
repeated.

## 3. What a blind exam is, and what we asked

In a **blind exam** we plant bookkeeping errors in a copy of a book and keep the answers in a key the agents never see.
Then we run the full review, the way a user runs it, and code counts how many errors the team found. An error counts as
found if an alert points at its row and the ① reviewer does not dismiss it as legitimate. That answers three questions:

1. **Does the team find errors that code alone does not see?**
2. **Does the AI respect the bookkeeper?** It never categorizes or computes; is what it proposes right?
3. **Was each Nemotron model chosen by measurement?**

## 4. The test sets

We name them after the book they run on: **D** are the **demo** books and **R** is the **real** book.

**D1 and D2 — the demo books.** Three made-up businesses (a heating and cooling contractor, a cleaning company and a
taqueria), with three months of transactions each, the same ones any demo visitor sees. **D1** has 32 planted errors and
91 Pending transactions with their answers; we used it to **tune** the agents and choose the models, so it **is not
blind**. **D2** has another 22 errors, written separately and **never used to tune anything**: it is the demo's blind
exam.

**R1, R2 and R3 — a real book.** The bank account of a small business, used with its owner's permission (*an authorized
client account*), with its **197 transactions of 2026**, categorized by a bookkeeper.
- **R1** has 18 errors planted in 20 rows chosen at random by a program: wrong categories, one merchant in two accounts,
  reversed signs, drifted rules, a personal expense, a card payment booked as an expense, a sale booked as an owner
  contribution.
- **R2** was designed by a practicing bookkeeper, with the errors they see at work: **12 blind** ones, almost all hidden
  personal spending (athletic wear, beauty, underwear, an amusement park) plus an ATM withdrawal and a gas station
  booked wrong. Since the book had no purchases of those kinds, they were added to the copy. It also has **4 errors
  that are not blind**, purchases on the wrong side of an amount threshold, counted separately.
- **R3** plants nothing: it compares the rules the ④ proposes with what the bookkeeper actually decided about the Pending
  transactions, without and with one of their notes.

## 5. Results with the current team

**The model that won each seat, and why that one:**

| Seat | Model | Why this one |
|---|---|---|
| ① Reviewer | **Nemotron Nano**, no reasoning | No candidate beats it on the blind exams (3.5 Lightning ties). On R1, Super, Ultra and Super VL dismiss 6 or 7 true errors as "legitimate" |
| ② Verifier | **Tavily + Nemotron Super**, no reasoning | On the same Tavily results, Super classifies the business type better than the keyword table on the real book (19–20 correct against 14, with 2 errors each) and ties on the demo; it was never worse in any run (see *Tavily*). Measured live with the team: on the same fresh Tavily results, R2 7 · 7 · 5 against 5 · 5 · 5 for the table, and a tie on D2 (see below) |
| ④ Rule drafter | **Nemotron 3.5 Super VL**, no reasoning | The same quality as Super with reasoning, 39% cheaper and 3 seconds per call instead of 15 to 40 |
| ⑤ Personal? | **Nemotron Nano**, no reasoning | The most stable: 7 of 12 on R2 in all 12 runs, with both prompts. Ultra found 10 of 12 on R2, but did not win on D2 or R1, and the best large model changed just by rewriting the prompt (see below) |
| ⑥ Note translator | **Nemotron Super**, no reasoning | Ties Ultra on quality and costs 15% less |

**The errors the team found:**

| Set | Errors | Code alone | With the agents, per run |
|---|---|---|---|
| D1 (not blind) | 32 | 24 | 31 · 30 · 31 |
| D2 (blind) | 22 | 8 | 14 · 15 · 14 |
| R1 (blind) | 18 | 9 | 11 · 10 · 11 |
| R2 (blind) | 12 | 0 | 7 · 7 · 7 |
| R2, the 4 that are not blind | 4 | 4 | 4 · 4 · 4 |
| R3, the ④ without the note → with the note (118 Pending transactions of 2026) | — | — | decides 15, right on 7 → decides 15, right on **15** (×3 each) |

*Measured on the code we ship, with the rewritten prompts: sha1 of the backend files that decide the result (set
`ec1143d3…`). This table was measured with the saved web evidence; the ② live, in the next table.*

**The ② live.** Live, the ② classifies freshly fetched Tavily results with Super, not the saved evidence. So we re-ran
D2 and R2 three times with the ② live, and added a **control**: the keyword table on those **same fresh results**. The
control separates the two changes: the new evidence, and Super.

| Set | With the saved evidence | With the ② live (Super on fresh Tavily results) | Control: the keyword table on those same results |
|---|---|---|---|
| D2 (blind, 22) | 14 · 15 · 14 | 14 · 13 · 14 | 15 · 13 · 16 |
| R2 blind (12) | 7 · 7 · 7 | 7 · 7 · 5 | 5 · 5 · 5 |
| R2, the 4 that are not blind | 4 · 4 · 4 | 4 · 4 · 4 | 4 · 4 · 4 |

- By the rule written before running (no run below the worst run with the saved evidence), the live team **drops**: one
  D2 run gives 13 and one R2 run gives 5.
- **The control says where it comes from:** on the same fresh results, the table also gives 13 on D2 and 5 · 5 · 5 on R2.
  The drop comes from the new evidence, not from Super: **on the same input, Super scores more than the table on R2 in
  two of three runs, and the same in the third** (7 · 7 · 5 against 5 · 5 · 5), and on D2 the runs cross: a tie.
- Privacy 0 · 0 · 0 live as well, in the agents and in what leaves the book for the classifier (the query and the region).

- **For reviewing each finding, bigger is not better.** As reviewers (①), the larger models were more cautious, not more
  accurate: on R1, Super, Ultra and 3.5 Super VL marked 6 or 7 true errors as "legitimate", letting them through. That is
  why **Nano**, the smallest, reviews. The ⑤ is also Nano, but for stability, not because the large models fail: Ultra
  found 10 of 12 on R2 against 7, and did not win on D2 or R1. For **drafting** rules a larger model does pay: 3.5 Super
  VL, without reasoning, matches Super with reasoning, 39% cheaper and 3 seconds per call instead of 15 to 40.
- On R2, code alone finds **none** of the 12 errors; the agents find 7, all personal spending.
- What nobody finds are error types that have no detector yet: a wrong category on a single row, an ATM withdrawal booked
  as an expense, a loan or owner money booked as a sale.
- **Privacy, measured on what was actually sent to the model:** 0 full descriptions, 0 amounts and 0 person names, in
  every run.
- **Cost of a full review:** $0.012 for the three demo books, $0.007–0.008 for the real book (2026).
- **The ⑤, seat by seat:** on R2 (the hidden personal spending exam), Ultra found 10 of 12 against Nano's 7, but did not
  win on D2 or R1, and the best large model changed just by rewriting the prompt: with the earlier prompts Super VL with
  reasoning won, and with the new ones it found 0. Nano gave 7 in all 12 runs, with both prompts: we kept the most stable.

## 6. Limits, and how we will address them

| Limit | How we will address it |
|---|---|
| The D1 and D2 errors were written by an AI, and R1's were picked at random by a program; only R2's were planted by a bookkeeper | Exams written by practicing bookkeepers |
| R2's personal purchases were added to the copy: the real book had none of those kinds | Measure on books that contain those expenses for real |
| A single real book | More authorized clients, each with its own exam |
| Each agent uses one prompt for every model; we did not write a prompt tailored to each model | Repeat the seat comparison with a prompt written for each candidate model |
| Errors with no detector (a wrong category on a single row, an ATM withdrawal as an expense, a loan or owner money as a sale) | Build those detectors and measure them on the same exams |
| A correct store type does not say what was bought: with the ② live, D1 (not blind) drops to 27 · 28 · 31 (31 · 30 · 31 with the saved evidence), because Super labels a department store or a supermarket with high confidence and the ⑤ downgraded 2 planted personal purchases | Stop the ⑤ from taking a store type as proof that the purchase is for the business |
