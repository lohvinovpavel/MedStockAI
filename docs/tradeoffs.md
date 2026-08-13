#

- Removed `ai-handler` (the queue/worker service) entirely -> Gemini is now called directly from `analogue`/`prediction` via `ask_ai()`, cached in `ai_cache` -> to save the cost of a Deployment/NetworkPolicy/async contract for benefits (crash-survival, KEDA-on-queue-depth) that weren't actually in use at this volume. See docs/services.md §4 for the full trade-off.
- Don't use external secret manager -> to save time
- 