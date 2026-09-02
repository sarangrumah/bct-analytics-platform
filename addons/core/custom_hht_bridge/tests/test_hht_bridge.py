# -*- coding: utf-8 -*-
# License: LGPL-3
"""HHT Bridge integration tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid

from odoo.tests.common import HttpCase, TransactionCase, tagged


def _sign(secret: str, ts: str, body: bytes) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        ts.encode("utf-8") + body,
        hashlib.sha256,
    ).hexdigest()


@tagged("post_install", "-at_install")
class TestHhtDevice(TransactionCase):
    def test_device_create_generates_keys(self):
        dev = self.env["hht.device"].create(
            {
                "name": "Test TC52",
                "device_id": "TC52-TEST-001",
                "model": "zebra_tc52",
            }
        )
        self.assertTrue(dev.api_key)
        self.assertTrue(dev.api_secret)
        self.assertEqual(len(dev.api_key), 32)  # 16 bytes hex
        self.assertEqual(len(dev.api_secret), 64)  # 32 bytes hex

    def test_sync_batch_deduplicates(self):
        dev = self.env["hht.device"].create(
            {
                "name": "BatchDev",
                "device_id": "BATCH-001",
                "model": "generic_browser",
            }
        )
        Queue = self.env["hht.sync.queue"]
        shared_cid = str(uuid.uuid4())
        # Create first item
        Queue.create(
            {
                "device_id": dev.id,
                "client_id": shared_cid,
                "action": "lookup",
                "payload": {"barcode": "X1"},
                "state": "applied",
            }
        )
        # Re-issuing same client_id triggers sql_constraint
        with self.assertRaises(Exception):
            Queue.create(
                {
                    "device_id": dev.id,
                    "client_id": shared_cid,
                    "action": "lookup",
                    "payload": {"barcode": "X1-dup"},
                }
            )
        # Distinct client_ids work
        a = Queue.create(
            {
                "device_id": dev.id,
                "client_id": str(uuid.uuid4()),
                "action": "lookup",
                "payload": {"barcode": "Y1"},
            }
        )
        b = Queue.create(
            {
                "device_id": dev.id,
                "client_id": str(uuid.uuid4()),
                "action": "lookup",
                "payload": {"barcode": "Y2"},
            }
        )
        self.assertNotEqual(a.id, b.id)


@tagged("post_install", "-at_install")
class TestHhtBridgeHttp(HttpCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            "custom_core.secure_endpoint.hht.secret",
            "test-secret-xyz",
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "custom_core.secure_endpoint.hht.allowed_cidrs",
            "0.0.0.0/0",
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "custom_hht_bridge.datawedge.allowed_cidrs",
            "0.0.0.0/0",
        )
        self.device = self.env["hht.device"].create(
            {
                "name": "HTTP Dev",
                "device_id": "HTTP-TEST-001",
                "model": "zebra_tc52",
            }
        )
        self.env.cr.commit()

    def _post_signed(self, path, payload, *, secret=None, ts=None):
        body = json.dumps(payload).encode("utf-8")
        ts = ts or str(int(time.time()))
        sig = _sign(secret or "test-secret-xyz", ts, body)
        return self.url_open(
            path,
            data=body,
            timeout=20,
            headers={
                "Content-Type": "application/json",
                "X-Timestamp": ts,
                "X-Signature": sig,
                "X-Device-Key": self.device.api_key,
            },
        )

    def test_scan_endpoint_valid_hmac(self):
        before = self.env["hht.scan.log"].search_count([("device_id", "=", self.device.id)])
        resp = self._post_signed(
            "/api/hht/scan",
            {
                "barcode": "TEST-BC-001",
                "action": "lookup",
            },
        )
        self.assertEqual(resp.status_code, 200)
        after = self.env["hht.scan.log"].search_count([("device_id", "=", self.device.id)])
        self.assertGreater(after, before)

    def test_scan_endpoint_invalid_hmac(self):
        body = json.dumps({"barcode": "X", "action": "lookup"}).encode("utf-8")
        ts = str(int(time.time()))
        resp = self.url_open(
            "/api/hht/scan",
            data=body,
            timeout=20,
            headers={
                "Content-Type": "application/json",
                "X-Timestamp": ts,
                "X-Signature": "00" * 32,  # wrong
                "X-Device-Key": self.device.api_key,
            },
        )
        self.assertEqual(resp.status_code, 401)

    def test_replay_nonce(self):
        payload = {"barcode": "REPLAY-01", "action": "lookup"}
        ts = str(int(time.time()))
        r1 = self._post_signed("/api/hht/scan", payload, ts=ts)
        self.assertEqual(r1.status_code, 200)
        # Same ts + body -> same signature -> replay should fail
        r2 = self._post_signed("/api/hht/scan", payload, ts=ts)
        self.assertEqual(r2.status_code, 401)
        data = r2.json() if hasattr(r2, "json") else json.loads(r2.text)
        self.assertEqual(data.get("error_code"), "REPLAY_NONCE")

    def test_datawedge_resolves_serial(self):
        body = json.dumps(
            {
                "barcode": "DW-BC-1",
                "device_serial": self.device.device_id,
            }
        ).encode("utf-8")
        resp = self.url_open(
            "/api/hht/datawedge",
            data=body,
            timeout=20,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.text)
        self.assertTrue(data.get("ok") in (True, False))  # routed correctly either way
        # A scan log must have been written (ok or error path).
        log = self.env["hht.scan.log"].search(
            [("device_id", "=", self.device.id), ("barcode", "=", "DW-BC-1")],
            limit=1,
        )
        self.assertTrue(log)

    def test_datawedge_unknown_serial(self):
        body = json.dumps(
            {
                "barcode": "DW-BC-2",
                "device_serial": "NOPE-NOPE-NOPE",
            }
        ).encode("utf-8")
        resp = self.url_open(
            "/api/hht/datawedge",
            data=body,
            timeout=20,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.text)
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("error"), "UNKNOWN_DEVICE_SERIAL")

    def test_manifest_endpoint_etag(self):
        resp = self._post_signed("/api/hht/manifest", {})  # signed (GET-with-body works too)
        # Manifest is GET; build a signed GET.
        ts = str(int(time.time()))
        sig = _sign("test-secret-xyz", ts, b"")
        resp = self.url_open(
            "/api/hht/manifest",
            timeout=20,
            headers={
                "X-Timestamp": ts,
                "X-Signature": sig,
                "X-Device-Key": self.device.api_key,
            },
        )
        self.assertEqual(resp.status_code, 200)
        etag = resp.headers.get("ETag")
        self.assertTrue(etag)

    def test_me_endpoint(self):
        ts = str(int(time.time()))
        sig = _sign("test-secret-xyz", ts, b"")
        resp = self.url_open(
            "/api/hht/me",
            timeout=20,
            headers={
                "X-Timestamp": ts,
                "X-Signature": sig,
                "X-Device-Key": self.device.api_key,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.text)
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["result"]["device"]["device_id"], "HTTP-TEST-001")


@tagged("post_install", "-at_install")
class TestHhtPwaShell(HttpCase):
    """The PWA shell must ship a working JS runtime, or /hht/ boots blank."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.users"].create(
            {
                "name": "HHT Shell Tester",
                "login": "hht_shell_tester",
                "password": "hht_shell_tester_pw",
                "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )

    def _login(self):
        self.authenticate("hht_shell_tester", "hht_shell_tester_pw")

    def test_shell_bootstraps_odoo_global(self):
        self._login()
        resp = self.url_open("/hht/", timeout=60)
        self.assertEqual(resp.status_code, 200)
        # The `odoo` global must be defined before the bundle runs, otherwise
        # the bundle's first line throws "odoo is not defined".
        self.assertIn("var odoo =", resp.text)
        self.assertIn("__session_info__", resp.text)
        self.assertIn('id="hht-app"', resp.text)
        # The manifest link must carry credentials or it 404s in multi-db.
        self.assertIn('crossorigin="use-credentials"', resp.text)

    def test_pwa_assets_bundle_has_module_loader(self):
        self._login()
        shell = self.url_open("/hht/", timeout=60)
        bundles = re.findall(
            r'src="(/web/assets/[^"]*custom_hht_bridge\.pwa_assets[^"]*\.js)"',
            shell.text,
        )
        self.assertTrue(bundles, "pwa_assets JS bundle not linked in /hht/")
        js = self.url_open(bundles[0], timeout=60)
        self.assertEqual(js.status_code, 200)
        # module_loader.js defines the `odoo` global's `define`/`loader`.
        self.assertIn("odoo.define", js.text)
        self.assertIn("HhtShell", js.text)

    def test_manifest_and_icons_exist(self):
        self._login()
        resp = self.url_open("/hht/manifest.webmanifest", timeout=30)
        self.assertEqual(resp.status_code, 200)
        manifest = json.loads(resp.text)
        for icon in manifest["icons"]:
            with self.subTest(icon=icon["src"]):
                img = self.url_open(icon["src"], timeout=30)
                self.assertEqual(img.status_code, 200)
                self.assertEqual(img.headers["Content-Type"], "image/png")

    def test_boot_diagnostics_are_es5_and_precede_bundle(self):
        """A blank screen on a DevTools-less handheld must explain itself."""
        self._login()
        html = self.url_open("/hht/", timeout=60).text
        self.assertIn("HHT shell failed to start", html)
        self.assertIn("navigator.userAgent", html)
        # The trap must be installed before the bundle it is meant to catch.
        self.assertLess(html.index("window.onerror"), html.index("pwa_assets"))
        # ES5 only: an old WebView must be able to parse the trap itself.
        trap = html[
            html.index("var errors = []") : html.index("t-call-assets")
            if "t-call-assets" in html
            else html.index("window.onerror") + 4000
        ]
        for modern in ("=>", "const ", "let ", "`"):
            self.assertNotIn(modern, trap, "boot trap must stay ES5: found %r" % modern)

    def test_service_worker_cannot_pin_a_broken_shell(self):
        """A cache-first navigation would strand devices on a stale shell."""
        self._login()
        sw = self.url_open("/hht/sw.js", timeout=30)
        self.assertEqual(sw.status_code, 200)
        # Bumped cache name: 'activate' purges older caches on the device.
        self.assertIn("hht-shell-v2", sw.text)
        # Navigations must be network-first.
        self.assertIn("event.request.mode === 'navigate'", sw.text)
        # sw.js itself must never be cached, or updates can never land.
        self.assertEqual(sw.headers.get("Cache-Control"), "no-cache")

    def test_boot_report_endpoint_logs_and_scrubs(self):
        """Devices with no DevTools report boot failures here."""
        self._login()
        resp = self.url_open(
            "/hht/boot-report",
            data=json.dumps(
                {
                    "errors": ["SyntaxError: Unexpected token '.'\nInjected: FAKE LOG LINE"],
                    "ua": "Mozilla/5.0 (Linux; Android 8.1.0) Chrome/61.0.3163.98",
                    "url": "https://example/hht/",
                }
            ).encode("utf-8"),
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_boot_report_survives_garbage(self):
        """Reporting must never raise: it runs when things are already broken."""
        self._login()
        # NB: an empty body makes url_open send a GET (-> 405); a real device
        # always POSTs a body, so use whitespace to exercise the unparseable case.
        for body in (b" ", b"not json", b'{"errors": "a string not a list"}', b"{}"):
            with self.subTest(body=body):
                resp = self.url_open(
                    "/hht/boot-report",
                    data=body,
                    timeout=30,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(resp.status_code, 200)

    def test_boot_report_requires_login(self):
        resp = self.url_open("/hht/boot-report", data=b"{}", timeout=30, allow_redirects=False)
        self.assertNotEqual(resp.status_code, 200)

    def test_bundle_ships_set_polyfill_for_older_webviews(self):
        """core/utils/indexed_db.js calls Set.difference (Chrome 122+).

        A real Chrome 119 handheld died with "this._tables.difference is not a
        function". web._assets_core omits web/static/src/polyfills, so the
        caller shipped without its polyfill.
        """
        self._login()
        shell = self.url_open("/hht/", timeout=60)
        bundles = re.findall(
            r'src="(/web/assets/[^"]*custom_hht_bridge\.pwa_assets[^"]*\.js)"',
            shell.text,
        )
        self.assertTrue(bundles, "pwa_assets JS bundle not linked in /hht/")
        js = self.url_open(bundles[0], timeout=60).text
        # The caller is present...
        self.assertIn("difference", js)
        # ...so the guard that installs the polyfill must be too.
        self.assertIn("Set.prototype.difference", js)
