"""Tests for the Check Cloning API feature."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta as td

import os
import sys
sys.path.insert(0, "/app")
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hc.settings")
django.setup()

from django.test import TestCase
from django.utils.timezone import now

from hc.api.models import Channel, Check
from hc.accounts.models import Project
from hc.test import BaseTestCase


class CloneLogModelTestCase(BaseTestCase):
    """Tests for the CloneLog model itself."""

    def setUp(self):
        super().setUp()
        self.source = Check.objects.create(project=self.project, name="Source")
        self.clone = Check.objects.create(project=self.project, name="Clone")

    def test_model_exists(self):
        """CloneLog model should be importable."""
        from hc.api.models import CloneLog
        self.assertTrue(hasattr(CloneLog, "objects"))

    def test_create_clone_log(self):
        """Can create a CloneLog entry with all fields."""
        from hc.api.models import CloneLog
        log = CloneLog.objects.create(source=self.source, clone=self.clone)
        self.assertIsNotNone(log.code)
        self.assertEqual(log.source_id, self.source.id)
        self.assertEqual(log.clone_id, self.clone.id)

    def test_to_dict(self):
        """to_dict() returns correct keys and values."""
        from hc.api.models import CloneLog
        log = CloneLog.objects.create(source=self.source, clone=self.clone)
        d = log.to_dict()
        self.assertEqual(d["uuid"], str(log.code))
        self.assertEqual(d["source"], str(self.source.code))
        self.assertEqual(d["clone"], str(self.clone.code))
        self.assertIn("created", d)

    def test_to_dict_created_no_microseconds(self):
        """created in to_dict() should use isostring (no microseconds)."""
        from hc.api.models import CloneLog
        log = CloneLog.objects.create(source=self.source, clone=self.clone)
        d = log.to_dict()
        self.assertNotIn(".", d["created"],
                         "created should not contain microseconds")

    def test_source_set_null(self):
        """Deleting the source check should set source to None, not delete the log."""
        from hc.api.models import CloneLog
        log = CloneLog.objects.create(source=self.source, clone=self.clone)
        self.source.delete()
        log.refresh_from_db()
        self.assertIsNone(log.source)
        d = log.to_dict()
        self.assertIsNone(d["source"])

    def test_clone_set_null(self):
        """Deleting the clone check should set clone to None, not delete the log."""
        from hc.api.models import CloneLog
        log = CloneLog.objects.create(source=self.source, clone=self.clone)
        self.clone.delete()
        log.refresh_from_db()
        self.assertIsNone(log.clone)
        d = log.to_dict()
        self.assertIsNone(d["clone"])


class CloneFieldsCopiedTestCase(BaseTestCase):
    """Tests that clone() copies all configuration fields."""

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(
            project=self.project,
            name="Original",
            tags="prod web",
            desc="My cron job",
            kind="cron",
            timeout=td(minutes=30),
            grace=td(minutes=10),
            schedule="*/5 * * * *",
            tz="America/New_York",
            filter_subject=True,
            filter_body=True,
            start_kw="START",
            success_kw="OK",
            failure_kw="FAIL",
            methods="POST",
            manual_resume=True,
        )
        self.cloned = self.check.clone(self.project)

    def test_name_copied_with_suffix(self):
        """Clone name should be original name + ' (copy)'."""
        self.assertEqual(self.cloned.name, "Original (copy)")

    def test_tags_copied(self):
        self.assertEqual(self.cloned.tags, "prod web")

    def test_desc_copied(self):
        self.assertEqual(self.cloned.desc, "My cron job")

    def test_kind_copied(self):
        self.assertEqual(self.cloned.kind, "cron")

    def test_timeout_copied(self):
        self.assertEqual(self.cloned.timeout, td(minutes=30))

    def test_grace_copied(self):
        self.assertEqual(self.cloned.grace, td(minutes=10))

    def test_schedule_copied(self):
        self.assertEqual(self.cloned.schedule, "*/5 * * * *")

    def test_tz_copied(self):
        self.assertEqual(self.cloned.tz, "America/New_York")

    def test_filters_copied(self):
        self.assertTrue(self.cloned.filter_subject)
        self.assertTrue(self.cloned.filter_body)

    def test_keywords_copied(self):
        self.assertEqual(self.cloned.start_kw, "START")
        self.assertEqual(self.cloned.success_kw, "OK")
        self.assertEqual(self.cloned.failure_kw, "FAIL")

    def test_methods_copied(self):
        self.assertEqual(self.cloned.methods, "POST")

    def test_manual_resume_copied(self):
        self.assertTrue(self.cloned.manual_resume)


class CloneFieldsResetTestCase(BaseTestCase):
    """Tests that clone() resets all state fields."""

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(
            project=self.project,
            name="Active Check",
            status="up",
            n_pings=50,
            slug="active-check",
            has_confirmation_link=True,
        )
        self.check.last_ping = now()
        self.check.last_start = now()
        self.check.last_start_rid = uuid.uuid4()
        self.check.last_duration = td(seconds=42)
        self.check.alert_after = now() + td(hours=1)
        self.check.badge_key = uuid.uuid4()
        self.check.save()
        self.cloned = self.check.clone(self.project)

    def test_new_uuid(self):
        """Clone should have a different UUID than the source."""
        self.assertNotEqual(self.cloned.code, self.check.code)

    def test_status_is_new(self):
        self.assertEqual(self.cloned.status, "new")

    def test_n_pings_zero(self):
        self.assertEqual(self.cloned.n_pings, 0)

    def test_last_ping_none(self):
        self.assertIsNone(self.cloned.last_ping)

    def test_alert_after_none(self):
        self.assertIsNone(self.cloned.alert_after)

    def test_slug_is_empty(self):
        """Clone slug should be reset to empty string."""
        self.assertEqual(self.cloned.slug, "")

    def test_has_confirmation_link_false(self):
        self.assertFalse(self.cloned.has_confirmation_link)

    def test_badge_key_none(self):
        self.assertIsNone(self.cloned.badge_key)

    def test_last_start_none(self):
        self.assertIsNone(self.cloned.last_start)

    def test_last_start_rid_none(self):
        self.assertIsNone(self.cloned.last_start_rid)

    def test_last_duration_none(self):
        self.assertIsNone(self.cloned.last_duration)

    def test_source_unchanged(self):
        """The original check should not be modified by cloning."""
        self.check.refresh_from_db()
        self.assertEqual(self.check.status, "up")
        self.assertEqual(self.check.n_pings, 50)
        self.assertIsNotNone(self.check.last_ping)
        self.assertEqual(self.check.slug, "active-check")


class CloneIntegrationTestCase(BaseTestCase):
    """Tests for clone() end-to-end behavior."""

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(project=self.project, name="Source")

    def test_clone_log_created(self):
        """clone() should create a CloneLog linking source to clone."""
        from hc.api.models import CloneLog
        cloned = self.check.clone(self.project)
        logs = CloneLog.objects.filter(source=self.check)
        self.assertEqual(logs.count(), 1)
        log = logs.first()
        self.assertEqual(log.clone.code, cloned.code)

    def test_channels_from_target_project(self):
        """Clone should get channels from the target project, not the source."""
        source_channel = Channel.objects.create(
            project=self.project, kind="email", value="alice@example.org"
        )
        self.check.channel_set.add(source_channel)

        target_channel = Channel.objects.create(
            project=self.bobs_project, kind="email", value="bob@example.org"
        )

        cloned = self.check.clone(self.bobs_project)
        assigned = list(cloned.channel_set.all())
        self.assertEqual(len(assigned), 1)
        self.assertEqual(assigned[0].id, target_channel.id)

    def test_clone_in_target_project(self):
        """Cross-project clone should land in the target project."""
        cloned = self.check.clone(self.bobs_project)
        self.assertEqual(cloned.project_id, self.bobs_project.id)


class CloneApiTestCase(BaseTestCase):
    """Tests for POST /api/v3/checks/<code>/clones/"""

    def setUp(self):
        super().setUp()
        self.bobs_project.api_key = "B" * 32
        self.bobs_project.save()
        self.check = Check.objects.create(project=self.project, name="Clone Me")
        self.url = f"/api/v3/checks/{self.check.code}/clones/"

    def post(self, data=None, api_key=None):
        if api_key is None:
            api_key = "X" * 32
        payload = {"api_key": api_key}
        if data:
            payload.update(data)
        return self.client.post(
            self.url,
            json.dumps(payload),
            content_type="application/json",
        )

    def test_clone_returns_201(self):
        """POST should clone the check and return 201."""
        r = self.post()
        self.assertEqual(r.status_code, 201)
        doc = r.json()
        self.assertIn("name", doc)
        self.assertEqual(doc["name"], "Clone Me (copy)")
        self.assertEqual(doc["status"], "new")

    def test_same_project_clone(self):
        """POST without target_api_key clones into the same project."""
        r = self.post()
        self.assertEqual(r.status_code, 201)
        new_code = r.json()["uuid"]
        new_check = Check.objects.get(code=new_code)
        self.assertEqual(new_check.project_id, self.project.id)

    def test_cross_project_clone(self):
        """POST with target_api_key clones into the target project."""
        r = self.post({"target_api_key": "B" * 32})
        self.assertEqual(r.status_code, 201)
        new_code = r.json()["uuid"]
        new_check = Check.objects.get(code=new_code)
        self.assertEqual(new_check.project_id, self.bobs_project.id)

    def test_capacity_exceeded(self):
        """POST should return 403 if target project has no capacity."""
        from hc.accounts.models import Profile
        profile = Profile.objects.for_user(self.bob)
        profile.check_limit = 0
        profile.save()

        r = self.post({"target_api_key": "B" * 32})
        self.assertEqual(r.status_code, 403)
        self.assertIn("no checks available", r.json()["error"])

    def test_invalid_target_api_key(self):
        """POST with unknown target_api_key should return 400."""
        r = self.post({"target_api_key": "Z" * 32})
        self.assertEqual(r.status_code, 400)
        self.assertIn("invalid target_api_key", r.json()["error"])

    def test_target_api_key_not_string(self):
        """POST with non-string target_api_key should return 400."""
        r = self.post({"target_api_key": 123})
        self.assertEqual(r.status_code, 400)

    def test_wrong_api_key(self):
        """POST with wrong API key should return 401."""
        r = self.post(api_key="Y" * 32)
        self.assertEqual(r.status_code, 401)

    def test_wrong_project(self):
        """POST for a check in a different project should return 403."""
        other_check = Check.objects.create(
            project=self.charlies_project, name="Other"
        )
        url = f"/api/v3/checks/{other_check.code}/clones/"
        r = self.client.post(
            url,
            json.dumps({"api_key": "X" * 32}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_nonexistent_check(self):
        """POST for a nonexistent check should return 404."""
        url = f"/api/v3/checks/{uuid.uuid4()}/clones/"
        r = self.client.post(
            url,
            json.dumps({"api_key": "X" * 32}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 404)


class ListClonesApiTestCase(BaseTestCase):
    """Tests for GET /api/v3/checks/<code>/clones/"""

    def setUp(self):
        super().setUp()
        self.project.api_key_readonly = "R" * 32
        self.project.save()
        self.check = Check.objects.create(project=self.project, name="Source")
        self.url = f"/api/v3/checks/{self.check.code}/clones/"

    def test_list_empty(self):
        """GET should return empty list when no clones exist."""
        r = self.client.get(self.url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["clones"], [])
        self.assertEqual(r["Access-Control-Allow-Origin"], "*")

    def test_list_clones(self):
        """GET should return clone log entries."""
        from hc.api.models import CloneLog
        clone_check = Check.objects.create(project=self.project, name="Clone")
        CloneLog.objects.create(source=self.check, clone=clone_check)

        r = self.client.get(self.url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 200)
        clones = r.json()["clones"]
        self.assertEqual(len(clones), 1)
        self.assertEqual(clones[0]["source"], str(self.check.code))
        self.assertEqual(clones[0]["clone"], str(clone_check.code))

    def test_readonly_api_key(self):
        """GET with read-only API key should work."""
        r = self.client.get(self.url, HTTP_X_API_KEY="R" * 32)
        self.assertEqual(r.status_code, 200)

    def test_clone_not_in_own_list(self):
        """A check that was cloned FROM another should have an empty clone list."""
        from hc.api.models import CloneLog
        clone_check = Check.objects.create(project=self.project, name="Clone")
        CloneLog.objects.create(source=self.check, clone=clone_check)

        url = f"/api/v3/checks/{clone_check.code}/clones/"
        r = self.client.get(url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["clones"], [])

    def test_wrong_project(self):
        """GET for a check in a different project should return 403."""
        other_check = Check.objects.create(
            project=self.bobs_project, name="Bob's Check"
        )
        url = f"/api/v3/checks/{other_check.code}/clones/"
        r = self.client.get(url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 403)

    def test_nonexistent_check(self):
        """GET for a nonexistent check should return 404."""
        url = f"/api/v3/checks/{uuid.uuid4()}/clones/"
        r = self.client.get(url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 404)


class CheckToDictClonesTestCase(BaseTestCase):
    """Tests for clones_count in Check.to_dict()."""

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(project=self.project, name="Test Check")

    def test_clones_count_zero(self):
        """to_dict() should include clones_count=0 when no clones."""
        d = self.check.to_dict()
        self.assertIn("clones_count", d)
        self.assertEqual(d["clones_count"], 0)

    def test_clones_count_reflects_actual(self):
        """to_dict() should include correct clones_count."""
        from hc.api.models import CloneLog
        c1 = Check.objects.create(project=self.project, name="Clone 1")
        c2 = Check.objects.create(project=self.project, name="Clone 2")
        CloneLog.objects.create(source=self.check, clone=c1)
        CloneLog.objects.create(source=self.check, clone=c2)
        d = self.check.to_dict()
        self.assertEqual(d["clones_count"], 2)

    def test_clones_count_in_checks_api(self):
        """GET /api/v3/checks/ should include clones_count in each check."""
        r = self.client.get("/api/v3/checks/", HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        check_data = doc["checks"][0]
        self.assertIn("clones_count", check_data)


class CloneUrlRoutingTestCase(BaseTestCase):
    """Tests that URL routing works for all API versions."""

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(project=self.project, name="Test Check")

    def test_v1_endpoint(self):
        """The clones endpoint should work under /api/v1/."""
        url = f"/api/v1/checks/{self.check.code}/clones/"
        r = self.client.get(url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 200)

    def test_v2_endpoint(self):
        """The clones endpoint should work under /api/v2/."""
        url = f"/api/v2/checks/{self.check.code}/clones/"
        r = self.client.get(url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 200)

    def test_v3_endpoint(self):
        """The clones endpoint should work under /api/v3/."""
        url = f"/api/v3/checks/{self.check.code}/clones/"
        r = self.client.get(url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 200)

    def test_options_request(self):
        """OPTIONS should return 204 with CORS headers."""
        url = f"/api/v3/checks/{self.check.code}/clones/"
        r = self.client.options(url)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r["Access-Control-Allow-Origin"], "*")
