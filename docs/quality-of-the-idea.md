# Quality of the Idea — inverting the obvious use of AI in accounting

## Summary

The obvious move would be to ask the AI to categorize every transaction. We did the opposite: **the AI never categorizes
and never computes.** Each book is a **formula**, an ordered list of rules like an Excel IF, and the AI only drafts rows
for that formula; the bookkeeper approves them and an engine with no AI applies them. So every number on the P&L has an
explanation a bookkeeper can sign. What the bookkeeper knows about a client is written once in a note, the AI turns it
into a check, and that check runs on every review without calling the model again. And nothing the model returns is
executed: a validator checks every row before it reaches the book (**26 test cases, 0 failures**).

> **ContaBu's agents, by number.** ① **Reviewer:** judges each of the auditor's findings (a problem, or legitimate?) ·
> ② **Verifier:** looks the merchant up on the web with Tavily and says what kind of business it is · ③ **Reconciler:**
> under construction · ④ **Rule drafter:** proposes rules for what is Pending · ⑤ **Personal?:** flags expenses that look
> like the owner's personal spending, for *this* business · ⑥ **Note translator:** turns a bookkeeper's note into a check
> the auditor runs on every review. Each one's model, and why, in *Technological Implementation*.

---

## 1. Thesis

Asking a model to assign categories produces books that cannot be audited: every decision is new, it cannot be tested
before it is applied, and its only justification is the model's output. We hold that the right place for a language
model in bookkeeping is **between the professional's judgment and a deterministic engine**: translating what the
bookkeeper knows into a restricted form that code can validate, test and execute.

## 2. Four non-obvious decisions

### 2.1 The model writes the formula, not the category

Each row of the formula has the form *text contains · date window · amount range · money in or out → P&L section +
category*. The engine applies it in a fixed order (**rule → transfer → Pending**), and whatever no rule covers stays
Pending and declared in every report. A rule can be read, tested against the book's history, approved and deleted; a
category assigned by a model cannot. The note *"the owner was on vacation in Europe from August 3 to 17"* becomes a
single row, `STARBUCKS · 2026-08-03 → 2026-08-17 · Owner Draw`, whose history test shows the **9** rows it changes, and
nothing else.

### 2.2 The bookkeeper's judgment is compiled once and runs without the model

A note like *"grocery purchases over $50 are usually personal"* is translated by the ⑥ agent **only once** into a closed
check (merchant types from a fixed list and the amount written in the note). Code validates the check and the auditor
applies it on every review **without calling the model again**. The model's interpretation stays visible (*what the AI
understood*) so the bookkeeper can correct it. The model acts as a compiler of professional judgment, not as a permanent
interpreter.

### 2.3 For reviewing each finding, a bigger model is not better

The contest guide suggests the large model for reasoning and the small one for speed. We measured it seat by seat, with
the decision rule written before running (details in *Technological Implementation*). In the seat that reviews each
finding, the larger models were more cautious, not more accurate, and **dismissed true errors as legitimate**: that is
why Nemotron Nano reviews. (The personal-spending judge is also Nano, but for stability: there Ultra found more on one
exam and did not win on the other two.) In the seat that drafts rules, a larger model does help, and the newest one does
not need to reason: Nemotron 3.5 Super VL, text only, matches the quality of Super with reasoning at a 39% lower cost and
in 3 seconds per call.

### 2.4 The web reaches a single model, which can only return a type

A web page can carry instructions. So only **one** model reads it, the ② verifier's classifier (Nemotron Super, without
reasoning), and its only possible output is **one business type from a closed list of 30**, with a confidence, in strict
JSON and with no tools. The other agents never see the text: they receive only the type and the confidence. So a page
can, at worst, get a type wrong, but it cannot give an agent orders. And the classifier was adopted by measurement: on
the same Tavily results, it gets **19–20 merchants right against 14** for the keyword table on the real book, with the
same errors (details in *Tavily*). Also, searching only the merchants that could be personal found **the same** (30 of 32
errors) with **40 credits instead of 56**, 29% fewer: the web does not solve a book's mechanical problems.

## 3. Verifiable safeguards

| Safeguard | What it does | Why it exists |
|---|---|---|
| Formula validator | Rejects rows outside the schema or the book's chart of accounts, duplicates, rows with no effect or rows that contradict a hand correction | Token Factory's strict JSON guarantees the **format**, not the truth: given an impossible value, the model returns a false and plausible one (`"7202-07-01"` as a date). The validator's test has **26 cases and 0 failures** |
| Nothing is executed | The engine interprets validated rows; there is no `eval` or `exec` on the server | A model output is never code |
| Grounds required | A rule only passes with a note, a repeated hand correction or high- or medium-confidence web evidence; without grounds, the model is not even called | A rule without grounds is a guess about the industry. Measuring, we found that a note with a period backed rows without a date window (in 2 of 12 runs a row that mixed different payments got through); now a note with a period only backs rows with a window |
| Findings the AI cannot dismiss | The reviewer does not judge note checks or personal-spending alerts, and cannot close as "legitimate" what the web identifies with high confidence as personal use | The reviewer called a streaming subscription booked as a business expense "legitimate" in 1 of 7 runs |
| Hand-written wins | A row fixed by hand is disconnected from the rules | The professional's decision prevails over any automation |
| Measured privacy | Person names are replaced with placeholders before every call | In what was actually sent: **0 full descriptions, 0 amounts and 0 person names**, in every run |

## 4. Understanding of the domain

- **The chart of accounts follows the IRS forms:** each account carries its Schedule C line (or 1120-S line), and the
  ones with no line of their own say where they go.
- **Payroll is understood as the bank sees it:** the auditor knows the bank only records the net pay and warns that the
  gross pay and the employer taxes come from the payroll summary.
- **"Personal" depends on the business:** the ⑤ agent judges each merchant with the industry, the notes and the
  categories of **that** book; a coffee is a deductible meal for one business and personal spending for another.
- **What only the client knows is not guessed:** payments between people, checks and transfers are never searched on the
  web and stay Pending so the bookkeeper can ask.

## 5. Limits, and how we will address them

| Limit | How we will address it |
|---|---|
| The architecture is not new: proposing in a restricted form, approval by a human and execution by a deterministic engine is how enterprise tools work | Not something to fix: the contribution is the user it is built for and the measured placement of models inside it |
| The drafter decides 15 of the real book's 118 Pending transactions; the rest stays with the bookkeeper | More sources of grounds for the drafter: the ③ reconciler and the client's answers |
| A note with no period still backs any row that cites it, even if it does not mention that merchant | Make it back only the merchants it names or the types the ⑥ agent understood |
| Missing detectors: a wrong category on a single row, an ATM withdrawal booked as an expense, a loan or an owner contribution booked as a sale | Build those detectors and measure them on the same exams |
| Each agent uses one prompt for every model; we did not write a prompt tailored to each model | Repeat the seat comparison with a prompt written for each candidate model |
| A correct store type does not say what was bought, and the ⑤ can take it as proof that the purchase is for the business | Stop the ⑤ from taking a store type as proof |
| A single real book | More authorized clients, each with its own exam |
