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
# 1. Add dependencies M2M field to Check model
###############################################################################

python3 << 'PATCH1'
with open("hc/api/models.py", "r") as f:
    content = f.read()

old = '''    status = models.CharField(max_length=6, choices=STATUSES, default="new")

    class Meta:'''

new = '''    status = models.CharField(max_length=6, choices=STATUSES, default="new")
    dependencies = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="dependents",
    )

    class Meta:'''

content = content.replace(old, new, 1)

with open("hc/api/models.py", "w") as f:
    f.write(content)
PATCH1

###############################################################################
# 2. Modify Flip.select_channels() for dependency suppression
###############################################################################

python3 << 'PATCH2'
with open("hc/api/models.py", "r") as f:
    content = f.read()

old = '''        q = self.owner.channel_set.exclude(disabled=True)
        return [ch for ch in q if not ch.transport.is_noop(self.new_status)]'''

new = '''        if self.new_status == "down":
            if self.owner.dependencies.filter(status="down").exists():
                return []

        q = self.owner.channel_set.exclude(disabled=True)
        return [ch for ch in q if not ch.transport.is_noop(self.new_status)]'''

content = content.replace(old, new, 1)

with open("hc/api/models.py", "w") as f:
    f.write(content)
PATCH2

###############################################################################
# 3. Add deps_count to Check.to_dict()
###############################################################################

python3 << 'PATCH3'
with open("hc/api/models.py", "r") as f:
    content = f.read()

old = '''        if self.kind == "simple":
            result["timeout"] = int(self.timeout.total_seconds())
        elif self.kind in ("cron", "oncalendar"):
            result["schedule"] = self.schedule
            result["tz"] = self.tz

        return result'''

new = '''        if self.kind == "simple":
            result["timeout"] = int(self.timeout.total_seconds())
        elif self.kind in ("cron", "oncalendar"):
            result["schedule"] = self.schedule
            result["tz"] = self.tz

        result["deps_count"] = self.dependencies.count()
        return result'''

content = content.replace(old, new, 1)

with open("hc/api/models.py", "w") as f:
    f.write(content)
PATCH3

###############################################################################
# 4. Add check_deps and remove_dep views
###############################################################################

cat >> /app/hc/api/views.py << 'VIEWEOF'


@authorize_read
def list_deps(request: ApiRequest, code: UUID) -> HttpResponse:
    check = get_object_or_404(Check, code=code)
    if check.project_id != request.project.id:
        return HttpResponseForbidden()

    deps = [dep.to_dict(v=request.v) for dep in check.dependencies.all()]
    return JsonResponse({"dependencies": deps})


@authorize
def add_dep(request: ApiRequest, code: UUID) -> HttpResponse:
    check = get_object_or_404(Check, code=code)
    if check.project_id != request.project.id:
        return HttpResponseForbidden()

    dep_str = request.json.get("dep")
    if dep_str is None:
        return JsonResponse({"error": "missing dep"}, status=400)

    if not is_valid_uuid_string(dep_str):
        return JsonResponse({"error": "invalid dep"}, status=400)

    dep_check = Check.objects.filter(code=dep_str).first()
    if dep_check is None:
        return HttpResponseNotFound()

    if dep_check.project_id != request.project.id:
        return JsonResponse({"error": "check not found in project"}, status=400)

    if dep_check.code == check.code:
        return JsonResponse({"error": "cannot depend on itself"}, status=400)

    if check.dependencies.filter(code=dep_check.code).exists():
        deps = [d.to_dict(v=request.v) for d in check.dependencies.all()]
        return JsonResponse({"dependencies": deps}, status=200)

    if check.dependencies.count() >= 10:
        return JsonResponse({"error": "too many dependencies"}, status=400)

    check.dependencies.add(dep_check)
    deps = [d.to_dict(v=request.v) for d in check.dependencies.all()]
    return JsonResponse({"dependencies": deps}, status=201)


@csrf_exempt
@cors("GET", "POST")
def check_deps(request: HttpRequest, code: UUID) -> HttpResponse:
    if request.method == "POST":
        return add_dep(request, code)

    return list_deps(request, code)


@cors("POST")
@csrf_exempt
@authorize
def remove_dep(request: ApiRequest, code: UUID) -> HttpResponse:
    check = get_object_or_404(Check, code=code)
    if check.project_id != request.project.id:
        return HttpResponseForbidden()

    dep_str = request.json.get("dep")
    if dep_str is None:
        return JsonResponse({"error": "missing dep"}, status=400)

    if is_valid_uuid_string(dep_str):
        dep_check = Check.objects.filter(
            code=dep_str, project=request.project
        ).first()
        if dep_check is not None:
            check.dependencies.remove(dep_check)

    deps = [d.to_dict(v=request.v) for d in check.dependencies.all()]
    return JsonResponse({"dependencies": deps})
VIEWEOF

###############################################################################
# 5. Add URL routes
###############################################################################

python3 << 'PATCH4'
with open("hc/api/urls.py", "r") as f:
    content = f.read()

old = '''    path("channels/", views.channels),'''

new = '''    path("checks/<uuid:code>/deps/", views.check_deps, name="hc-api-check-deps"),
    path(
        "checks/<uuid:code>/remove-dep",
        views.remove_dep,
        name="hc-api-remove-dep",
    ),
    path("channels/", views.channels),'''

content = content.replace(old, new, 1)

with open("hc/api/urls.py", "w") as f:
    f.write(content)
PATCH4

###############################################################################
# 6. Create the migration and apply
###############################################################################

python manage.py makemigrations api --name check_dependencies 2>&1
python manage.py migrate 2>&1
