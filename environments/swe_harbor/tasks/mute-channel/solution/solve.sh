#!/bin/bash
set -e
cd /app

###############################################################################
# 0. Create stub hc/logs module (referenced in settings.py but missing)
###############################################################################

mkdir -p /app/hc/logs
cat > /app/hc/logs/__init__.py << 'STUBEOF'
import logging

class Handler(logging.Handler):
    def emit(self, record):
        pass
STUBEOF

###############################################################################
# 1. Add muted_until field to Channel model
###############################################################################

python3 << 'PATCH1'
with open("hc/api/models.py", "r") as f:
    content = f.read()

old = '''    disabled = models.BooleanField(default=False)
    last_notify = models.DateTimeField(null=True, blank=True)'''

new = '''    disabled = models.BooleanField(default=False)
    muted_until = models.DateTimeField(null=True, blank=True)
    last_notify = models.DateTimeField(null=True, blank=True)'''

content = content.replace(old, new, 1)

with open("hc/api/models.py", "w") as f:
    f.write(content)
PATCH1

###############################################################################
# 2. Modify Flip.select_channels() to exclude muted channels
###############################################################################

python3 << 'PATCH2'
with open("hc/api/models.py", "r") as f:
    content = f.read()

old = '''        q = self.owner.channel_set.exclude(disabled=True)
        return [ch for ch in q if not ch.transport.is_noop(self.new_status)]'''

new = '''        q = self.owner.channel_set.exclude(disabled=True).exclude(
            muted_until__gt=now()
        )
        return [ch for ch in q if not ch.transport.is_noop(self.new_status)]'''

content = content.replace(old, new, 1)

with open("hc/api/models.py", "w") as f:
    f.write(content)
PATCH2

###############################################################################
# 3. Expand Channel.to_dict() to include muted_until
###############################################################################

python3 << 'PATCH3'
with open("hc/api/models.py", "r") as f:
    content = f.read()

old = '''    def to_dict(self) -> dict[str, str]:
        return {"id": str(self.code), "name": self.name, "kind": self.kind}'''

new = '''    def to_dict(self) -> dict[str, str]:
        return {
            "id": str(self.code),
            "name": self.name,
            "kind": self.kind,
            "muted_until": isostring(self.muted_until),
        }'''

content = content.replace(old, new, 1)

with open("hc/api/models.py", "w") as f:
    f.write(content)
PATCH3

###############################################################################
# 4. Add mute/unmute API views
###############################################################################

cat >> /app/hc/api/views.py << 'VIEWEOF'


@cors("POST")
@csrf_exempt
@authorize
def mute_channel(request: ApiRequest, code: UUID) -> HttpResponse:
    channel = get_object_or_404(Channel, code=code)
    if channel.project_id != request.project.id:
        return HttpResponseForbidden()

    duration = request.json.get("duration")
    if duration is None:
        return JsonResponse({"error": "missing duration"}, status=400)

    if not isinstance(duration, int):
        return JsonResponse({"error": "invalid duration"}, status=400)

    if duration < 1 or duration > 31536000:
        return JsonResponse({"error": "duration out of range"}, status=400)

    channel.muted_until = now() + td(seconds=duration)
    channel.save()

    return JsonResponse(channel.to_dict())


@cors("POST")
@csrf_exempt
@authorize
def unmute_channel(request: ApiRequest, code: UUID) -> HttpResponse:
    channel = get_object_or_404(Channel, code=code)
    if channel.project_id != request.project.id:
        return HttpResponseForbidden()

    channel.muted_until = None
    channel.save()

    return JsonResponse(channel.to_dict())
VIEWEOF

###############################################################################
# 5. Add URL routes
###############################################################################

python3 << 'PATCH4'
with open("hc/api/urls.py", "r") as f:
    content = f.read()

old = '''    path("channels/", views.channels),'''

new = '''    path(
        "channels/<uuid:code>/mute",
        views.mute_channel,
        name="hc-api-mute-channel",
    ),
    path(
        "channels/<uuid:code>/unmute",
        views.unmute_channel,
        name="hc-api-unmute-channel",
    ),
    path("channels/", views.channels),'''

content = content.replace(old, new, 1)

with open("hc/api/urls.py", "w") as f:
    f.write(content)
PATCH4

###############################################################################
# 6. Create the migration and apply
###############################################################################

python manage.py makemigrations api --name mute_channel 2>&1
python manage.py migrate 2>&1
