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
# 1. Add the CloneLog model to hc/api/models.py
###############################################################################

cat >> /app/hc/api/models.py << 'PYEOF'


class CloneLog(models.Model):
    code = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    source = models.ForeignKey(
        Check, models.SET_NULL, null=True, related_name="clone_logs"
    )
    clone = models.ForeignKey(
        Check, models.SET_NULL, null=True, related_name="+"
    )
    created = models.DateTimeField(default=now)

    class Meta:
        ordering = ["-created"]

    def to_dict(self) -> dict:
        return {
            "uuid": str(self.code),
            "source": str(self.source.code) if self.source else None,
            "clone": str(self.clone.code) if self.clone else None,
            "created": isostring(self.created),
        }
PYEOF

###############################################################################
# 2. Add Check.clone() method (insert after assign_all_channels)
###############################################################################

python3 << 'PATCH1'
with open("hc/api/models.py", "r") as f:
    content = f.read()

old = '''    def assign_all_channels(self) -> None:
        channels = Channel.objects.filter(project=self.project)
        self.channel_set.set(channels)'''

new = '''    def assign_all_channels(self) -> None:
        channels = Channel.objects.filter(project=self.project)
        self.channel_set.set(channels)

    def clone(self, target_project):
        """Clone this check into the target project.

        Copies configuration fields, resets state, assigns channels from the
        target project, and creates a CloneLog entry.
        """
        new_check = Check.objects.create(
            project=target_project,
            name=self.name + " (copy)",
            tags=self.tags,
            desc=self.desc,
            kind=self.kind,
            timeout=self.timeout,
            grace=self.grace,
            schedule=self.schedule,
            tz=self.tz,
            filter_subject=self.filter_subject,
            filter_body=self.filter_body,
            start_kw=self.start_kw,
            success_kw=self.success_kw,
            failure_kw=self.failure_kw,
            methods=self.methods,
            manual_resume=self.manual_resume,
            slug="",
        )
        new_check.assign_all_channels()

        CloneLog.objects.create(source=self, clone=new_check)

        return new_check'''

content = content.replace(old, new, 1)

with open("hc/api/models.py", "w") as f:
    f.write(content)
PATCH1

###############################################################################
# 3. Add clones_count to Check.to_dict()
###############################################################################

python3 << 'PATCH2'
with open("hc/api/models.py", "r") as f:
    content = f.read()

old = '''        if self.kind == "simple":
            result["timeout"] = int(self.timeout.total_seconds())
        elif self.kind in ("cron", "oncalendar"):
            result["schedule"] = self.schedule
            result["tz"] = self.tz

        return result'''

new = '''        result["clones_count"] = self.clone_logs.count()

        if self.kind == "simple":
            result["timeout"] = int(self.timeout.total_seconds())
        elif self.kind in ("cron", "oncalendar"):
            result["schedule"] = self.schedule
            result["tz"] = self.tz

        return result'''

content = content.replace(old, new, 1)

with open("hc/api/models.py", "w") as f:
    f.write(content)
PATCH2

###############################################################################
# 4. Add API views for cloning
###############################################################################

cat >> /app/hc/api/views.py << 'VIEWEOF'


@authorize_read
def list_clones(request: ApiRequest, code: UUID) -> HttpResponse:
    check = get_object_or_404(Check, code=code)
    if check.project_id != request.project.id:
        return HttpResponseForbidden()

    from hc.api.models import CloneLog

    logs = CloneLog.objects.filter(source=check)
    return JsonResponse({"clones": [log.to_dict() for log in logs]})


@authorize
def clone_check(request: ApiRequest, code: UUID) -> HttpResponse:
    check = get_object_or_404(Check, code=code)
    if check.project_id != request.project.id:
        return HttpResponseForbidden()

    target_project = request.project

    target_api_key = request.json.get("target_api_key")
    if target_api_key is not None:
        if not isinstance(target_api_key, str):
            return JsonResponse({"error": "target_api_key is not a string"}, status=400)

        try:
            target_project = Project.objects.get(api_key=target_api_key)
        except Project.DoesNotExist:
            return JsonResponse({"error": "invalid target_api_key"}, status=400)

    if target_project.num_checks_available() <= 0:
        return JsonResponse(
            {"error": "target project has no checks available"}, status=403
        )

    new_check = check.clone(target_project)
    return JsonResponse(new_check.to_dict(v=request.v), status=201)


@csrf_exempt
@cors("GET", "POST")
def check_clones(request: HttpRequest, code: UUID) -> HttpResponse:
    if request.method == "POST":
        return clone_check(request, code)

    return list_clones(request, code)
VIEWEOF

###############################################################################
# 5. Add URL routes
###############################################################################

python3 << 'PATCH3'
with open("hc/api/urls.py", "r") as f:
    content = f.read()

old = '''    path("channels/", views.channels),'''

new = '''    path(
        "checks/<uuid:code>/clones/",
        views.check_clones,
        name="hc-api-clones",
    ),
    path("channels/", views.channels),'''

content = content.replace(old, new, 1)

with open("hc/api/urls.py", "w") as f:
    f.write(content)
PATCH3

###############################################################################
# 6. Create the migration and apply
###############################################################################

python manage.py makemigrations api --name clonelog 2>&1
python manage.py migrate 2>&1
