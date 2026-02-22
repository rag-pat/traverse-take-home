# Add Check Dependencies

The Healthchecks codebase is at `/app/`. It's a Django app for monitoring cron jobs.

## What to build

Add upstream dependency tracking between checks in the same project. When a check goes DOWN, if any of its declared dependencies are also currently DOWN, its alerts are suppressed entirely — the failure is likely caused by the upstream dependency, not the check itself. When dependencies recover, normal alerting resumes automatically.

The critical integration point is `Flip.select_channels()` in `/app/hc/api/models.py` — this is the method that decides which channels get notified when a check's status changes. The suppression logic must be inserted there.

Three API operations are needed: list dependencies (GET), add a dependency (POST), and remove a dependency (POST).

## 1. Model changes (`/app/hc/api/models.py`)

### Add `dependencies` field to `Check`

Add a self-referential many-to-many field on the `Check` model, after the existing status-related fields and before `class Meta`:

| Field | Type | Details |
|-------|------|---------|
| `dependencies` | `ManyToManyField` | self-referential, `symmetrical=False`, `blank=True`, `related_name="dependents"` |

`symmetrical=False` means A depending on B does not imply B depends on A. `related_name="dependents"` lets you query which checks depend on a given check.

### Modify `Flip.select_channels()`

When the status transition is to `"down"`, check whether any of the check's declared dependencies currently have `status="down"`. If so, return an empty list — suppress all notifications. This logic should be inserted before the existing channel queryset that builds the notification list.

Transitions to other statuses (e.g., `"up"`) should never be suppressed by dependencies.

### Update `Check.to_dict()`

Add `deps_count` (integer) to the return dict — the number of dependencies this check has. This field must always be present, even when `0`.

## 2. API endpoints (`/app/hc/api/views.py`)

Use the same decorator and dispatcher patterns as existing views in `views.py`. For endpoints that support both GET and POST, look at how other views dispatch on `request.method`.

### `GET /api/v3/checks/<uuid:code>/deps/`

List all dependencies of a check.

- Uses read-level authentication
- Returns `{"dependencies": [...]}` where each item is the dependency check's full `to_dict()` representation
- `403` if check belongs to a different project
- `404` if check doesn't exist

### `POST /api/v3/checks/<uuid:code>/deps/`

Add a dependency to a check.

- Uses write-level authentication
- JSON body:
  - `dep` (required) — UUID string of the check to add as a dependency
- Validation (in this order):
  - `400` with `{"error": "missing dep"}` if `dep` not provided
  - `400` with `{"error": "invalid dep"}` if `dep` is not a valid UUID string
  - `404` if the dependency check doesn't exist
  - `400` with `{"error": "check not found in project"}` if the dependency is in a different project
  - `400` with `{"error": "cannot depend on itself"}` if `dep` is the check itself
- If the dependency already exists, return `200` (idempotent, no error)
- `400` with `{"error": "too many dependencies"}` if the check already has 10 dependencies
- If new, add it and return `201`
- Response body: `{"dependencies": [...]}` (same format as GET)
- `403` if the check belongs to a different project, `404` if the check doesn't exist

### `POST /api/v3/checks/<uuid:code>/remove-dep`

Remove a dependency from a check.

- Uses write-level authentication
- JSON body:
  - `dep` (required) — UUID string of the dependency to remove
- Validation:
  - `400` with `{"error": "missing dep"}` if `dep` not provided
- If `dep` is not a valid UUID, doesn't resolve to a check, the check is not in the same project, or it's not currently a dependency — silently ignore and return `200`
- Response body: `{"dependencies": [...]}` (same format as GET)
- `403` if the check belongs to a different project, `404` if the check doesn't exist

## 3. URL routes (`/app/hc/api/urls.py`)

Add URL routes for dependency listing/adding and removal, following the naming conventions in `urls.py`.

## 4. Migration (`/app/hc/api/migrations/`)

Generate with `python manage.py makemigrations api --name check_dependencies`.

## Constraints

- Don't modify existing tests
- `is_valid_uuid_string()` is already imported in `views.py` for UUID validation
- Follow existing patterns for decorators, error responses, and dispatchers
