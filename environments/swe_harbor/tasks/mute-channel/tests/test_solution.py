"""Tests for the Channel Muting feature."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from datetime import timedelta as td
from unittest.mock import PropertyMock, patch

import os
import sys
sys.path.insert(0, "/app")
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hc.settings")
django.setup()

from django.test import TestCase
from django.utils.timezone import now

from hc.api.models import Channel, Check, Flip
from hc.test import BaseTestCase


class ChannelModelTestCase(BaseTestCase):
    """Tests for the muted_until field and to_dict() changes."""

    def setUp(self):
        super().setUp()
        self.channel = Channel.objects.create(
            project=self.project, kind="email", value="alice@example.org"
        )

    def test_muted_until_defaults_null(self):
        """New channel should have muted_until=None."""
        self.assertIsNone(self.channel.muted_until)

    def test_can_set_muted_until(self):
        """Can set muted_until to a future datetime."""
        future = now() + td(hours=1)
        self.channel.muted_until = future
        self.channel.save()
        self.channel.refresh_from_db()
        self.assertIsNotNone(self.channel.muted_until)

    def test_to_dict_unmuted(self):
        """to_dict() should return muted_until=None when not muted."""
        d = self.channel.to_dict()
        self.assertIn("muted_until", d)
        self.assertIsNone(d["muted_until"])

    def test_to_dict_muted(self):
        """to_dict() should return ISO string when muted, no microseconds."""
        self.channel.muted_until = now() + td(hours=1)
        self.channel.save()
        d = self.channel.to_dict()
        self.assertIsNotNone(d["muted_until"])
        self.assertIsInstance(d["muted_until"], str)
        self.assertNotIn(".", d["muted_until"],
                         "muted_until should not contain microseconds")


class SelectChannelsTestCase(BaseTestCase):
    """Tests for Flip.select_channels() muting integration."""

    def setUp(self):
        super().setUp()
        self.check = Check.objects.create(project=self.project, name="Test")
        self.flip = Flip.objects.create(
            owner=self.check,
            created=now(),
            old_status="up",
            new_status="down",
        )

    def _make_channel(self, muted_until=None, disabled=False):
        ch = Channel.objects.create(
            project=self.project,
            kind="email",
            value="test@example.org",
            disabled=disabled,
        )
        if muted_until is not None:
            ch.muted_until = muted_until
            ch.save()
        ch.checks.add(self.check)
        return ch

    def test_unmuted_channel_included(self):
        """A channel with muted_until=None should be included."""
        ch = self._make_channel()
        with patch.object(Channel, "transport", new_callable=PropertyMock) as mt:
            mt.return_value.is_noop.return_value = False
            channels = self.flip.select_channels()
        self.assertIn(ch, channels)

    def test_muted_channel_excluded(self):
        """A channel with muted_until in the future should be excluded."""
        ch = self._make_channel(muted_until=now() + td(hours=1))
        with patch.object(Channel, "transport", new_callable=PropertyMock) as mt:
            mt.return_value.is_noop.return_value = False
            channels = self.flip.select_channels()
        self.assertNotIn(ch, channels)

    def test_expired_mute_included(self):
        """A channel with muted_until in the past should be included (mute expired)."""
        ch = self._make_channel(muted_until=now() - td(hours=1))
        with patch.object(Channel, "transport", new_callable=PropertyMock) as mt:
            mt.return_value.is_noop.return_value = False
            channels = self.flip.select_channels()
        self.assertIn(ch, channels)

    def test_disabled_still_excluded(self):
        """A disabled channel should still be excluded regardless of mute status."""
        ch = self._make_channel(disabled=True)
        with patch.object(Channel, "transport", new_callable=PropertyMock) as mt:
            mt.return_value.is_noop.return_value = False
            channels = self.flip.select_channels()
        self.assertNotIn(ch, channels)

    def test_mixed_channels(self):
        """Only unmuted channels should be returned when mixed."""
        muted = self._make_channel(muted_until=now() + td(hours=1))
        unmuted = self._make_channel()
        with patch.object(Channel, "transport", new_callable=PropertyMock) as mt:
            mt.return_value.is_noop.return_value = False
            channels = self.flip.select_channels()
        self.assertNotIn(muted, channels)
        self.assertIn(unmuted, channels)


class MuteChannelApiTestCase(BaseTestCase):
    """Tests for POST /api/v3/channels/<code>/mute"""

    def setUp(self):
        super().setUp()
        self.channel = Channel.objects.create(
            project=self.project, kind="email", value="alice@example.org"
        )
        self.url = f"/api/v3/channels/{self.channel.code}/mute"

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

    def test_mute_success(self):
        """POST should mute the channel and return 200."""
        r = self.post({"duration": 3600})
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertIsNotNone(doc["muted_until"])
        self.assertEqual(r["Access-Control-Allow-Origin"], "*")

    def test_muted_until_in_future(self):
        """muted_until in response should be in the future."""
        r = self.post({"duration": 3600})
        doc = r.json()
        muted_until = datetime.fromisoformat(doc["muted_until"])
        self.assertGreater(muted_until, now())

    def test_overwrite_existing_mute(self):
        """Muting an already-muted channel should overwrite the value."""
        self.post({"duration": 3600})
        r = self.post({"duration": 7200})
        self.assertEqual(r.status_code, 200)
        self.channel.refresh_from_db()
        diff = self.channel.muted_until - now()
        self.assertGreater(diff.total_seconds(), 3600)

    def test_missing_duration(self):
        """POST without duration should return 400."""
        r = self.post({})
        self.assertEqual(r.status_code, 400)
        self.assertIn("missing duration", r.json()["error"])

    def test_invalid_duration_type(self):
        """POST with non-integer duration should return 400."""
        r = self.post({"duration": "sixty"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("invalid duration", r.json()["error"])

    def test_duration_too_small(self):
        """POST with duration < 1 should return 400."""
        r = self.post({"duration": 0})
        self.assertEqual(r.status_code, 400)
        self.assertIn("duration out of range", r.json()["error"])

    def test_duration_too_large(self):
        """POST with duration > 31536000 should return 400."""
        r = self.post({"duration": 31536001})
        self.assertEqual(r.status_code, 400)
        self.assertIn("duration out of range", r.json()["error"])

    def test_wrong_project(self):
        """POST for a channel in a different project should return 403."""
        other_channel = Channel.objects.create(
            project=self.bobs_project, kind="email", value="bob@example.org"
        )
        url = f"/api/v3/channels/{other_channel.code}/mute"
        r = self.client.post(
            url,
            json.dumps({"api_key": "X" * 32, "duration": 3600}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_nonexistent_channel(self):
        """POST for a nonexistent channel should return 404."""
        url = f"/api/v3/channels/{uuid.uuid4()}/mute"
        r = self.client.post(
            url,
            json.dumps({"api_key": "X" * 32, "duration": 3600}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 404)

    def test_duration_min_boundary(self):
        """duration=1 (minimum valid) should succeed."""
        r = self.post({"duration": 1})
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.json()["muted_until"])

    def test_duration_max_boundary(self):
        """duration=31536000 (maximum valid, 1 year) should succeed."""
        r = self.post({"duration": 31536000})
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.json()["muted_until"])

    def test_wrong_api_key(self):
        """POST with an incorrect API key should return 401."""
        r = self.post({"duration": 3600}, api_key="Y" * 32)
        self.assertEqual(r.status_code, 401)

    def test_float_duration(self):
        """POST with a float duration should return 400 invalid duration."""
        r = self.post({"duration": 1.5})
        self.assertEqual(r.status_code, 400)
        self.assertIn("invalid duration", r.json()["error"])


class UnmuteChannelApiTestCase(BaseTestCase):
    """Tests for POST /api/v3/channels/<code>/unmute"""

    def setUp(self):
        super().setUp()
        self.channel = Channel.objects.create(
            project=self.project, kind="email", value="alice@example.org"
        )
        self.channel.muted_until = now() + td(hours=1)
        self.channel.save()
        self.url = f"/api/v3/channels/{self.channel.code}/unmute"

    def post(self, api_key=None):
        if api_key is None:
            api_key = "X" * 32
        return self.client.post(
            self.url,
            json.dumps({"api_key": api_key}),
            content_type="application/json",
        )

    def test_unmute_success(self):
        """POST should unmute the channel and return 200."""
        r = self.post()
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertIsNone(doc["muted_until"])
        self.channel.refresh_from_db()
        self.assertIsNone(self.channel.muted_until)

    def test_unmute_already_unmuted(self):
        """POST on an already-unmuted channel should still return 200."""
        self.channel.muted_until = None
        self.channel.save()
        r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["muted_until"])

    def test_wrong_project(self):
        """POST for a channel in a different project should return 403."""
        other_channel = Channel.objects.create(
            project=self.bobs_project, kind="email", value="bob@example.org"
        )
        url = f"/api/v3/channels/{other_channel.code}/unmute"
        r = self.client.post(
            url,
            json.dumps({"api_key": "X" * 32}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_nonexistent_channel(self):
        """POST for a nonexistent channel should return 404."""
        url = f"/api/v3/channels/{uuid.uuid4()}/unmute"
        r = self.client.post(
            url,
            json.dumps({"api_key": "X" * 32}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 404)

    def test_cors_header(self):
        """Response should include CORS headers."""
        r = self.post()
        self.assertEqual(r["Access-Control-Allow-Origin"], "*")

    def test_wrong_api_key(self):
        """POST with an incorrect API key should return 401."""
        r = self.post(api_key="Y" * 32)
        self.assertEqual(r.status_code, 401)


class ChannelMuteUrlRoutingTestCase(BaseTestCase):
    """Tests that URL routing works for all API versions."""

    def setUp(self):
        super().setUp()
        self.channel = Channel.objects.create(
            project=self.project, kind="email", value="alice@example.org"
        )

    def post_mute(self, version):
        url = f"/api/v{version}/channels/{self.channel.code}/mute"
        return self.client.post(
            url,
            json.dumps({"api_key": "X" * 32, "duration": 60}),
            content_type="application/json",
        )

    def test_v1_mute(self):
        self.assertEqual(self.post_mute(1).status_code, 200)

    def test_v2_mute(self):
        self.assertEqual(self.post_mute(2).status_code, 200)

    def test_v3_mute(self):
        self.assertEqual(self.post_mute(3).status_code, 200)

    def test_v3_unmute(self):
        """Unmute endpoint should work under /api/v3/."""
        url = f"/api/v3/channels/{self.channel.code}/unmute"
        r = self.client.post(
            url,
            json.dumps({"api_key": "X" * 32}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)

    def test_get_method_not_allowed(self):
        """GET on the mute endpoint should return 405."""
        url = f"/api/v3/channels/{self.channel.code}/mute"
        r = self.client.get(url)
        self.assertEqual(r.status_code, 405)

    def test_options_request(self):
        """OPTIONS should return 204 with CORS headers."""
        url = f"/api/v3/channels/{self.channel.code}/mute"
        r = self.client.options(url)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r["Access-Control-Allow-Origin"], "*")
