# Add Channel Muting

The Healthchecks codebase is at `/app/`. It's a Django app for monitoring cron jobs.

## What to build

Add a time-based muting feature for notification channels. When a channel is muted, it is silenced — it won't receive any alert notifications until the mute expires. Two new API endpoints: mute (with a duration in seconds) and unmute.

The critical integration point is `Flip.select_channels()` in `/app/hc/api/models.py` — this is the method that decides which channels get notified when a check's status changes. Muted channels must be excluded there.

## 1. Model changes (`/app/hc/api/models.py`)

### Add `muted_until` field to `Channel`

Add after the `disabled` field:

| Field | Type | Details |
|-------|------|---------|
| `muted_until` | `DateTimeField` | `null=True, blank=True` |

When `muted_until` is `None`, the channel is not muted. When it's a future datetime, the channel is muted until that time.

### Modify `Flip.select_channels()`

This method currently builds a queryset that excludes disabled channels. Add an additional exclusion so channels whose `muted_until` is in the future (i.e., currently muted) are also filtered out. Channels with `muted_until=None` (never muted or already expired) must still pass through — `NULL` values should not be excluded.

The `now()` function is already imported in the file via `from django.utils.timezone import now`.

### Update `Channel.to_dict()`

Add `muted_until` to the existing return dict. Use `isostring()` (already in the codebase) for datetime formatting — it returns `None` when the value is `None`, ISO 8601 string otherwise.

## 2. API endpoints (`/app/hc/api/views.py`)

Use the same decorator pattern as the existing `pause` and `resume` views in `views.py`.

### `POST /api/v3/channels/<uuid:code>/mute`

Mute a channel for a given duration.

- JSON body:
  - `duration` (required) — integer, number of seconds to mute (1–31536000, i.e. up to 1 year)
- Validation:
  - `400` with `{"error": "missing duration"}` if not provided
  - `400` with `{"error": "invalid duration"}` if not an integer (floats are rejected)
  - `400` with `{"error": "duration out of range"}` if < 1 or > 31536000
- Set `muted_until` to a datetime that is `duration` seconds from now, then save
- If already muted, just overwrites with the new value (no special handling)
- Returns `channel.to_dict()` with status `200`
- `403` if channel belongs to a different project
- `404` if channel doesn't exist

### `POST /api/v3/channels/<uuid:code>/unmute`

Unmute a channel.

- No body needed
- Sets `channel.muted_until = None`, saves
- Idempotent — works even if the channel is not currently muted
- Returns `channel.to_dict()` with status `200`
- `403` if wrong project, `404` if not found

## 3. URL routes (`/app/hc/api/urls.py`)

Add URL routes for muting and unmuting a channel by UUID, following the naming conventions in `urls.py`. Place them before the existing `path("channels/", ...)` entry (works across v1/v2/v3 automatically).

## 4. Migration (`/app/hc/api/migrations/`)

Generate with `python manage.py makemigrations api --name mute_channel`.

## Constraints

- Don't modify existing tests
- Use `isostring()` for datetime formatting (already in the codebase)
- `td` is already imported as `from datetime import timedelta as td` in views.py
- `now` is already imported as `from django.utils.timezone import now` in views.py
- Follow existing patterns for decorators, error responses, etc.
