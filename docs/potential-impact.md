[← Back to README](../README.md)

# Potential Impact — a large market, an underserved user and evidence on a real book

## Summary

The United States has **71,408 bookkeeping offices**, with 4 people on average, and the trade is shrinking **6%** toward
2035, while their clients are still there: **36.4 million businesses**, **5.8 million** of them Hispanic-owned. ContaBu
is built for that small bookkeeper, in English and in Spanish, and also for the owner who is just starting out and keeps
their own books. We tested it where it matters, on a real book (*an authorized client account*, with **197 current
transactions from 2026**): a bookkeeper hid **12 errors** of the kind they see at work; our fixed checks in code, without
AI, found none, and the full system found **7**, all personal spending. And of the 15 Pending transactions for which the
AI proposed rules, it got 7 right on its own and **all 15** with a single note from the bookkeeper. Reviewing that whole
book takes **about 30 seconds** and costs **less than a cent**: about one second of a bookkeeper's wage.

![Results on a real book, an authorized client account with 197 transactions from 2026, 3 runs per condition with identical results. Errors found, of 12 hidden by a practicing bookkeeper: code without AI 0 of 12, the full system 7 of 12. Rules right, of 15 Pending transactions the AI decides: without the bookkeeper's note 7 of 15, with a one-line note 15 of 15. Cost of a full review in Nemotron: $0.007 to $0.008, less than a cent, about 1 second of a bookkeeper's wage.](img/real-book-results.png)

---

## 1. Thesis

Small-business bookkeeping is judgment work repeated at scale: every month, for every client, someone has to classify
every bank transaction, catch what does not belong and deliver an income statement the tax preparer can use. That work
is done mostly by very small offices. We hold that a tool that **catches errors before they reach the client, without
taking the decision away from the bookkeeper, at a marginal cost of cents per book**, has a broad, concrete and growing
market.

## 2. The size of the problem

**The clients: many businesses, almost all small, and all accountable to the IRS.**

| What we measured | Figure | What it means for the bookkeeper | Source |
|---|---|---|---|
| How many businesses there are in the United States, with and without employees | **36.4 million**, with $50.0 trillion in receipts in 2023 | It is the universe of books someone has to keep | U.S. Census Bureau, *Annual Business Survey* and *Nonemployer Statistics by Demographics*, reference year 2023 (released Nov 20, 2025) |
| What share of them are small businesses | **99.9%** (the SBA counts 36,207,130; a different source and year, which is why it does not match the 36.4 million exactly) | Almost all of that universe is small | SBA Office of Advocacy, *Frequently Asked Questions About Small Business 2026* |
| What share of a small business's federal paperwork comes from the IRS | **more than 80%** | Keeping the books right is, above all, being able to answer to the IRS: that is why every ContaBu account carries its IRS line | SBA Office of Advocacy, same source |

**The people who keep the books.**

| Indicator | Figure | Source |
|---|---|---|
| Private *Other Accounting Services* offices (NAICS 541219: bookkeepers', non-CPA accountants' and billing offices) | **71,408**, with 287,389 employees: **4.0 per office** on average | U.S. Bureau of Labor Statistics, *Quarterly Census of Employment and Wages*, 2024 annual averages |
| *Bookkeeping, accounting and auditing clerks* jobs | 1,532,400 in 2025 | BLS, *Occupational Outlook Handbook* |
| Projected change in those jobs, 2025–2035 | **−85,600 (−6%)** | BLS, *Occupational Outlook Handbook* |
| Median wage | $50,670 a year (May 2025) | BLS, *Occupational Outlook Handbook* |

**Our reading:** bookkeeping jobs are falling while the businesses to serve are growing (Hispanic-owned employer firms
alone grew 22% from 2021 to 2023; see §3), and the typical office has four people. Productivity per bookkeeper has to
rise. That is the gap ContaBu goes after: not replacing the bookkeeper, but letting them review more books with the same
care.

## 3. The Hispanic segment, and ContaBu in English and Spanish end to end

