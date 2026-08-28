# Extending the backend API

The API is split so each new piece of work gets its own file instead of
everyone editing the same handler code.

```
backend/app/api/
  main.py           # app factory: creates FastAPI(), adds CORS, registers routers
  deps.py           # small shared plumbing (e.g. loading a fixture into a contract type)
  routers/
    health.py       # GET /health
    optimize.py     # POST /optimize
```

## Adding your own endpoint

1. Create `backend/app/api/routers/your_thing.py`:

   ```python
   from fastapi import APIRouter
   from contracts import YourExistingContractType

   router = APIRouter()

   @router.post("/your-endpoint", response_model=YourExistingContractType)
   def your_handler(request: SomeExistingContractType) -> YourExistingContractType:
       ...
   ```

2. Register it in `backend/app/api/main.py`:

   ```python
   from backend.app.api.routers import your_thing
   app.include_router(your_thing.router)
   ```

That's it — no other file needs to change.

## Rules that don't change

- **`contracts/` is the only schema surface.** Request/response types
  for any new endpoint must be existing types from `contracts/`
  (or plain built-ins) — never a new parallel schema that duplicates
  what a contract already says.
- **`solve()` is the only scheduling entry point.** New endpoints
  (e.g. a future disruption/re-optimize endpoint) call
  `backend.app.optimizer.solver.solve()` the same way `routers/optimize.py`
  does; they don't reimplement scheduling logic inline.
- **AI/ML output is never a hard constraint.** Anything a future
  `/score`-style endpoint produces (`priority_score`, `risk_score` on
  `BlockCandidate`) is an objective-function input only — see
  `docs/architecture.md`.

See `docs/contracts.md` for the open domain-review questions that
should get resolved before new endpoints lean too heavily on today's
contract shapes (`track_id` modeling in particular).
