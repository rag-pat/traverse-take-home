"""Tests for the Check Dependencies feature."""
from __future__ import annotations

import json
import uuid
from unittest.mock import PropertyMock, patch

import os
import sys
sys.path.insert(0, "/app")
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hc.settings")
django.setup()

from django.utils.timezone import now

from hc.api.models import Channel, Check, Flip
from hc.test import BaseTestCase


class CheckDependencyModelTestCase(BaseTestCase):
    """Tests for the dependencies M2M field and deps_count in to_dict()."""

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(project=self.project, name="Web")
        self.other = Check.objects.create(project=self.project, name="DB")

    def test_deps_count_default_zero(self):
        """Fresh check should have deps_count=0 in to_dict()."""
        d = self.check.to_dict()
        self.assertEqual(d["deps_count"], 0)

    def test_add_dependency(self):
        """Adding a dependency should increase count to 1."""
        self.check.dependencies.add(self.other)
        self.assertEqual(self.check.dependencies.count(), 1)

    def test_remove_dependency(self):
        """Removing a dependency should bring count back to 0."""
        self.check.dependencies.add(self.other)
        self.check.dependencies.remove(self.other)
        self.assertEqual(self.check.dependencies.count(), 0)

    def test_multiple_dependencies(self):
        """deps_count should reflect multiple deps."""
        for i in range(3):
            dep = Check.objects.create(project=self.project, name=f"Dep {i}")
            self.check.dependencies.add(dep)
        self.assertEqual(self.check.to_dict()["deps_count"], 3)

    def test_asymmetric(self):
        """Adding B as dep of A should NOT make A a dep of B."""
        self.check.dependencies.add(self.other)
        self.assertEqual(self.check.dependencies.count(), 1)
        self.assertEqual(self.other.dependencies.count(), 0)

    def test_cascade_on_delete(self):
        """Deleting a dep check should remove it from dependencies."""
        self.check.dependencies.add(self.other)
        self.other.delete()
        self.assertEqual(self.check.dependencies.count(), 0)

    def test_deps_count_in_checks_api(self):
        """deps_count should appear in GET /api/v3/checks/ list response."""
        self.check.dependencies.add(self.other)
        r = self.client.get(
            "/api/v3/checks/",
            HTTP_X_API_KEY="X" * 32,
        )
        self.assertEqual(r.status_code, 200)
        checks = r.json()["checks"]
        main = [c for c in checks if c["name"] == "Web"][0]
        self.assertEqual(main["deps_count"], 1)


class SelectChannelsSuppressionTestCase(BaseTestCase):
    """Tests for Flip.select_channels() dependency suppression."""

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(project=self.project, name="Web")
        self.dep = Check.objects.create(
            project=self.project, name="DB", status="up"
        )
        self.channel = Channel.objects.create(
            project=self.project, kind="email", value="test@example.org"
        )
        self.channel.checks.add(self.check)

    def _make_flip(self, old_status="up", new_status="down"):
        return Flip.objects.create(
            owner=self.check,
            created=now(),
            old_status=old_status,
            new_status=new_status,
        )

    def test_no_deps_normal(self):
        """Check with no deps should fire normally on down."""
        flip = self._make_flip()
        with patch.object(Channel, "transport", new_callable=PropertyMock) as mt:
            mt.return_value.is_noop.return_value = False
            channels = flip.select_channels()
        self.assertIn(self.channel, channels)

    def test_dep_down_suppressed(self):
        """If any dep is down, alerts should be suppressed."""
        self.check.dependencies.add(self.dep)
        self.dep.status = "down"
        self.dep.save()
        flip = self._make_flip()
        with patch.object(Channel, "transport", new_callable=PropertyMock) as mt:
            mt.return_value.is_noop.return_value = False
            channels = flip.select_channels()
        self.assertEqual(channels, [])

    def test_dep_up_fires(self):
        """If dep is up, alerts should fire normally."""
        self.check.dependencies.add(self.dep)
        self.dep.status = "up"
        self.dep.save()
        flip = self._make_flip()
        with patch.object(Channel, "transport", new_callable=PropertyMock) as mt:
            mt.return_value.is_noop.return_value = False
            channels = flip.select_channels()
        self.assertIn(self.channel, channels)

    def test_dep_paused_fires(self):
        """If dep is paused, alerts should fire (paused != down)."""
        self.check.dependencies.add(self.dep)
        self.dep.status = "paused"
        self.dep.save()
        flip = self._make_flip()
        with patch.object(Channel, "transport", new_callable=PropertyMock) as mt:
            mt.return_value.is_noop.return_value = False
            channels = flip.select_channels()
        self.assertIn(self.channel, channels)

    def test_multiple_deps_any_down(self):
        """If any of multiple deps is down, suppress."""
        dep2 = Check.objects.create(
            project=self.project, name="Cache", status="up"
        )
        self.check.dependencies.add(self.dep)
        self.check.dependencies.add(dep2)
        self.dep.status = "down"
        self.dep.save()
        flip = self._make_flip()
        with patch.object(Channel, "transport", new_callable=PropertyMock) as mt:
            mt.return_value.is_noop.return_value = False
            channels = flip.select_channels()
        self.assertEqual(channels, [])

    def test_multiple_deps_all_up(self):
        """Multiple deps all up should fire normally."""
        dep2 = Check.objects.create(
            project=self.project, name="Cache", status="up"
        )
        self.check.dependencies.add(self.dep)
        self.check.dependencies.add(dep2)
        self.dep.status = "up"
        self.dep.save()
        flip = self._make_flip()
        with patch.object(Channel, "transport", new_callable=PropertyMock) as mt:
            mt.return_value.is_noop.return_value = False
            channels = flip.select_channels()
        self.assertIn(self.channel, channels)

    def test_suppression_only_on_down(self):
        """Suppression should NOT apply when transitioning to up."""
        self.check.dependencies.add(self.dep)
        self.dep.status = "down"
        self.dep.save()
        flip = self._make_flip(old_status="down", new_status="up")
        with patch.object(Channel, "transport", new_callable=PropertyMock) as mt:
            mt.return_value.is_noop.return_value = False
            channels = flip.select_channels()
        self.assertIn(self.channel, channels)


