# Add Check Cloning API

The Healthchecks codebase is at `/app/`. It's a Django app for monitoring cron jobs.

## What to build

Add a cloning feature to the REST API so users can duplicate a fully-configured check in one call. The clone gets a fresh UUID and clean state but inherits all configuration (schedule, filters, keywords, etc.). Cross-project cloning is supported via a `target_api_key` parameter — e.g., clone a staging monitor into production.

## 1. `CloneLog` model (`/app/hc/api/models.py`)

New model to record clone history:

| Field | Type | Details |
|-------|------|---------|
| `code` | `UUIDField` | `default=uuid.uuid4, editable=False, unique=True` |
| `source` | `ForeignKey` to `Check` | `on_delete=models.SET_NULL, null=True, related_name="clone_logs"` |
| `clone` | `ForeignKey` to `Check` | `on_delete=models.SET_NULL, null=True, related_name="+"` |
| `created` | `DateTimeField` | `default=now` |

Use `SET_NULL` (not `CASCADE`) so the log survives if either the source or clone check is deleted.

Add `to_dict()` returning:

| Key | Value |
|-----|-------|
| `uuid` | `str(self.code)` |
| `source` | `str(self.source.code)` if source exists, else `None` |
| `clone` | `str(self.clone.code)` if clone exists, else `None` |
| `created` | `isostring(self.created)` |

`Meta` class: `ordering = ["-created"]`.

## 2. Migration (`/app/hc/api/migrations/`)

Generate with `python manage.py makemigrations api`.

## 3. `Check.clone()` method (`/app/hc/api/models.py`)

Add `clone(self, target_project)` to the `Check` model. It creates a new check in `target_project` by copying configuration fields and resetting state fields.

### Fields to copy

| Field | Note |
|-------|------|
| `name` | Append `" (copy)"` to the original name |
| `tags` | Exact copy |
| `desc` | Exact copy |
| `kind` | Exact copy |
| `timeout` | Exact copy |
| `grace` | Exact copy |
| `schedule` | Exact copy |
| `tz` | Exact copy |
| `filter_subject` | Exact copy |
| `filter_body` | Exact copy |
| `start_kw` | Exact copy |
| `success_kw` | Exact copy |
| `failure_kw` | Exact copy |
| `methods` | Exact copy |
| `manual_resume` | Exact copy |

### Fields to reset on the new check

| Field | Value | Reason |
|-------|-------|--------|
| `code` | Auto-generated | New UUID via model default |
| `project` | `target_project` | May differ from source |
| `created` | `now()` | It's a new check |
| `slug` | `""` | Avoids slug collision with original |
| `status` | `"new"` | Clone has not been pinged |
| `n_pings` | `0` | No pings received |
| `last_ping` | `None` | Never pinged |
| `last_start` | `None` | Never started |
| `last_start_rid` | `None` | No run ID |
| `last_duration` | `None` | No duration data |
| `has_confirmation_link` | `False` | No pings means no links |
| `alert_after` | `None` | No alert scheduled |
| `badge_key` | `None` | Gets its own badge if needed |

After creating and saving the new check:

1. Call `assign_all_channels()` on the clone to give it all channels from the target project.
2. Create a `CloneLog` entry with `source=self` (the original) and `clone=` the new check.
3. Return the new check.

## 4. API endpoints (`/app/hc/api/views.py`)

### `POST /api/v3/checks/<uuid:code>/clones/`

Clone a check.

- Use `@authorize` (write key required)
- JSON body (all optional):
  - `target_api_key` — write API key of a different project to clone into
- Validate that `target_api_key` is a string if provided (return `400` if not)
- Logic:
  1. Look up source check by UUID. Return `404` if not found.
  2. Verify the check belongs to the caller's project. Return `403` if not.
  3. Determine target project:
     - If `target_api_key` is provided, look up the project with that API key. Return `400` with `{"error": "invalid target_api_key"}` if no project matches.
     - If not provided, clone into the same project.
  4. Check capacity: `target_project.num_checks_available() > 0`. Return `403` with `{"error": "target project has no checks available"}` if at capacity.
  5. Call `check.clone(target_project)`.
  6. Return the new check's `to_dict()` with status `201`.
- Error responses:
  - `401` — Invalid API key (handled by `@authorize`)
  - `403` — Check belongs to different project, or target at capacity
  - `404` — Check doesn't exist
  - `400` — Invalid `target_api_key`

### `GET /api/v3/checks/<uuid:code>/clones/`

List clone history for a check (clones made FROM this check).

- Use `@authorize_read` (read-only key works)
- Returns `{"clones": [clone_log.to_dict(), ...]}`
- Only includes `CloneLog` entries where `source=check`
- `403` if wrong project, `404` if check doesn't exist

Wire these up with a dispatcher called `check_clones` that sends GET to the list handler and POST to the clone handler. Decorate with `@csrf_exempt` and `@cors("GET", "POST")`. The `@cors` decorator handles OPTIONS preflight requests automatically (returns `204` with CORS headers).

## 5. URL routes (`/app/hc/api/urls.py`)

Add to the `api_urls` list (works across v1/v2/v3 automatically):

```
path("checks/<uuid:code>/clones/", views.check_clones, name="hc-api-clones"),
```

## 6. `Check.to_dict()` (`/app/hc/api/models.py`)

Add `"clones_count"` (integer) to the dict, before the `if self.kind == "simple":` block. It should be the number of `CloneLog` entries where this check is the source.

## Constraints

- Don't modify existing tests
- Use `isostring()` for datetime formatting (already in the codebase)
- Follow existing patterns for decorators, error responses, etc.
