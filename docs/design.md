# Design — a complete product, built around the evidence

## Summary

We wanted a bookkeeper to be able to trust the AI without giving up the review. So everything lives in one flow, like
their daily work: the client's accounts connect through **Plaid** and their transactions update with one button (or come
in from a statement), the rules categorize them, whatever no rule covers stays **Pending** in plain sight, the agents
review and propose, and the bookkeeper decides. The Bookkeeping sheet works like a spreadsheet, with the tools a
bookkeeper uses every day: a formula bar, right-click to create a rule from a row, color labels ("ask the client",
"done"), filters by date, account and Pending, undo, a pivot and export to Excel. Every AI claim comes with its evidence,
and every action shows its effect before it runs: select a row and a panel tells its whole story, and every proposed rule
shows, before you approve it, the rows it would change. The P&L never hides what is Pending. And because the books of
many clients live in one console, an automated test goes through the **86 server actions** and confirms that each user
sees and changes only what is theirs.

---

## 1. Thesis

An AI tool for accounting is only usable if the professional can verify every result without leaving their workflow. So
ContaBu's design does not start from the AI but from the bookkeeper's work: one book per client, a spreadsheet for the
transactions, a formula for the rules and an income statement at the end. The AI fits into that flow as a collaborator
that proposes and explains, never as a black box that decides.

## 2. One flow, from start to finish

The sidebar is the map of that flow, in the same order: **Summary** (the P&L and what needs attention) · **Bank feeds**
(where transactions come in) · **Transactions** (the Bookkeeping sheet, the Pivot and the Rules) · **Notes** (what the
bookkeeper knows about that client) · **Audit** (the AI team) · **P&L** (what the client receives) · **Book settings**
(the chart of accounts, with each account's IRS line).

| Stage | What the bookkeeper does | What the design guarantees |
|---|---|---|
| Input | Connects the client's accounts through **Plaid** and updates them with one button (**↻ Update bank**), or uploads a CSV or XLSX statement | Both paths go through the **same engine**; updating or re-importing does not duplicate transactions |
| Categorization | Writes rules in a dedicated sheet or fixes rows by hand | Fixed order **rule → transfer → Pending**; what is written by hand is marked ✎ and no rule touches it again |
| Review | Clicks **Review the book** | Three visible steps: audit, check on the web, draft rules |
| Decision | Approves or dismisses each proposal | Before approving, sees **exactly which rows would change** |
| Delivery | Exports the income statement | Pending is declared in **all five outputs** of the P&L |

Consistency was checked output by output: of the P&L's five views (the card, the summary, the PDF, a closed period and
the list of saved projects), the list was the only one that left Pending out, and it was fixed. With Pending
transactions, the result is titled *before Pending*.

## 3. The Bookkeeping sheet: a spreadsheet a bookkeeper already knows how to use

Transactions live in a spreadsheet, not a form, because that is how a bookkeeper works. The category cell says where its
value came from, with the same logic as a formula in Excel:

| Mark | What it means |
|---|---|
| Green **ƒ** | The category was set by a **rule**: the cell is a formula. One click shows which rule |
| Faint **yellow** background with **✎** | The category was **written by hand**. Like a value typed over a formula in Excel, **it beats the rule**: no rule touches that row again. One click on ✎ makes it follow the rule again |
| **Red**, *Pending* | No rule covers it yet. It shows in red and is declared on the P&L; it is never hidden |

And it has the tools a bookkeeper uses every day:
- A **formula bar** with the full content of the selected cell.
- **Right-click:** view, edit, copy and paste; *Follow the rule again*; *Create a rule from this row*.
- **Color labels** with a meaning: blue (ask the client), green (done), red (problem), yellow (check this); and
  formatting: bold, italic and color.
- **Filters** by date, by account, Pending only or only what needs a look, and **search**.
- **Undo**, which brings back the exact cell, with its ƒ.
- Three tabs on the same data: **Bookkeeping**, **Pivot** and **Rules**; and **export to Excel** (CSV).