class ListDepsApiTestCase(BaseTestCase):
    """Tests for GET /api/v3/checks/<code>/deps/"""

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(project=self.project, name="Web")
        self.url = f"/api/v3/checks/{self.check.code}/deps/"

    def get(self, api_key=None):
        if api_key is None:
            api_key = "X" * 32
        return self.client.get(
            self.url,
            HTTP_X_API_KEY=api_key,
        )

    def test_list_empty(self):
        """GET should return empty dependencies list with CORS header."""
        r = self.get()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["dependencies"], [])
        self.assertEqual(r["Access-Control-Allow-Origin"], "*")

    def test_list_with_deps(self):
        """GET should return full to_dict() for each dependency."""
        dep = Check.objects.create(
            project=self.project, name="DB"
        )
        self.check.dependencies.add(dep)
        r = self.get()
        self.assertEqual(r.status_code, 200)
        deps = r.json()["dependencies"]
        self.assertEqual(len(deps), 1)
        item = deps[0]
        self.assertIn("name", item)
        self.assertIn("status", item)
        self.assertIn("deps_count", item)
        self.assertEqual(item["name"], "DB")

    def test_wrong_project(self):
        """GET for a check in a different project should return 403."""
        other = Check.objects.create(project=self.bobs_project, name="X")
        url = f"/api/v3/checks/{other.code}/deps/"
        r = self.client.get(url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 403)

    def test_nonexistent(self):
        """GET for a nonexistent check should return 404."""
        url = f"/api/v3/checks/{uuid.uuid4()}/deps/"
        r = self.client.get(url, HTTP_X_API_KEY="X" * 32)
        self.assertEqual(r.status_code, 404)

    def test_wrong_api_key(self):
        """GET with an incorrect API key should return 401."""
        r = self.get(api_key="Y" * 32)
        self.assertEqual(r.status_code, 401)


