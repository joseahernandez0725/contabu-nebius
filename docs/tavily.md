[← Back to README](../README.md)

# Tavily — how the merchant verifier uses the web, and how we tested it

## Summary

To know whether an expense belongs to the business, sometimes you need to know what the merchant is. The merchant
verifier looks it up on **Tavily** at review time, and **Nemotron Super** reads the results and picks the business type
from a list of 30: so the reviewer can say *"this is a gym booked as an office expense"*. It searches only what is
suspicious and **never people**, and web text cannot give orders to anyone: a single model reads it, and that model can
only return a type. On the real book, of **23 merchants** whose business type the bookkeeper wrote down before searching,
Super identified **19–20** correctly (**83–87%**, depending on the run) **reading Tavily's results**, against 14 (61%)
for our keyword table, without AI, which read **exactly the same results**. And it spends little: the first review of the three demo books (**387
transactions** over three months), in an office that has not searched any merchant yet, uses **33–35 credits**; the
second, **0**, because each office remembers the merchants it already searched, for all of its clients.

![The Check on the web step of the AI team panel: Looks the merchant up online, Tavily + Nemotron 3 Super, 1 web search, 3.3 s, 1 credit.](img/tavily-search.png)

*A real search, as the Audit tab shows it: the review that built the guided tour searched 1 merchant on Tavily (3.3 s, 1 credit); the rest came from the office's memory.*

---

## 1. What it does

- **Where it runs:** in *Review the book*, on the merchants of findings that could be personal spending (mostly the ones
  the personal-spending agent flagged) and on the **10 merchants with the most Pending transactions**, so the rule
  drafter has evidence for what no rule covers. Also on demand, with *Search this merchant*, from a finding or from a transaction's panel. Mechanical
  problems (a row outside its rule, money that came in booked as an expense) are not searched: the web does not solve
  them.
- **Why only search:** the verifier needs a short, ranked list of what a merchant is, not a page's full text or a
  crawl of a site, so we use Tavily's search alone. The next step, for merchants that share a name with another
  business, is to ask the search for the book's own region (see the limits below).
- **How it searches:** a **basic** search first (1 credit). If the confidence is not high, an **advanced** one (2
  credits), once, keeping the advanced answer.
- **How it classifies:** Nemotron Super, without reasoning, receives the query, the book's region and the 5 trimmed
  results, and returns the business type, the confidence, which result it used, and whether that result is about
  **this** merchant. If the model fails or leaves the schema, the keyword table classifies.
- **It remembers:** each merchant is searched once per office; the second review comes from memory and spends no
  credits. Every search and every classification is logged, with what it cost.
- **Where the call happens:** `backend/merchant_verifier.py` sends the search to Tavily at review time (`verify_flagged`
  for the review, `search_one` for a single merchant). To see it in the live demo, run *Review the book* in Audit: the
  "Check on the web" step shows each search with its seconds and credits; or press *🔎 Search this merchant* on a Pending
  merchant. The guided tour is read-only: its web evidence was searched once with Tavily and saved, so it spends nothing.

## 2. What it never does

- **It never searches people.** Before every search, code drops payments between people (Zelle, Venmo, Cash App…),
  checks, transfers, card payments, bank fees, incoming money and any query that contains the name of a person from the
  book.
- **It never sends the full description or an amount:** the query is a short phrase with the merchant's name, built by
  code.
- **It never lets web text give orders.** The text of the results reaches a single model, the classifier, with four
  defenses: (1) it can only return **one type from the closed list**, or none; (2) in **strict JSON**; (3) **with no
  tools**: it cannot search, write or call anything; (4) the prompt states that the results are data, never instructions.
  Code has the last word on the answer: if the model says the result is about another merchant with the same name, or
  cites a result that does not exist, the confidence drops and nobody uses it. The other agents never see the web text:
  they receive only the type and the confidence.

## 3. The search: how we tested it

We used the three demo books, with a **key of types written before searching**: 44 merchants with their business type,
7 with no type and 4 ambiguous. Each merchant was searched **with both searches, twice**. One of the three books was
used to tune the keyword table, so it **does not count as a test**: the results are from the other two. These figures
were measured with the keyword table, before Super classified; they serve to choose how to search.

