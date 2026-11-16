# Who does what — tool, model, human

Three workers, one session. Most bad outcomes come from one of them doing another's job.

```
        ┌──────────────────────────────────────────────────────────────┐
        │  HUMAN                                                       │
        │  scope · authorization · approval · send · judge impact      │
        └───────────────▲───────────────────────────┬──────────────────┘
                        │ approves / sends          │ asks, decides
        ┌───────────────┴──────────────┐   ┌────────▼─────────────────┐
        │  MODEL (AI)                  │   │  TOOL (livehunt)         │
        │  interpret · hypothesise     │◄──┤  capture · normalise     │
        │  choose test · write prose   │   │  classify · rank · remember│
        └───────────────┬──────────────┘   └────────▲─────────────────┘
                        │  one request (proposal)    │  one state block
                        └────────────────────────────┴──  agent --json
```

The interface is deliberately narrow:

- **tool → model:** one state block (`livehunt agent --json`), ~250 tokens, no bodies.
- **model → tool:** one recorded test (`livehunt test …`), one line.
- **model → human:** one proposed request, with its falsifier.
- **human → world:** the request itself. Only the human sends.

---

## 1. The tool's job (deterministic, boring, reliable)

| Job | What it means | What it must never do |
|---|---|---|
| **Capture** | record every request/response from the user's Chrome | send, replay, fuzz, crawl |
| **Normalise** | turn URLs into `object × principal × action` | guess ownership |
| **Classify** | tag each flow with a bug family (rules, not judgement) | call something a vulnerability |
| **Rank** | score signals (id in path, write verb, money field) | claim severity or impact |
| **Remember** | cursor, hypotheses, tests, dead ends | forget or silently retest |

The tool is allowed to be **wrong about importance** and must still be **right about facts**.
A tag is a lead. A score is a sort order. Neither is a finding.

## 2. The model's job (judgement, language, planning)

| Job | What it means | What it must never do |
|---|---|---|
| **Interpret** | explain what a result means for this app | invent data or coverage |
| **Hypothesise** | one falsifiable claim, with an alternative | say "could be vulnerable" |
| **Choose** | pick the next test, predict the expected result | batch three tests |
| **Write** | the 6-line review, the report prose | decide scope |
| **Chain** | combine primitives into an impact story | send requests |

The model is allowed to be **wrong about a hypothesis** — that is what tests are for. It is not
allowed to be **wrong about the evidence**.

## 3. The human's job (authority, risk, reality)

| Job | Why only the human |
|---|---|
| **Scope / authorization** | no tool or model can grant permission |
| **Approval** | writes, deletes, money, recovery = real consequences |
| **Sending** | keeps attribution clean: one human action, one result |
| **Impact judgement** | business context lives outside the data |
| **Stopping** | two clean tries, then move on |

---

## 4. How they interact — the handoff, step by step

```
1. HUMAN   surfs one flow
2. TOOL    captures it                         (flows table)
3. HUMAN   livehunt drain
4. TOOL    joins objects/principals, ranks     (objects, touches, hypotheses)
5. TOOL    emits state                         (agent --json)
6. MODEL   reads state, picks ONE hypothesis, writes:
             hypothesis · evidence · alternative · falsifier · request · confidence
7. HUMAN   approves (or refuses)
8. HUMAN   sends the request                   (curl / Repeater)
9. HUMAN   reports the raw result
10. MODEL  interprets: proved / killed / inconclusive + what it means
11. TOOL   records it                          (livehunt test … → ledger)
12. repeat from 5
```

Two artifacts cross the boundary in each direction. Nothing else should:

| Direction | Artifact | Contains | Never contains |
|---|---|---|---|
| tool → model | `agent --json` | counts, ids, classes, next | bodies, tokens |
| model → tool | `test <H> <result>` | one line of outcome | speculation |

## 5. What each side must ask the other

**The model should ask the tool** (via commands, not guesses):
- "what objects does this endpoint touch?" → `query objects` / `object <kind> <id>`
- "was this object seen under two principals?" → `query collisions`
- "what did I already test here?" → `agent` (tests_recent + ledger)
- "what is new since last drain?" → `agent` (flows.cursor)

**The tool should ask the human** (it cannot know):
- which hosts are in scope
- which Chrome context is A, and whether B exists
- whether a destructive test is allowed

**The model should ask the human** (it must not assume):
- approval before any state change
- the business meaning of a field ("is `total` user-facing?")
- whether a dead end is truly dead

## 6. Failure modes when the boundary is crossed

| Violation | What you get |
|---|---|
| tool pretends to judge ("this is critical") | false confidence, wasted reports |
| model pretends to remember ("we tested that") | retests, duplicated effort |
| model sends the request itself | broken attribution, scope risk |
| human skips recording | the ledger rots, next session repeats |
| human pastes bodies into the model | context burn, no signal |
| anyone widens the host filter | out-of-scope capture, legal risk |

## 7. A concrete handoff (real shape)

```
TOOL   H4 IDOR-write PUT /api/expenses/{id} s12 x1
       object expense/1001 seen under A and B

MODEL  Hypothesis: the write path trusts the client's expense id.
       Evidence: object 1001 touched by A and B; PUT carries amount in the body.
       Alternative: the server re-checks ownership and returns 403 (already correct).
       Falsifier: 403 for B on A's expense.
       Request: PUT /api/expenses/1001 {"amount":0} as B.
       Confidence: medium — collision exists, write check unproven.

HUMAN  approved, sending.
       → 200 {"ok":true}

MODEL  Proved: B changed A's expense. That is a write-IDOR with money impact.
       Next: confirm the read-back shows the new amount under A.

TOOL   livehunt test navan 4 confirmed --flow 812 --note "B PUT A's expense → 200"
```

Every line in that exchange has exactly one owner.

## 8. Checklist before you start a session

- [ ] human has stated scope and hosts
- [ ] tool is running with that host filter (`livehunt up`)
- [ ] contexts labelled A (and B if it exists)
- [ ] model has the manual/AI guide loaded
- [ ] agreed: one test at a time, record every result
- [ ] agreed: the model proposes, the human sends

## 9. The one-sentence version

**The tool makes the facts undeniable, the model makes them meaningful, the human makes them
authorized.** Remove any one and the session gets slower, noisier, or unsafe.