class AddDepApiTestCase(BaseTestCase):
    """Tests for POST /api/v3/checks/<code>/deps/"""

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(project=self.project, name="Web")
        self.dep = Check.objects.create(project=self.project, name="DB")
        self.url = f"/api/v3/checks/{self.check.code}/deps/"

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

    def test_add_success(self):
        """POST should add dependency and return 201."""
        r = self.post({"dep": str(self.dep.code)})
        self.assertEqual(r.status_code, 201)
        deps = r.json()["dependencies"]
        self.assertEqual(len(deps), 1)

    def test_missing_dep(self):
        """POST without dep field should return 400."""
        r = self.post({})
        self.assertEqual(r.status_code, 400)
        self.assertIn("missing dep", r.json()["error"])

    def test_invalid_dep(self):
        """POST with non-UUID dep should return 400."""
        r = self.post({"dep": "not-a-uuid"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("invalid dep", r.json()["error"])

    def test_dep_not_found(self):
        """POST with nonexistent UUID should return 404."""
        r = self.post({"dep": str(uuid.uuid4())})
        self.assertEqual(r.status_code, 404)

    def test_cross_project(self):
        """POST with dep in different project should return 400."""
        other = Check.objects.create(project=self.bobs_project, name="X")
        r = self.post({"dep": str(other.code)})
        self.assertEqual(r.status_code, 400)
        self.assertIn("check not found in project", r.json()["error"])

    def test_self_dep(self):
        """POST adding check as its own dep should return 400."""
        r = self.post({"dep": str(self.check.code)})
        self.assertEqual(r.status_code, 400)
        self.assertIn("cannot depend on itself", r.json()["error"])

    def test_duplicate_idempotent(self):
        """POST adding existing dep should return 200, not 201."""
        self.check.dependencies.add(self.dep)
        r = self.post({"dep": str(self.dep.code)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.check.dependencies.count(), 1)

    def test_too_many(self):
        """POST when check already has 10 deps should return 400."""
        for i in range(10):
            d = Check.objects.create(project=self.project, name=f"Dep {i}")
            self.check.dependencies.add(d)
        new_dep = Check.objects.create(project=self.project, name="Dep 10")
        r = self.post({"dep": str(new_dep.code)})
        self.assertEqual(r.status_code, 400)
        self.assertIn("too many dependencies", r.json()["error"])

    def test_wrong_project_check(self):
        """POST for a check in a different project should return 403."""
        other = Check.objects.create(project=self.bobs_project, name="X")
        url = f"/api/v3/checks/{other.code}/deps/"
        r = self.client.post(
            url,
            json.dumps({"api_key": "X" * 32, "dep": str(self.dep.code)}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_wrong_api_key(self):
        """POST with an incorrect API key should return 401."""
        r = self.post({"dep": str(self.dep.code)}, api_key="Y" * 32)
        self.assertEqual(r.status_code, 401)


class RemoveDepApiTestCase(BaseTestCase):
    """Tests for POST /api/v3/checks/<code>/remove-dep"""

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(project=self.project, name="Web")
        self.dep = Check.objects.create(project=self.project, name="DB")
        self.check.dependencies.add(self.dep)
        self.url = f"/api/v3/checks/{self.check.code}/remove-dep"

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

    def test_remove_success(self):
        """POST should remove the dependency and return 200."""
        r = self.post({"dep": str(self.dep.code)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["dependencies"]), 0)
        self.assertEqual(self.check.dependencies.count(), 0)

    def test_non_dep_idempotent(self):
        """POST removing a check that's not a dep should return 200."""
        other = Check.objects.create(project=self.project, name="Cache")
        r = self.post({"dep": str(other.code)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.check.dependencies.count(), 1)

    def test_missing_dep(self):
        """POST without dep field should return 400."""
        r = self.post({})
        self.assertEqual(r.status_code, 400)
        self.assertIn("missing dep", r.json()["error"])

    def test_invalid_uuid_silent(self):
        """POST with invalid UUID should silently return 200."""
        r = self.post({"dep": "garbage"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.check.dependencies.count(), 1)

    def test_wrong_project(self):
        """POST for a check in a different project should return 403."""
        other = Check.objects.create(project=self.bobs_project, name="X")
        url = f"/api/v3/checks/{other.code}/remove-dep"
        r = self.client.post(
            url,
            json.dumps({"api_key": "X" * 32, "dep": str(self.dep.code)}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_wrong_api_key(self):
        """POST with an incorrect API key should return 401."""
        r = self.post({"dep": str(self.dep.code)}, api_key="Y" * 32)
        self.assertEqual(r.status_code, 401)


class DepUrlRoutingTestCase(BaseTestCase):
    """Tests that URL routing works for all API versions."""

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(project=self.project, name="Web")

    def get_deps(self, version):
        url = f"/api/v{version}/checks/{self.check.code}/deps/"
        return self.client.get(url, HTTP_X_API_KEY="X" * 32)

    def test_v1_list(self):
        self.assertEqual(self.get_deps(1).status_code, 200)

    def test_v2_list(self):
        self.assertEqual(self.get_deps(2).status_code, 200)

    def test_v3_list(self):
        self.assertEqual(self.get_deps(3).status_code, 200)

    def test_v3_remove(self):
        """Remove endpoint should work under /api/v3/."""
        url = f"/api/v3/checks/{self.check.code}/remove-dep"
        r = self.client.post(
            url,
            json.dumps({"api_key": "X" * 32, "dep": str(uuid.uuid4())}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)

    def test_options_request(self):
        """OPTIONS should return 204 with CORS headers."""
        url = f"/api/v3/checks/{self.check.code}/deps/"
        r = self.client.options(url)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r["Access-Control-Allow-Origin"], "*")