## 4. The evidence, always in sight

**Each row's side panel.** When a transaction is selected, its whole story appears on the right, in this order: the
**bookkeeper's note on that transaction**; **what the agents found**; the **category** and why it has it (which rule gave
it, or whether it was written by hand, or detected as a transfer, or whether no rule covers it); the **book notes** that
apply to it; and the **correction history**. Three decisions make it usable in a dense spreadsheet:
- **It narrows the sheet instead of covering it**, so the selected row always stays visible.
- **Each row's color marks come from the same table of findings as the Audit**, with the same severity criteria, and
  update on reload: fixing a cell makes its mark disappear.
- **It shows only the relevant notes:** the ones that name the merchant and are in force on its date, or the general ones
  whose date window covers it.

**What the agents found stays on the row.** Each finding is saved with the agent's verdict, its explanation in English
and in Spanish, which model judged it (*Judged by Nemotron 3 Nano*, taken from the log of the agents' calls) and the
Tavily web evidence with its link. Looking at a row again does not call the AI again: what was already paid for stays.

**The AI team.** The Audit shows each agent with its Nemotron model, what it did in the last run and what it cost, and
it updates agent by agent while the review runs. The agents are named by what they do (*Reviews each finding*, *Drafts
rules*, *Spots personal spending*, *Reads your notes*), not by a number. Two decisions answer real user questions:
- **"N proposals · M passed the validator":** how many rules the model wrote and how many code accepted are shown
  together, so the difference does not look like a bug.
- **When none pass, the screen explains why and what to do:** that nothing backed them, or how many were rejected for each
  reason, and what helps (write a note, fix two of the merchant's transactions by hand, look it up on the web).

**The history test.** Every proposed rule shows, before it is approved, the past rows it would change. The bookkeeper's
note about the owner's vacation becomes a rule with a date window that moves **9** rows — the coffees bought during the
trip — and nothing else.

**Notes: what the bookkeeper knows about the client.** One page keeps all of the bookkeeper's notes about that client.
In practice the note is the bookkeeper's prompt, so it has a prompt's tools: it shows what the AI understood, it can be
tested before saving (testing and saving cost a single call), it can be paused and turned back on without paying for the
translation again, and it shows which transactions it touched.

## 5. Decisions that came from measuring, not assuming

| What we found | What we changed |
|---|---|
| In the server logs, **two full reviews** launched from the same tab: with a cold server, an earlier query took 12.7 s and the button was still active | A per-book lock in the database (the second attempt spends nothing) and the button disables on the same click |
| The books of many clients live in one console, and a client must never see or change what is not theirs. An automated test calls **every server action** (view a book, change a category, approve a rule…) as the bookkeeper and as the client, and it found **12 improper accesses**, 5 of them older than the permissions layer | All closed; the test also requires that the bookkeeper is never refused. Today: **86 actions, 0 leaks** |
| On a phone, the page widened to **787 px** | Two grids with no declared columns; fixed to 375 px |
| A finding's explanation went stale when the screen switched between English and Spanish | The reviewer writes the explanation in English and in Spanish in the same call; doing so surfaced a real bug in the side panel, which was fixed |
| Each row's mark in a new column pushed the search and the fixed columns aside | The mark goes inside the date cell |

## 6. Limits, and how we will address them

| Limit | How we will address it |
|---|---|
| The business owner's platform does not exist yet: the client receives the income statement from their bookkeeper | Build the platform that connects the bookkeeper with their clients (see *Potential Impact*, "What's next") |
| The spreadsheet is designed for large screens | A dedicated phone view for the sheet |
| The call log has no run identifier: the "last run" is rebuilt by grouping calls with no gaps longer than 3 minutes | A run identifier on every call |
| There are no screens yet for PDF statements or check images | They come with the ③ reconciler, under construction |