| Indicator (reference year 2023) | Figure | Source |
|---|---|---|
| Hispanic-owned employer firms | **496,000** (8.4% of employer firms), with $730.3 billion in receipts | U.S. Census Bureau, *Annual Business Survey* (released Nov 20, 2025) |
| Hispanic-owned nonemployer firms | **5.3 million** (17.5% of nonemployer firms), with $244.2 billion in receipts | U.S. Census Bureau, *Nonemployer Statistics by Demographics* (released Nov 20, 2025) |
| Growth of Hispanic-owned employer firms | from 406,086 (2021) to 465,202 (2022) and 496,000 (2023) | U.S. Census Bureau, *Annual Business Survey*, 2022, 2023 and 2024 editions |

Together, Hispanic-owned businesses had **$974.5 billion** in receipts in 2023 ($730.3 + $244.2 billion). They are
**nearly one in six** businesses in the country, and the share is higher among the smallest: 17.5% of nonemployer
businesses, against 8.4% of employer businesses. That is why every ContaBu screen is in **English and in Spanish**.

**English and Spanish end to end, with one click.** ContaBu is written entirely in both languages: every screen, every
button, every message and the demo guide exist in English and in Spanish, and the language switches with one click on
any screen, without losing anything (the data is stored in English; the language is only the layer you see). The agents
that explain a finding (the reviewer and the personal-spending agent) write their explanation **in both languages in the
same call**, so switching languages does not pay for the review again. In practice, a bookkeeper can do all the work in
Spanish, talk to their client in Spanish, and deliver the P&L as a PDF **in English**, the language of the tax preparer
and the IRS. For the bookkeeper who serves Hispanic owners, the difference is not having to translate their own work.

ContaBu was born from the daily work of a bookkeeper in the United States: every tool in this document answers a
mistake that work finds.

## 4. The problem, in concrete terms

The errors in a small book are not abstract. Three cases, planted in the demo books and caught by *Review the book*:

- **A gym booked as an office expense** at a cleaning company: the owner's personal spending.
- **Payroll the bank only shows net:** the gross pay and the employer taxes never appear as such in the bank, and the
  P&L needs them.
- **One merchant in two categories:** cleaning supplies, except for one row booked as an office expense.

The hard part is not data entry but **judgment**: *"Starbucks is a deductible meal for this client, except from August 3
to 17, when the owner was on vacation"*. A model that assigns categories can read that judgment, but then the answer to
*"why this number?"* is *"because the model said so"*, and a bookkeeper cannot sign that. ContaBu turns judgment into
rules and checks that can be read, tested and approved.

## 5. Evidence on a real book

The book is the bank account of a small business, used with its owner's permission (*an authorized client account*):
its **197 transactions from 2026**, categorized by a bookkeeper. It never goes into the demo or the repository. We ran
two tests on that book.

**1. Errors a bookkeeper finds at work.** A practicing bookkeeper planted in a copy of the book the errors they see in
practice: 12 hidden from the system, almost all personal spending (athletic wear, beauty, underwear, an amusement park),
plus an ATM withdrawal and a gas station booked wrong.

| | Found (of 12) |
|---|---|
| Code without AI | 0 |
| The full system, 3 runs | **7 · 7 · 7** |

The 7 are personal spending, caught by the agent that judges whether a merchant is personal **for this particular
business**. Four more errors, not hidden (purchases on the wrong side of an amount threshold), are caught by code
without AI in all three runs.

**2. The bookkeeper's judgment as input.** Of the book's **118 Pending transactions**, the rule drafter proposed rules
that decide 15. We compared them with what the bookkeeper actually decided about those transactions:

| | Transactions it decides | Right |
|---|---|---|
| Without the bookkeeper's note | 15 | 7 |
| With a one-line note | 15 | **15** |

Three runs per condition, with identical results. The note set the amount threshold that separates materials from
supplies at one store; the agent reproduced it exactly and cited it.

