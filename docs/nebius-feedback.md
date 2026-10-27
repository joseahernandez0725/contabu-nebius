[← Back to README](../README.md)

# Feedback: Nebius Token Factory + NVIDIA Nemotron, from building ContaBu

Context: five bookkeeping agents returning strict JSON, via the OpenAI-compatible API (httpx, temperature 0). Findings 1–7 were measured on Nemotron 3 Nano 30B-A3B; 8–10, 12 and 13 compare Nano, 3.5 Lightning, Super 120B, Ultra 550B and 3.5 Super VL, agent by agent; 11 and 14 are about the service. Findings 8–10 are mid-October snapshots; the team we ship came out of them and of later runs: ① reviewer and ⑤ personal-spending judge on Nano, ② merchant classifier and ⑥ note translator on Super, ④ rule drafter on 3.5 Super VL, none reasoning. Every finding below comes from real calls we measured; each says what we tried, what happened, why (✅ = proven with a test; 🟡 = our hypothesis, since we can't see inside the service), why it matters to a developer, and what we suggest. Everything is reproducible with the commands at the end.

## Summary, by impact

| # | Finding | Impact on us | Suggested fix | Effort for Nebius (our guess) |
|---|---|---|---|---|
| 3 | Only chat_template_kwargs.enable_thinking turns reasoning off; reasoning_budget and reasoning_effort are accepted with 200 and ignored | The biggest cost lever: 6–8× cost, 16–75 s → 1–5 s per book | Document enable_thinking; make the documented reasoning_effort work on Nemotron (or say which models honor it); reject undocumented fields such as reasoning_budget with 400 | Low (docs) to medium |
| 2 | Reasoning consumes max_tokens; the answer arrives empty | 12,000 tokens billed for 0 answers | Document it; recommend a floor; separate reasoning cap | Low (docs) |
| 4 | The catalog API doesn't say which models support json_schema (the web cards have a "JSON mode" tag); strict mode guarantees format, not truth | A false date that passes the schema | Expose the tag in the API, json_schema/strict apart; document the behavior | Low |
| 1 | Reasoning tokens not reported separately in usage | Can't budget reasoning without estimating | Fill completion_tokens_details.reasoning_tokens | Low–medium |
| 5 | Temperature 0 isn't deterministic on long outputs; seed is ignored | Can't evaluate a prompt change with one run | Document; reject or honor seed; optional deterministic mode | Low (docs) |
| 7 | Without reasoning, Nano won't map a common word to a closed list; an optional field placed first gets omitted | Silent empty results | Guidance + example in the structured-output docs | Low (docs) |
| 9 | Bigger is not better at judging in JSON; reasoning rarely pays | Upgrading "just in case" cost up to 15× and lost findings | A short "which Nemotron for which task" guide with real per-call cost | Low (docs) |
| 10 | Super reasoning is the well-behaved reasoner: bounded, never truncated | The one seat where reasoning clearly paid (Pending 22% → 47%) | Publish typical reasoning length per model; recommend Super for "propose with judgment" | Low (docs) |
| 13 | On a blind exam, a larger model as judge finds FEWER errors (as the reviewer, on every exam; as the personal-spending judge, Ultra won one real-book exam) | "Use the biggest model for what matters" lost 1–3 errors per run in our judging seats | Model guide: separate generating from judging/filtering; measure recall, not only precision | Low (docs) |
| 12 | 3.5 Super VL, text only, performs like Super reasoning, about 4.5× faster; Super reasoning sometimes returns an almost empty answer | Our best drafter came from the "image" model; nothing in the catalog hinted at it | Say on VL's card how it does on text only vs. Super; say whether near-empty answers from Super reasoning are known | Low (docs) |
| 11 | When the trial ends, the notice doesn't say promotional credits need "paid usage", and the API returns a bare 402 | The app stopped mid-contest with credits still showing | Say it in the notice; a 402 with a code (trial_ended / insufficient_credits / billing_mode_required); warn days before | Low |
| 8 | Lightning, reasoning, writes ~3× more than Nano and runs out of tokens in half the calls | "Same price" ≠ "same cost": 2.7× | Typical reasoning length per model; a working reasoning cap; return partial reasoning on truncation | Low to medium |
| 14 | A window of 503s that no response explained silently degraded our results (the status page posted a dedicated-endpoint incident 16 min after our errors began) | A model comparison run in that window would have picked the wrong model | Retry-After and a reason (capacity / maintenance) in 503s; status components per model | Low to medium |
| 6 | The model id in the official cookbook returns 404 | The first copied example fails | Fix the id; case-insensitive ids or a "did you mean" | Low |

## What worked well

- The OpenAI-compatible API worked with a plain httpx client: one route, no SDK.
- json_schema with strict: true is really enforced (finding 4) — the right foundation for agents that must return data, not prose.
- With reasoning off, Nano is fast and cheap enough to run on every review; the full measured team costs $0.012 per review of three books.
- Super with reasoning on was predictable in length: 0 truncated answers in 144 calls (finding 10).
- 3.5 Super VL, with text only, became our best rule drafter on quality, cost and latency (finding 12).
- Reference costs (Nano, public catalog prices $0.06 in / $0.24 out per 1M tokens):

| Task | Input tokens | Output tokens | Cost | Latency |
|---|---|---|---|---|
| JSON ping, reasoning off | 37 | 18 | $0.000007 | 0.3–1.3 s |
| JSON ping, reasoning on (default) | 37 | 196–247 | $0.00005 | 1.0–1.4 s |
| Propose rules for a book, reasoning off | 2,000–4,000 | 110–1,000 | $0.0002–0.0004 | 1–5 s |
| Same, reasoning on | 2,000–4,000 | 4,900–16,000 | $0.0014–0.0040 | 16–75 s |
| Review 5 findings, reasoning off | ~1,400 | 300–400 | ~$0.00017 | 1.5–3.7 s |

## 1. Reasoning tokens are not reported separately in usage

What we tried: measure how much of each call's cost is reasoning and how much is the answer — the number that decides whether to let the model reason.

What happened: usage.completion_tokens_details is null on every call (✅ Oct 7). The reasoning arrives separately in choices[0].message.reasoning, but its tokens are added, undistinguished, to completion_tokens. Example: a ping with completion_tokens = 230, where content was a one-line JSON and reasoning held 848 characters.

Why: ✅ reasoning is generated output, so it is billed as output. 🟡 The OpenAI-compatible API already has a place for it (completion_tokens_details.reasoning_tokens), and the server already separates reasoning from content, so the number exists server-side; it just isn't published.

Why it matters: a developer can't measure the cost of reasoning without estimating it (we split completion tokens in proportion to the characters of reasoning vs content). In our task reasoning was 85–100% of the output (≈15,000 of 17,000 tokens across three books) — the number that sets the budget, and it isn't in the response.

Suggestion: fill usage.completion_tokens_details.reasoning_tokens, as the OpenAI API does. The field is already in Token Factory's API reference (✅ read Oct 26); on Nemotron it came back empty. SDKs already read it.

## 2. Reasoning consumes max_tokens and the answer arrives empty

What we tried: a strict-JSON ping with max_tokens = 200, plenty for {"ok": true, "model_family": "…"}.

What happened: finish_reason = "length", empty content, 200 tokens billed (✅ Oct 7). On a real task with max_tokens = 4,000, all three books ended the same way: 0 answers, 12,000 tokens billed, $0.0034. With a 16,000 cap one book still ran out (16,000 output tokens, 50 s, $0.0040), while the same input answered in 6,400 on another run.

Why: ✅ reasoning is the first part of the same generation and counts against the same max_tokens; if the budget is spent thinking, nothing is left for the answer. 🟡 Reasoning length depends on the input and varies between runs (finding 5), so there is no fixed "safe" cap.

Why it matters: max_tokens stops meaning "the length of my answer"; a cap that used to work (200 for a short JSON) fails with no prompt change; and the error looks like another one — the client sees empty content and thinks the model returned no JSON. We reported it as not_json until we looked at finish_reason.

Suggestion: (1) state on each reasoning model's page that max_tokens includes reasoning; (2) recommend a minimum max_tokens; (3) better, accept a separate reasoning cap (see finding 3).

## 3. Turning reasoning off works, but you have to discover how; the other controls are silently ignored

What we tried: turn off or limit Nano's reasoning.

What happened (✅ Oct 8, same question, temperature 0):

| Parameter sent | Response | Output tokens | Reasoning characters |
|---|---|---|---|
| (none) | 200 | 196 | 525 |
| chat_template_kwargs: {"enable_thinking": false} | 200 | 11 | 0 |
| chat_template_kwargs: {"reasoning_budget": 100} | 200 | 217 | 546 |
| reasoning_budget: 100 (in the body) | 200 | 196 | 533 |
| reasoning_effort: "low" | 200 | 394 | 1,054 |

Only enable_thinking works. We found it on the model card on Hugging Face, not in the Nebius docs. reasoning_budget, which that same card mentions, and reasoning_effort, the standard OpenAI parameter (listed in Token Factory's API reference, with no note on which models honor it — ✅ read Oct 26), are accepted with 200 and do nothing on Nemotron Nano — effort "low" reasoned twice as much. Reasoning is on by default.

Why: 🟡 enable_thinking is an argument of the model's chat template, which the server passes through; reasoning_budget seems to be implemented in NVIDIA's inference engine rather than in the template. The server does not reject fields it doesn't know.

Why it matters: switching reasoning on or off is the biggest cost decision. In our task, turning it off cut the cost from $0.0046–0.0068 to $0.0008 for three books (6–8× less) and latency from 16–75 s to 1–5 s per book — with an equal or better result. An ignored parameter is worse than an error: we believed we were capping spend.

Suggestion: (1) document chat_template_kwargs.enable_thinking on every reasoning model's page, with its default; (2) make reasoning_effort, already in the API reference, work on Nemotron, or say on each model card whether it is honored; (3) reject with 400 the fields that are not applied, such as reasoning_budget.

When reasoning DOES pay (data for the docs): with reasoning off, Nano got a two-trains arithmetic problem wrong (06:00 instead of 19:00); with it on, right. On our structured-extraction task it was the opposite. We tested the two effects separately:

| Variant | Corrections reproduced (of 4) | Vacation-note case (9 rows) | Rows rejected | Cost, 3 books |
|---|---|---|---|---|
| One long prompt, reasoning off | 0 | 0 | 1/11 | $0.0009 |
| One long prompt, reasoning on | 1 | 0 | 0/3 | $0.0064 |
| Short questions, reasoning off | 2 | 9, 0 extra | 3/13 | $0.0008 |
| Short questions, reasoning on (2 runs) | 1 · 0 | 9 · 9 | 5/19 · 0/11 (1 book out of tokens) | $0.0046 · $0.0068 |

Splitting the work into short questions is what solved the case — 9/9 with or without reasoning. Reasoning didn't help and cost 6–8× more. With reasoning, the model proposed 5 categories that did not exist in the chart of accounts we passed; without it, none.

## 4. The catalog API doesn't say which models accept json_schema, and "matches the schema" doesn't mean "tells the truth"

What we tried: strict JSON with response_format: {"type": "json_schema", "strict": true}.

What happened: the public catalog API (/api/public/models_info, ✅ Oct 4) does not say which models support json_schema; we found out by trying (Nano and Super accept it, ✅ Oct 7). The web model cards do carry a "JSON mode" tag for structured output (✅ docs, "Structured output & JSON", read Oct 26), but it does not say whether strict json_schema is supported. It IS enforced (✅ Oct 8). We deliberately asked for values that violate the schema:

| Asked for in the prompt | Schema rule | Strict json_schema | json_object |
|---|---|---|---|
| d = "July 2026" | pattern YYYY-MM-DD | "7202-07-01" | "July 2026" |
| n = 42 | integer 1–5 | 4 | 42 |
| e = "green" | "red" or "blue" | "blue" | "green" |
| an extra field x | additionalProperties: false | (omitted) | x: 1 |

Why: 🟡 constrained decoding — the server only lets the model emit tokens that keep the output valid against the schema. When the requested value doesn't fit, the model can't refuse; it emits the closest thing the schema allows: a correctly formatted date with an invented year, a number in range, the most likely allowed value.

Why it matters: strict mode guarantees the JSON can be parsed, not that the values are true. An impossible value arrives not as an error but as plausible false data. In accounting, a date "7202-07-01" that passes the schema is worse than an error. So we validate everything again in code (date ranges, each book's chart of accounts); our validator rejects that date (date_out_of_range). json_object, by contrast, enforces nothing beyond "it's JSON".

Suggestion: (1) expose the "JSON mode" tag in the catalog API, split into json_schema yes/no and strict yes/no; (2) document that strict mode forces the format, with the invented-date example; (3) 🟡 consider a way for the model to return "not applicable" (e.g., always allow null in strict mode) instead of inventing a value.

## 5. Temperature 0 isn't deterministic on long outputs, and seed is ignored

What we tried: repeat the same call, same input, temperature 0, to know whether a verdict is stable.

What happened (✅ Oct 8): short answers are stable (the reviewer, 5 runs, ~300 output tokens: 5/5 identical; the drafter on a small book, 4 runs, ~135 tokens: 4/4). Long answers differ: the drafter on a book with many merchants, ~900 output tokens, gave 4 different answers in 4 runs (4 to 8 rules, different sets). With seed: 42, 3 different answers in 3; with seed + top_p: 1e-9, the same. Both returned 200. With reasoning, the same input answered with 6,400 tokens once and ran out at 16,000 another time.

Why: 🟡 batched inference isn't bit-for-bit reproducible: the order of GPU sums changes with batch size, and in a mixture-of-experts model that can change which expert handles a token. A tiny difference in one token derails everything after it, so variation grows with output length. seed controls sampling, but at temperature 0 there is no sampling to control: the variation comes earlier.

Why it matters: a result can't be reproduced to debug it, and a prompt change can't be measured with one run. It happened to us: a difference we attributed to the model coincided with a prompt change of ours; only by repeating the same input 5 times could we separate the two. To measure, you have to repeat and report the variance.

Suggestion: (1) document that temperature 0 doesn't guarantee the same output and that seed doesn't make it deterministic; (2) reject seed if it isn't applied, or honor it; (3) 🟡 if possible, a deterministic mode (batch of 1), even at a higher price, for evaluations.

## 6. The model id in the official cookbook returns 404

What we tried: call Nano by copying the official cookbook (nebius/token-factory-cookbook on GitHub).

What happened (✅ Oct 8):

| Base URL | Model id | Result |
|---|---|---|
| api.tokenfactory.nebius.com/v1/ | nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B (catalog) | 200 |
| api.tokenfactory.nebius.com/v1/ | nvidia/nvidia-nemotron-3-nano-30b-a3b (cookbook) | 404 "does not exist" |
| api.tokenfactory.us-central1.nebius.com/v1/ (cookbook) | catalog id | 200 |
| api.tokenfactory.us-central1.nebius.com/v1/ | cookbook id | 404 |

Why: ✅ the id is case-sensitive and the cookbook's Nano guide has it in lowercase (still so on Oct 27: models/nemotron/nemotron3-nano-30b.md, 4 times; the folder's README has the right one).

Why it matters: the first example a developer copies fails, and the 404 says "does not exist", not "did you mean…?", which suggests the account has no access to the model.

Suggestion: fix the id in the cookbook, and make ids case-insensitive or have the 404 suggest the right one.

## 7. Without reasoning, Nano won't map a common word to a closed list; and an optional field placed first gets omitted

What we tried: have the model translate a bookkeeper's note ("Supermarket purchases over $50 are usually personal: review them") into a strict-JSON check: merchant types from a closed list (which includes grocery), merchants from the book, the minimum amount, and a boolean applies. Same prompt and input every time; only reasoning and field order changed.

What happened (✅ Oct 13, 12 calls, temperature 0):

| Reasoning | applies in the schema | Types? | applies in the answer? |
|---|---|---|---|
| off | first | [] in 3/3 | omitted in 3/3 |
| off | last | [] in 3/3 | present in 3/3 |
| on | first | ["grocery"] in 3/3 | omitted in 3/3 |
| on | last | ["grocery"] in 3/3 | present in 3/3 |

Without reasoning, 0 of 6 mapped "supermarket" to grocery, with the word in the note and grocery in the list; with reasoning, 6 of 6 (~960 input tokens, ~970–1,080 output, ~$0.0003, 3.4–4.6 s per call). Even with reasoning, the list of merchants varied between 0 and 3 across identical calls (finding 5). applies had a default (not required in the schema): placed first it was omitted in 6/6 answers, with and without reasoning; placed last it was filled in 6/6.

Why: 🟡 without reasoning the model writes the JSON in one pass, and going from a common word to a list label is an inference step it doesn't get to take; reasoning gives it that step. With constrained decoding an optional property can be skipped, and right after opening the object the model prefers to skip it; at the end it "knows" what to put.

Why it matters: turning reasoning off (finding 3) saves a lot, but for classifying against a list the result drops from 6/6 to 0/6 with no error — the JSON is valid and empty. Unmeasured, it looks like the note "doesn't apply". An optional field that disappears depending on its position is a silent error: the JSON still validates. What worked best for us was mixing: reasoning only in the call that classifies (one per note, ~$0.0003), off in the ones that summarize.

Suggestion: (1) in the strict-JSON guide, mark as required everything you need, because optional fields can be omitted, and note that property order matters; (2) a reasoning control that works on Nemotron (reasoning_effort or reasoning_budget, both ignored today, finding 3): classifying needs a little reasoning, not 1,000 tokens; (3) a docs example of "classify against a closed list", with and without reasoning.

## 8. Lightning, reasoning, writes ~3× more than Nano and runs out of tokens in half the calls

What we tried: the same four ContaBu agents (strict JSON, temperature 0, same prompt and input) on Nemotron 3 Nano and Nemotron 3.5 Lightning, which the catalog prices the same ($0.06 / $0.24 per million). With reasoning, the client raises max_tokens to 16,000 (finding 2).

What happened (✅ Oct 15, model_matrix.py, 2 runs per combination, 24 calls per model):

| Agent (calls per run) | Output per run, Nano reasoning | Output per run, Lightning reasoning |
|---|---|---|
| ⑤ personal for this business? (3) | 16,196 | 45,328 |
| ⑥ note translator (2) | 3,676 | 10,204 |
| ① reviewer (3) | 15,451 | 48,000 (all 3 calls at the cap) |
| ④ drafter (4) | 18,374 | 51,916 |

Ran out of tokens (finish_reason "length" at 16,000): Lightning 13 of 24 calls, Nano 0 of 24. A truncated call is billed in full and returns neither the answer nor the reasoning text. Cost of the four per run: Lightning reasoning $0.039, Nano reasoning $0.015 — same price per token; the difference is only how much it writes. Without reasoning, Lightning was the better of the two as ④ in that test: 33% Pending coverage in both runs (Nano: 10% and 24%), 100% precision, 3 s and $0.0018 per run (Nano: $0.0020).

Why: 🟡 Lightning seems trained to reason much longer by default; with a fixed cap that becomes truncated answers. From outside we can't see how much of the output is reasoning when the answer is cut.

Why it matters: "same price" isn't "same cost": with reasoning, the catalog's "fast" model cost us 2.7× more and failed half the time. Unmeasured, the switch looks free.

Suggestion: (1) publish a typical reasoning length per model (or a recommended max_tokens with reasoning); (2) make a reasoning cap work on Nemotron (reasoning_effort or reasoning_budget, both ignored today, finding 3) — exactly what would make Lightning useful; (3) return the partial reasoning when an answer is cut by length, so it can be diagnosed.

## 9. Bigger is not better at judging in JSON, and reasoning rarely pays (the model matrix)

What we tried: for each ContaBu agent, vary ONLY its model (Nano · Lightning · Super 120B · Ultra 550B) and reasoning (on/off), keeping the rest fixed, and measure quality against an exam written separately from the agents (32 planted errors in 3 demo books, plus the answers for the uncategorized transactions). 2 runs per combination, 64 in total, $0.70.

What happened (✅ Oct 15; quality is counted by code, never by the model):

| Agent | Best quality for the cost | What happened with the large models |
|---|---|---|
| ⑤ personal for this business? | Nano, reasoning off: flags 8–9 of 11 personal expenses, precision 86–92%, $0.001 | Super and Ultra: 100% precision but flag only 4–6 of 11 (more cautious), at 4–14× the cost. Reasoning lowered what it flags in 3 of the 4 models (same in Ultra) |
| ① reviewer | Nano, reasoning off: 30–31 of 32, precision 87–92%, 0 true errors called "legitimate", $0.001 | Super without reasoning dismissed 4–5 true errors as "legitimate"; Ultra, 2. None improved on Nano |
| ④ drafter | Lightning, reasoning off: coverage 33% (stable), 100% precision, $0.0018 | Ultra without reasoning: 43% stable, 100%, $0.028 (15×). Super reasoning: 48–51%, but 3 extra rows on the vacation case |
| ⑥ note translator | Ultra, reasoning off: the 3 errors the notes touch, in both runs, 1 s, $0.003 | Nano needs reasoning (without it: 0–1 of 3); Ultra reasoning didn't improve and cost 4× |

What repeats: with structured outputs over a closed list, the large models were more conservative, not more accurate. To FIND problems (⑤, ①) the small one won on these demo exams (for ⑤, not on every exam: see finding 13); to PROPOSE rules (④), where world knowledge matters, the larger ones won. Reasoning helped in 2 cases (the note translator on Nano; the ④ on Super, +10 points of coverage at 2.4× the cost) and was worse or equal in the rest, multiplying latency (up to 250 s in one call).

Why: 🟡 our prompts were tuned on Nano; a prompt written for Super or Ultra might do differently. The trend (bigger = more cautious) repeated in two different agents.

Why it matters: upgrading the model "just in case" can cost 15× and LOSE findings. You have to measure per agent: within one app, the best model changed from one task to the next.

Suggestion: a short "which Nemotron for which task" guide (classify, judge, propose), with the real per-call cost including typical reasoning.

## 10. Super reasoning is the catalog's well-behaved reasoner: bounded and never truncated

What we tried: validate the best model for each agent IN THE TEAM — ContaBu's full flow (4 model-backed agents + a deterministic auditor), on the same exam, varying one agent at a time (Nano · 3.5 Lightning · Super 120B · Ultra 550B, reasoning on and off). With reasoning, the client sets max_tokens = 16,000.

What happened (✅ Oct 15–16, team_matrix.py; per call, across all runs):

| With reasoning | Calls | Mean output per call (estimated reasoning) | Out of tokens | Max latency |
|---|---|---|---|---|
| Super 120B (the ④ drafter) | 144 | 3,115 (2,652) | 0 | 41 s |
| Ultra 550B | 34 | 1,300–3,712 | 0 | 33 s |
| Nano | 33 | 1,308–6,760 | 1 | 90 s |
| 3.5 Lightning | 32 | 5,189–11,313 | 9 | 261 s |

Super reasoning won a seat on quality: as the rule drafter it covers 47% of the uncategorized transactions with 99% precision (Super without reasoning: 41%; Ultra without reasoning: 37%; Nano: ~22%). It is the only seat in the team where reasoning clearly paid. Ultra WITHOUT reasoning had odd latency spikes: on the note translator (mean output 87 tokens) one call took 30 s; in the morning's matrix the same task took ~1 s. The other agents (judging, classifying) stayed on Nano without reasoning: no larger model and no reasoning beat them, and several found less (finding 9, now confirmed in the team).

Why: 🟡 Super's reasoning seems calibrated to the task (about 2,600 tokens), while Lightning's and Nano's run long without control. Without a working reasoning_budget (finding 3), the model makes the difference, not the developer.

Why it matters: if you want reasoning, the model matters more than the switch: Super reasoning was predictable (0 truncations in 144 calls) and Lightning wasn't (9 of 32).

Suggestion: publish the typical reasoning length per model (what we measured here), and recommend Super for "propose with judgment" tasks in the model guide.

## 11. When the trial ends, the notice doesn't explain that promotional credits require "paid usage", and the API returns 402 without saying why

What happened (✅ seen on Oct 16 in the Nebius console and in the app's calls; 🟡 we didn't keep the exact 402 body — our client translated it to "no balance" without saving it): the account had promotional credits (the contest's). When the trial ended, the console showed a "trial ended" notice and every call started returning 402. The notice doesn't say that promotional credits are only used under "paid usage": the account has to be switched to that mode (with a payment method) even if the credits are enough. We found out by searching. The API returned 402 without the reason (trial ended, no balance, or billing mode). After switching the account to "paid usage", the same call returned 200 again (✅ Oct 16, nemotron.py --ping).

Why it matters: with credits in sight and a silent 402, the natural conclusion is "the credits ran out" or "the key is wrong", and time is lost. In the middle of a contest, the app stops working without any code change.

Suggestion: (1) in the "trial ended" notice, say in one line that promotional credits are still there and are used after switching to "paid usage", with the button to do it; (2) give the 402 a code and a message: trial_ended, insufficient_credits or billing_mode_required; (3) warn a few days before the trial ends, by email or in the console.

(Since Oct 16, our client logs the body of a 402, for next time.)

## 12. Nemotron 3.5 Super VL, used with text only, performs like Super reasoning, about 4.5 times faster

What we tried: the multimodal model (nvidia/Nemotron-3.5-Super-VL, same price as Super) in every ContaBu agent, text only, against the current team run in the same batch (✅ Oct 16, team_matrix.py --vl; 28 runs of the full flow; $0.68).

What happened: as the rule drafter, without reasoning, it matches Super reasoning: 30–31 of 32 errors found by the team, 46% of the uncategorized transactions covered at 100% precision. It costs 39% less per run ($0.012 vs. $0.019 for Super reasoning's normal runs) and answers in 3 s per call (max 6 s), against 15 s on average (max 31) for Super reasoning. It accepts json_schema like the others, and enable_thinking works (with reasoning: 24 s per call, with no gain in quality). 🟡 Super reasoning, in the same batch, stumbled 2 times out of 6: it returned a valid but almost empty answer for a whole book (1–2 rows instead of 15–17), with no error and no token cut-off. VL without reasoning didn't stumble in 8 runs. We don't know whether it's the model or the service.

Why it matters: the catalog presents VL as "the image one". For structured text, it was our best drafter on quality, cost and latency. Nothing in the catalog suggests it.

Suggestion: say on VL's model card how it does with text only compared with Super, and whether an "almost empty" answer from Super reasoning is known behavior.

## 13. On a blind exam, a larger model as judge finds FEWER errors

What we tried: confirm finding 9 on errors the agents were never tuned on — the blind exam (22 errors planted and written separately) — changing one seat at a time. ⑤ (personal for this business?) is the agent that adds errors; ① (reviewer) is the one that filters. Each was run with Lightning, Super, Ultra and 3.5 Super VL, all without reasoning, against the current team (both on Nano), 3 runs each, in the same batch. The reading rule was written before: "X detects more" only if its worst run beats the control's best (✅ Oct 17, team_matrix.py --blind-seats; 27 runs; $0.46).

What happened: the control finds 14–15 of 22 (code alone, 8 in all 27 runs). A larger ⑤: all four find 12–13. They contribute 3–4 errors instead of 5–6, with somewhat higher precision (81–85% vs. 78–79%). Ultra costs 2.4 times more per run and finds fewer. A larger ①: none finds more. Super and Ultra dismiss 1–3 true errors per run as "legitimate" (Nano and Lightning, 0). 3.5 Super VL finds fewer (12–13) and dismisses 3 in every run, even though it is our best rule drafter (finding 12). Limit: each agent uses one prompt for every model; we did not write a prompt tuned to each model.

One exception, on a later exam: on a real book with 12 errors planted by a practicing bookkeeper, Ultra as ⑤ found 10 of 12 against Nano's 7; it did not win on the blind demo exam or on a second real-book exam, and the best large model changed just by rewriting the prompt, so ⑤ stayed on Nano for stability. As ① (the reviewer), no larger model found more on any exam: Super, Ultra and 3.5 Super VL dismissed 6–7 true errors as "legitimate" on the first real-book exam (errors picked at random by a program).

Why it matters: "use the biggest model for what matters" fails when what matters is NOT letting something through: as the reviewer, Nemotron's larger models were more cautious, not more accurate, on every exam, including data they were never tuned on. As the judge that flags personal spending, the result changed with the exam and the prompt. One model can be the best in one seat and the worst in another.

Suggestion: have the model-selection guide distinguish generating from judging or filtering, and not recommend moving up in size to classify without measuring what is lost (recall), not only precision.

## 14. A window of 503s that no response explained, and results that degrade silently

What we tried: a long measurement batch (five Nemotron models, seat by seat, three runs per variant) with our usual client: up to 3 attempts per call on 429, 500, 502, 503 and 504, with a doubling wait (✅ nemotron._post).

What happened: on Oct 24, between 06:06 and 06:47 PDT, Token Factory answered HTTP 503 to many calls: 224 needed one retry and 192 a second; across the whole batch there were also 41 network failures. No response said why or when the service would be back. The serious part was not the error but its effect: in the runs inside that window, an agent with a FIXED model (the rule drafter, on 3.5 Super VL) dropped from 44 to 0–34 Pending transactions resolved, and the seat we were measuring seemed to lose to four candidates. We repeated those runs outside the window: the drafter was back to 44 of 44 in 17 of 18 runs (41 in the other), and the seat's result changed.

Why: 🟡 our hypothesis is a temporary lack of capacity. Correction (✅ read Oct 26): there is a status page, status.nebius.com, with Token Factory per region (not per model), and on Oct 24 it posted "Token Factory 503 errors in eu-west2" for dedicated endpoints, from 13:22 to 14:24 UTC. Our 503s (the serverless API) began at 13:06 UTC, 16 minutes earlier; we cannot tell from outside whether it was the same cause. We missed that page during the batch.

Why it matters: a 503 that survives the retries leaves an agent without an answer, and in a team of agents that does not always show up as an error — it shows up as a worse result. A model comparison run in that window picks the wrong model. We caught it only because an agent with a fixed model dropped for no reason; without that control, we would have changed a seat because of a provider failure.

Suggestion: a capacity signal in the response itself (Retry-After on 503s and a body that says capacity or maintenance), so a client can pause a batch instead of measuring on a degraded service; and status components per model, with incidents that say whether the serverless API is affected.

## Scope

We used Token Factory with five Nemotron models (Nano, 3.5 Lightning, Super, Ultra, 3.5 Super VL); the product runs three of them. Beyond Token Factory and the account's billing (finding 11), we did not use other Nebius AI Cloud services, so we have no feedback on them.

## How to reproduce

Everything was measured with the code in the repo (backend/nemotron.py, agents.py, measure.py); the key goes in backend/.env (NEBIUS_API_KEY).

```bash
cd backend
python nemotron.py --ping                 # reasoning off
python nemotron.py --ping --reasoning     # reasoning on
python measure.py --demo --agent          # the agents on the demo data
python measure.py --demo --agent --reasoning
python measure.py --demo --audit          # against the demo's answer key (finding 7 and the notes)
python model_matrix.py                    # the model matrix (findings 8 and 9; ~$0.70)
python team_matrix.py --plan              # the best model per agent, in the team (finding 10; ~$2)
python team_matrix.py --vl                # 3.5 Super VL in every seat (finding 12; ~$0.70)
python team_matrix.py --blind-seats       # a larger ⑤ or ① on the blind exam (finding 13; ~$0.46)
```