**Is it right when it says "high"?**

| Search | High confidence | Medium confidence | Low confidence |
|---|---|---|---|
| Basic | **40 of 44** | 2 of 8 | 8 of 14 |
| Advanced | **44 of 50** | 6 of 8 | 4 of 8 |

**Which way to search pays off?**

| Policy | Correct | Wrong evidence that reaches the agents | Credits |
|---|---|---|---|
| Basic only | 52 of 68 | 10 | 106 |
| Basic, then advanced if not high, keeping the better | 52 of 68 | 12 | 202 |
| **Basic, then advanced if not high, keeping the advanced (today's)** | **56 of 68** | **8** | **202** |
| Advanced only | 56 of 68 | 8 | 212 |

**Does it help the team?** On the demo's first exam (32 errors), with live search the team found the same errors as with
the saved evidence (29 and 31 of 32 live; 31 and 31 saved), and the verifier contributed the right correction on **4** errors in each run, against 2 with
the saved evidence.

## 4. The classification: the keyword table against the Nemotron models

**The question.** On the **same raw results** from Tavily, what classifies the business type better: the keyword table
or a Nemotron model?

**The method.** The raw results were fetched **only once** and saved (186 credits): 37 demo merchants, from the two books
not used to tune the table, and 25 merchants from the real book (2026), with basic and advanced search. After that, zero
credits: the table and the **10 candidates** (Token Factory's five Nemotron models, with and without reasoning) read exactly the
same thing, **3 times each**. 33 demo merchants and 23 real-book merchants were scored (the ambiguous ones do not count),
with today's search policy. We counted what the agents actually use: the high- or medium-confidence answers, right and
wrong. **The rule, written before running:** a model is adopted only if, on both keys, it has more usable right answers
than the table and no more wrong ones.

**The real book's key, and why 4 of its rows were corrected.** The key says, for each merchant in the real book, what
kind of business it is; the book's bookkeeper wrote it **before seeing any search**. Then two things about the verifier
changed: the list of types gained a new one, *government and taxes*, and the search for a merchant named like its city
switched to searching the merchant, not the city. With those two changes, 4 answers in the key went stale, and we
corrected them using what the bookkeeper had written in each row's comment:
- 3 merchants they had left "with no type", whose comments said *taxes*, *vehicle registration* and *licenses and
  permits*, move to *government and taxes*.
- The merchant named like its city moves to *hardware store*, which is what it is.

Each correction was logged with what it said before and why it changed.

**Result** (usable answers, right · wrong, 3 runs):

| Classifier | Demo (33) | Real book, corrected key (23) |
|---|---|---|
| Keyword table | 25 · 4 | 14 · 2 |
| **Nemotron Super, no reasoning (adopted)** | **25–26 · 3–4** | **19–20 · 2** |
| Nemotron 3.5 Super VL, no reasoning | 25 · 6 | 22 · 1 |
| Nemotron 3.5 Super VL, reasoning | 22–24 · 3–5 | 21–22 · 0 |

The other seven candidates (Nano, 3.5 Lightning and Ultra, with and without reasoning, and Super with reasoning) were
measured the same way, with the key before the correction, and none beat Super on the demo without making more errors.

**The decision.** We do not pick the model that gets the most right, but the one that gets more right **without getting
more wrong**: a confident wrong answer reaches the agents as evidence and can lead them into an error, so the rule asks
for both. That is why 3.5 Super VL was not chosen: it gets more right on the real book, but on the demo it makes 6
usable errors against the table's 4. With Super, the rule written beforehand **was not met**: on the demo, its worst run
(25) ties the table. We adopted it because **in no run, on neither key, was it worse than the table**, in right answers
or in errors, and on the real book, which the table never saw, it beats it by 5–6 right answers with the same errors.
The tie on the demo is explained by the table having been tuned on the demo's results: it plays at home there. The
classifier costs **$0.0004 and one second per merchant**.

**Credits with Super.** The advanced search is only requested if the basic one's confidence is not high, so the
classifier decides how many credits are spent. Computed on the saved results (0 credits), with today's search policy:

| Merchants | With the keyword table | With Super | Fewer with Super |
|---|---|---|---|
| The 37 demo merchants | 73 | 49–53 | 27–33% |
| The 25 real-book merchants | 53 | 29–35 | 34–45% |

Super spends less because it answers "high" more often on the basic search and uses the advanced one less.

## 5. The real book (2026)

- **Credits per review** (measured with the keyword table): the first *Review the book* searches 9 merchants, all flagged
  by the audit, and spends **21 credits**; the second, with the office's memory, **0**. The same in both runs. With
  Super, on the saved results for the real book's 25 merchants, the count drops from 53 to 29–35 credits (§4).
- **Repeatable:** the 2 repetitions of the search gave exactly the same answer in all 50 cases (merchant × search type).
- **Privacy, measured on what was sent:** **0 queries with a person's name and 0 equal to the full bank line**, in the 100
  test searches and the 30 review searches. Only the query and the business's region (its city and state) go to the
  classifier.

## 6. In the demo

- The three demo books (387 transactions) spend **33–35 credits** on the first review (measured with the keyword table);
  the second, **0**. With Super, on the saved results for the demo's 37 merchants, the count drops from 73 to 49–53 credits
  (§4).
- So the demo does not spend on every visitor, the evidence for its books was fetched once and is copied into each office,
  except for **three merchants left out on purpose**: an evaluator's first review makes real Tavily searches, and the
  verify step says how many were live and what they cost.

## 7. With and without Tavily: what a merchant's type tells, and what it doesn't

**What we learned:** Tavily tells **what the merchant is**, not **what was bought**. For the reviewer and the rule
drafter, that is exactly what they need: knowing that a merchant is a gym or a hardware store lets the reviewer say
*"this is a gym booked as an office expense"* and the drafter ground a rule. For the personal-spending agent, at
general stores such as a supermarket or a department store, it is not enough: the type makes it plausible that the
business buys there, and the alert disappears. And without the type it finds more errors, but not because it judges
better: it suspects more merchants, and on the real book the ones it flags on top are all business expenses. Adding up
the three runs, that is 10 false alarms with Tavily, 17 without Tavily and 26 without the type for that agent only.
We measured it with four conditions on the same day and with the alerts labeled by the bookkeeper; neither of the two
fixes we tried met the rule written beforehand, so the product stays as submitted.

The same exams, the same code and the same day, three runs per condition, with the whole team. The four conditions:

- **With Tavily:** the agents receive the merchant types Tavily found, saved from earlier real searches. The same
  evidence in all 3 runs, and 0 credits.
- **Without Tavily:** no search at all, and the saved types are hidden from every agent (personal spending, reviewer,
  auditor, note checks and rule drafter): the office's memory reads as empty.
- **Without Tavily for the personal-spending agent only:** the other agents still see the evidence; that agent alone
  does not get the verified type.
- **With Tavily and the purchase context:** the personal-spending agent sees the verified type and also how the
  bookkeeper booked each merchant's purchases (the section and the category; never an amount, a description or a name).
- **Why the saved evidence:** so that the only difference between the conditions is what the agents see, not what Tavily
  happens to return that day.

The scripts are `backend/no_web_matrix.py` and `backend/context_matrix.py`; they do not change the product or the
measured files. On the real book, the bookkeeper labeled every extra merchant the personal-spending agent flagged (14
merchants): all 14 are business expenses, so every extra alert is a false alarm.

| Three runs each | With Tavily | Without Tavily | Without Tavily for personal spending only | With Tavily and the purchase context |
|---|---|---|---|---|
| Real book: hidden errors found (of 12) | 7 · 7 · 7 | **12 · 12 · 12** | **12 · 12 · 12** | 0 · 6 · 0 |
| Real book: false alarms of the personal-spending agent (labeled) | 8 · 1 · 1 | 5 · 5 · 7 | 10 · 8 · 8 | 0 · 2 · 0 |
| Demo, blind exam (of 22) | **14 · 14 · 14** | 14 · 14 · 13 | **14 · 14 · 14** | 10 · 10 · 10 |
| Demo, first exam (not blind, of 32) | 30 · 32 · 30 | 32 · 32 · 30 | 32 · 30 · 32 | 26 · 26 · 26 |
| Demo: false alarms of the personal-spending agent | 2 · 2 · 2 | 3 · 2 · 3 | 2 · 2 · 2 | 1 · 1 · 1 |
| Demo: Pending transactions covered by correct proposed rules (of 91) | 41 · 44 · 44 | 42 · 42 · 42 | **44 · 44 · 44** | **44 · 44 · 44** |
| Sent to the model: descriptions · amounts · names | 0 · 0 · 0 | 0 · 0 · 0 | 0 · 0 · 0 | 0 · 0 · 0 |

*Two runs stand out: the first run with Tavily gave 8 false alarms on the real book, and the other two, 1; and the
second run with the purchase context found 6 errors, and the other two, 0. In the other conditions, the three runs are
close. That is why false alarms are compared by adding up the three runs, not run by run. The run records show the
cause is the model, not the evidence or the service: the three runs of each condition received the same saved
evidence and the same merchant types, with no failed calls, and only the model's answers changed (in the first run
with Tavily it judged 7 more merchants with no web type as personal; in the second run with the purchase context, the
same merchants went from low to medium confidence, which is the threshold for an alert).*

- **On the demo, with and without Tavily nearly tie:** the blind exam gives 14 · 14 · 14 against 14 · 14 · 13. With
  Tavily, the personal-spending agent makes fewer false alarms (2 against 2–3), and up to 2 more Pending transactions get
  a correct rule.
- **On the real book, the cause is confirmed:** the personal-spending agent judges the merchant, not the purchase. A
  verified type such as "supermarket" or "department store" makes "the business could buy here" plausible, so it answers
  "mixed" and the alert disappears. Taking the type away from **that agent alone** is enough for it to find all 12 errors,
  as without Tavily. It is not the keyword table's doing: with Super classifying live, the team found 7 · 7 · 5 of 12 on
  the same exam.
- **Both fixes were measured against a rule written before running, and neither meets it:**
  - *Taking the type away from the personal-spending agent only* loses nothing on the demo and finds all 12 errors, but
    its extra alerts are all false alarms: 8–10 per run, more than with Tavily off for everyone (5–7).
  - *Giving it the purchase context* makes things worse: on the real book it finds 0 · 6 · 0 of 12, and on the demo it
    drops to 10 of 22 on the blind exam. The hidden personal purchases are booked as business expenses, so the booked
    category confirms to the agent what it was supposed to question.
- **The decision:** the verifier stays on for every agent, as submitted. With Tavily, the personal-spending agent lets
  errors through on the real book; without the type, it catches them all, but at the cost of false alarms the bookkeeper
  would have to dismiss by hand. Neither is free, and we do not change the product without a fix that passes the rule.
- **An earlier run** found the same errors, 7 against 12, but its extra alerts, unlabeled, were different (5–8 with
  Tavily, 12–13 without): that is why we labeled them before deciding.

## 8. Limits, and how we will address them

| Limit | How we will address it |
|---|---|
| Small samples: 33 demo merchants and 23 from the real book | More authorized real books, each with its key written before searching |
| A store type does not say what was bought: on the real book, the personal-spending agent finds 7 of 12 with the web and 12 without the type, but without the type it adds 8–10 false alarms per run; giving it the booked category makes it worse (§7) | The agent sees the merchant, not what was bought. Next: at a general store, the alert should not go away but move to "ask the client" instead, measured with the same rule and the same labels |
| A merchant with the same name as another business can bring the other one's evidence | Ask the search for the book's own region, besides having the classifier flag it |
| The demo's saved evidence was classified with the keyword table; in the public demo, Super classifies only the live merchants and manual searches | Re-classify that evidence with Super and re-measure the demo exams |
| The search policy was chosen with the keyword table; on the real book, *advanced only* was as accurate, made fewer errors (6 against 8) and spent less (100 against 118 credits) | Repeat that comparison with Super classifying and adopt the winner |