**What that first run taught us.** On a newly arrived book, with no notes or corrections, the system discarded every rule
the agent proposed, for lack of evidence: the web verifier searched only the merchants flagged as suspicious, and a
Pending merchant never is. In the demo the problem did not show because its evidence is saved in advance. The fix: the
verify step also searches the 10 merchants with the most Pending transactions. In the demo, Pending coverage went from
42–45 to 44–50 of 91 transactions, with 100% precision.

## 6. Time and cost

| A full review | Time | Cost |
|---|---|---|
| The real book (2026) | 31–34 s | $0.007–0.008 in Nemotron |
| The three demo books (387 transactions) | 107–109 s | $0.012 in Nemotron; 33–35 Tavily credits the first time (measured with the keyword table; with Super, 27 to 45% fewer: see [Tavily §4](tavily.md#4-the-classification-the-keyword-table-against-the-nemotron-models)), 0 the second |

At the trade's median wage ($24.36 an hour), a full review of the real book costs the same as **1.0 to 1.2 seconds** of
the bookkeeper's time.

**The same cost serves the owner who is just starting out.** ContaBu is built for the bookkeeper, but nothing in it
requires being one: the owner of a business that is just starting and wants to keep their own books opens their office,
connects their bank and uses the same rules, notes and reviews. With a full review at less than a cent, AI stops being an
expense they have to justify.

## 7. Current scope and limits, and how we will address them

| Limit | How we will address it |
|---|---|
| Cash basis and income statement: no double entry and no balance sheet | A balance sheet derived from cash, then double entry |
| It does not include payroll, invoicing or tax filing | Out of scope on purpose: ContaBu gives the preparer a P&L with the IRS lines |
| Missing detectors: a wrong category on a single row, an ATM withdrawal booked as an expense, a loan or an owner contribution booked as a sale | Build those detectors and measure them on the same exams |
| The drafter decides 15 of the real book's 118 Pending transactions; the rest stays with the bookkeeper | More sources of grounds: the statement reconciler (checks with names) and the client's answers |
| A single real book, and no paying clients yet | More authorized clients, each with its own exam |
| The personal purchases in the real book's exam were added to the copy, because the book had none of those kinds | Exams written by bookkeepers on books that contain those expenses for real |

ContaBu is a bookkeeping tool, not tax advice.

## 8. What's next (a plan, not a current feature)

Bookkeeping is the beginning of what ContaBu wants to offer. Two pieces are planned; neither is available yet:

- **The statement reconciler (under construction).** It will store bank statements and check images, reconcile every transaction
  in the book against the PDF statement and assign each check the name of whoever cashed it, automatically. It is the
  piece that turns into evidence what today only the client knows.
- **The platform that connects bookkeepers with business owners.** The owner will receive their income statement, answer
  their bookkeeper's questions and see their book, while the bookkeeper still keeps it.

## Sources

- U.S. Census Bureau, *Census Bureau Releases New Data About Characteristics of Employer and Nonemployer Business
  Owners*, Nov 20, 2025: https://www.census.gov/newsroom/press-releases/2025/business-owner-characteristics.html
- U.S. Census Bureau, *Construction Sector Had Highest Number of Hispanic-Owned Businesses*, Oct 2024:
  https://www.census.gov/library/stories/2024/10/hispanic-owned-businesses.html
- U.S. Census Bureau, *Census Bureau Releases New Data on Minority-Owned, Veteran-Owned and Women-Owned Businesses*,
  2024: https://www.census.gov/newsroom/press-releases/2024/employer-businesses.html
- U.S. Bureau of Labor Statistics, *Quarterly Census of Employment and Wages*, 2024, NAICS 541219, private sector:
  https://data.bls.gov/cew/data/api/2024/a/industry/541219.csv
- U.S. Bureau of Labor Statistics, *Occupational Outlook Handbook: Bookkeeping, Accounting, and Auditing Clerks*:
  https://www.bls.gov/ooh/office-and-administrative-support/bookkeeping-accounting-and-auditing-clerks.htm
- SBA Office of Advocacy, *Frequently Asked Questions About Small Business 2026*:
  https://advocacy.sba.gov/2026/02/03/frequently-asked-questions-about-small-business-2026/
