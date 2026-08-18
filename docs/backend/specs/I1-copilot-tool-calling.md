# I1 — Copilot chat with tool calling

**Service:** new thin gateway (`/api/copilot`) or `analogue` extended · **Flow:** 19 · **Status:** ✅ (wave 6) — analogue hosts `/api/copilot`
**Depends on:** A4, B2, C5, D1, E1 · **Scope:** `copilot:use`

## Goal

The drawer is a real model conversation (`POST /api/copilot/messages`) whose answers come from
the services that already own the data — never from the model's own knowledge of drugs.

**No framework.** `ask_ai()` is already the orchestration layer: registry, cache, validation,
degradation, in ~150 commented lines the team owns. LangChain would replace that with control
flow they do not own, and it hides intermediate steps exactly where this product needs them
recorded. Native Gemini function calling, one tool per existing endpoint.

## Tools

Each maps to an endpoint that already exists or is specified:

| Tool | Endpoint | Scope required |
|---|---|---|
| `get_stock` | `GET /api/inventory/stock` | `inventory:read` |
| `find_analogues` | `GET /api/analogue/analogues/{rxcui}` | `drug:search` |
| `check_certificate` | `GET /api/compliance/status` | `inventory:read` |
| `get_forecast` | `GET /api/prediction/forecast/{rxcui}` | `forecast:read` |
| `draft_order` | `POST /api/inventory/orders` (status `draft`) | `order:write` |

## The authorization payoff

Every one of those endpoints already carries `Depends(require(...))`. The gateway forwards
**the caller's own JWT** on each tool call — no service account, no separate permission model.
A physician's copilot cannot draft an order because `POST /orders` refuses their token, in the
same code path that refuses their browser. The copilot inherits authorization per tool, for
free, and that is far easier to defend than any bespoke gating.

## API

### `POST /api/copilot/messages` — `copilot:use`

```json
{ "conversation_id": "…", "text": "Do we have anything to replace norepinephrine?",
  "focus": { "type": "sku", "ndc": "0409-1782-01", "facility_id": 1 } }
```

Server-sent events: `token` deltas, then `tool_call` / `tool_result` frames, then a final
`message` frame with the structured card.

`focus` is the selected inventory row — the drawer already tracks it in `CopilotFocus`.

## Card contract

The existing card kinds (`po`, `analogues`, `certificate`, `emergency` in
`web/lib/copilot-context.tsx`) are already a function-calling schema. Keep them: a tool result
maps to exactly one card, and the model's prose wraps it rather than restating it.

## Rules

1. **Every factual claim comes from a tool result.** The model composes and explains; it never
   supplies a stock number, a price, an expiry, or a substitute from its own knowledge. If no
   tool returned data, the answer is "I don't have that" — not a plausible guess about a drug.
2. Tool calls are capped: max 5 per turn, 20-second budget. On exhaustion, return what was
   gathered plus a note. A copilot that hangs is worse than one that answers partially.
3. A tool call returning 403 surfaces as "you do not have access to that", not as an error the
   model paraphrases into a hallucinated answer.
4. `draft_order` is the only writing tool and always produces `status: draft` requiring human
   confirmation (flow 13). No tool places, cancels, or deletes anything.
5. Model outages degrade, they do not 500 — the `AIError` contract in `ask_ai`. Fall back to
   the quick-action cards, which are pure endpoint reads and need no model at all.
6. Every turn records its `ai_dedupe_key` (H2) so a copilot-driven decision is traceable.
7. Prompt-injection boundary: tool results are **data**. Text arriving from a certificate
   record, a shortage description, or a drug label must never be followed as an instruction.
   Wrap tool results in a delimited block and say so in the system prompt.

## Acceptance criteria

- [x] Asking for stock triggers `get_stock` and the number in the reply matches the endpoint's.
- [x] A physician asking for a draft order gets a refusal sourced from a real 403, not a model refusal.
- [x] With the model unavailable, quick actions still return cards.
- [x] A question needing no tool (a greeting) makes no tool calls.
- [x] A tool result containing "ignore previous instructions" does not change behaviour.
- [x] Turn count and tool-call count are bounded under an adversarial prompt.

## Out of scope

Multi-step planning or agent loops (if that need is real, use LangGraph or the Claude Agent
SDK — an explicit, inspectable state machine — not chains), voice, multi-turn tool retries,
streaming partial cards.
