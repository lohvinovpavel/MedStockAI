# I2 — Copilot conversation persistence

**Service:** copilot gateway · **Flow:** 19 · **Status:** ✅ (wave 6, `20260818_wave6`) · **Depends on:** H1, I1 · **Scope:** `copilot:use`

## Goal

The drawer's conversation lives in `useState` and dies on refresh or navigation. Two reasons to
store it: continuity across a page change (the drawer follows the user between inventory and
forecasts), and evidence — a copilot that influenced a substitution decision must leave
something the audit trail can point at.

## API

### `POST /api/copilot/conversations` — `copilot:use`

`{ "facility_id": 1 }` → `{ "id": "…", "created_at": "…" }`

### `GET /api/copilot/conversations/{id}?limit=50&before=` — `copilot:use`

Newest-last message page for rehydrating the drawer.

### `GET /api/copilot/conversations?limit=10` — `copilot:use`

The caller's recent conversations, for a history list.

### `DELETE /api/copilot/conversations/{id}` — `copilot:use`

Soft delete — sets `deleted_at`. Messages that fed a `review_decision` are never hard-deleted.

## Data model

```sql
CREATE TABLE copilot_conversation (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  hospital_id uuid NOT NULL,
  actor_id    uuid NOT NULL,
  facility_id bigint REFERENCES facility(id),
  title       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz
);

CREATE TABLE copilot_message (
  id              bigserial PRIMARY KEY,
  conversation_id uuid NOT NULL REFERENCES copilot_conversation(id) ON DELETE CASCADE,
  hospital_id     uuid NOT NULL,
  role            text NOT NULL CHECK (role IN ('user','assistant','tool')),
  text            text,
  card            jsonb,                  -- po | analogues | certificate | emergency
  tool_name       text,
  ai_dedupe_key   text,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_copilot_msg ON copilot_message (conversation_id, created_at);
```

RLS per A4 on both tables.

## Rules

1. A conversation belongs to one actor. Sharing is not a feature; scoping to `actor_id` as well
   as `hospital_id` keeps a colleague's clinical questions private inside the tenant.
2. `tool` role rows store the tool name and the **result summary**, not the full payload. The
   full payload is reproducible from the endpoint, and copying it here duplicates PHI-adjacent
   data into a chat table for no gain.
3. `ai_dedupe_key` on assistant rows links to `ai_cache` (H2), which is what makes an answer
   replayable months later.
4. `title` is the first user message truncated to 60 characters. Do not spend a model call
   naming conversations.
5. Retention: 90 days for conversations with no linked decision; indefinite for any conversation
   referenced by a `review_decision`. State this in the response headers of the delete endpoint
   so the behaviour is discoverable.
6. Soft delete only. A user hiding a conversation must not erase evidence behind an order they
   approved.

## Acceptance criteria

- [x] Reloading the page restores the visible conversation in order.
- [x] Another user in the same tenant cannot read the conversation by id.
- [x] Deleting sets `deleted_at` and the conversation disappears from the list but remains readable by id for audit.
- [x] An assistant message created via `ask_ai` has a non-null `ai_dedupe_key`.
- [x] A 200-message conversation paginates without loading all rows.

## Out of scope

Full-text search over conversations, sharing or handoff between users, attachments, editing or
regenerating a previous turn.
