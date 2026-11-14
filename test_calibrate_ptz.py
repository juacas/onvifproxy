import base64
import hashlib
import http.server
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from unittest.mock import patch

import calibrate_ptz as cli
from fov_math import calculate_fov_move


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.content


def response(namespace, operation, content=""):
    return (
        f'<s:Envelope xmlns:s="{cli.SOAP}" xmlns:t="{namespace}" '
        f'xmlns:tt="{cli.SCHEMA}"><s:Body><t:{operation}Response>'
        f'{content}</t:{operation}Response></s:Body></s:Envelope>'
    ).encode()


class Camera:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def open(self, request, timeout):
        root = ET.fromstring(request.data)
        command = root.find(f"{{{cli.SOAP}}}Body")[0]
        operation = command.tag.split("}")[-1]
        self.calls.append((operation, request, root))
        if operation == self.fail:
            raise urllib.error.URLError("simulated failure")
        namespace = command.tag.split("}")[0][1:]
        content = ""
        if operation == "GetCapabilities":
            content = (
                "<tt:Capabilities><tt:Media><tt:XAddr>http://camera:2020/media</tt:XAddr>"
                "</tt:Media><tt:PTZ><tt:XAddr>http://camera:2020/ptz</tt:XAddr>"
                "</tt:PTZ></tt:Capabilities>"
            )
        elif operation == "GetProfiles":
            content = '<t:Profiles token="p1"><tt:PTZConfiguration token="cfg1"/></t:Profiles>'
        return FakeResponse(response(namespace, operation, content))


class CalibrationTests(unittest.TestCase):
    def run_cli(self, argv, camera, sleep=None):
        args = ["calibrate_ptz.py", "--url", "http://proxy:8211/onvif/service",
                "--user", "user", "--profile", "p1"] + argv
        with patch.object(sys, "argv", args), \
             patch.dict(os.environ, {"ONVIF_PASSWORD": "secret"}), \
             patch("urllib.request.urlopen", side_effect=camera.open), \
             patch("time.sleep", side_effect=sleep), \
             patch("sys.stdout", new_callable=io.StringIO):
            cli.main()

    def test_digest_and_action(self):
        camera = Camera()
        with patch("urllib.request.urlopen", side_effect=camera.open):
            cli.soap_request("http://proxy:8211/ptz", "user", "secret", cli.PTZ, "GetStatus")
        operation, request, root = camera.calls[0]
        self.assertEqual(operation, "GetStatus")
        self.assertIn(f"{cli.PTZ}/GetStatus", request.headers["Content-type"])
        nonce = base64.b64decode(root.find(f".//{{{cli.WSSE}}}Nonce").text)
        created = root.find(f".//{{{cli.WSU}}}Created").text
        expected = base64.b64encode(hashlib.sha1(nonce + created.encode() + b"secret").digest()).decode()
        self.assertEqual(root.find(f".//{{{cli.WSSE}}}Password").text, expected)
        self.assertNotIn(expected, cli.sanitized_xml(request.data))

    def test_probe_discovers_profile_and_proxy_service_paths(self):
        camera = Camera()
        self.run_cli(["probe"], camera)
        self.assertEqual([call[0] for call in camera.calls], [
            "GetCapabilities", "GetProfiles", "GetServiceCapabilities", "GetNodes",
            "GetConfigurationOptions", "GetStatus",
        ])
        self.assertEqual(camera.calls[1][1].full_url, "http://proxy:8211/media")
        self.assertEqual(camera.calls[-1][1].full_url, "http://proxy:8211/ptz")
        config = camera.calls[-2][2].find(f".//{{{cli.PTZ}}}ConfigurationToken")
        self.assertEqual(config.text, "cfg1")

    def test_diagonal_and_stop_with_correct_actions(self):
        camera = Camera()
        self.run_cli(["move", "--x", "-0.03", "--y", "-0.8",
                      "--seconds", "0.88", "--yes"], camera)
        self.assertEqual([c[0] for c in camera.calls], ["GetStatus", "ContinuousMove", "Stop", "GetStatus"])
        pan_tilt = camera.calls[1][2].find(f".//{{{cli.SCHEMA}}}PanTilt")
        self.assertEqual(pan_tilt.attrib, {"x": "-0.03", "y": "-0.8"})
        for operation, request, root in camera.calls:
            self.assertEqual(root.find(f".//{{{cli.WSA}}}Action").text, f"{cli.PTZ}/{operation}")
            self.assertIn(f"{cli.PTZ}/{operation}", request.headers["Content-type"])

    def test_stop_after_move_failure_and_interrupt(self):
        camera = Camera(fail="ContinuousMove")
        with self.assertRaises(cli.OnvifError):
            self.run_cli(["move", "--x", "0", "--y", "0.8", "--seconds", "0.5", "--yes"], camera)
        self.assertEqual([c[0] for c in camera.calls], ["GetStatus", "ContinuousMove", "Stop"])
        camera = Camera()
        with self.assertRaises(KeyboardInterrupt):
            self.run_cli(["move", "--x", "0", "--y", "0.8", "--seconds", "0.5", "--yes"],
                         camera, KeyboardInterrupt())
        self.assertEqual([c[0] for c in camera.calls], ["GetStatus", "ContinuousMove", "Stop"])

    def test_proxy_action_reproduces_mismatched_action(self):
        camera = Camera()
        self.run_cli(["move", "--x", "0", "--y", "-0.8", "--seconds", "0.5",
                      "--proxy-action", "--yes"], camera)
        for index in (1, 2):
            root = camera.calls[index][2]
            self.assertEqual(root.find(f".//{{{cli.WSA}}}Action").text, f"{cli.PTZ}/RelativeMove")

    def test_fov_uses_proxy_formula(self):
        cfg = {"pan_seconds": 1.3, "tilt_seconds": 0.9, "speed": 0.8}
        x, y, duration = calculate_fov_move(-0.02550954756069368, -0.9747241047189901, cfg)
        self.assertEqual((x, y), (-0.03, -0.8))
        self.assertAlmostEqual(duration, 0.8772516942470911)
        self.assertEqual(calculate_fov_move(0.001, 0.001, cfg), (0.0, 0.0, 0.0))

    def test_fov_cli_against_local_onvif_server(self):
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                root = ET.fromstring(self.rfile.read(int(self.headers["Content-Length"])))
                command = root.find(f"{{{cli.SOAP}}}Body")[0]
                operation = command.tag.split("}")[-1]
                requests.append((operation, command))
                content = response(cli.PTZ, operation)
                self.send_response(200)
                self.send_header("Content-Type", "application/soap+xml")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, format, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as config:
                config.write("cameras:\n  test:\n    pan_seconds: 1.3\n    tilt_seconds: 0.9\n    speed: 0.8\n")
                config.flush()
                result = subprocess.run(
                    [sys.executable, "calibrate_ptz.py",
                     "--url", f"http://127.0.0.1:{server.server_port}/onvif/service",
                     "--user", "user", "fov", "--camera", "test", "--config", config.name,
                     "--x", "-0.02550954756069368", "--y", "-0.9747241047189901", "--yes"],
                    env={**os.environ, "ONVIF_PASSWORD": "secret"},
                    capture_output=True, text=True, timeout=10,
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("vx=-0.030, vy=-0.800", result.stdout)
            self.assertEqual([item[0] for item in requests], ["GetStatus", "ContinuousMove", "Stop", "GetStatus"])
            velocity = requests[1][1].find(f".//{{{cli.SCHEMA}}}PanTilt")
            self.assertEqual(velocity.attrib, {"x": "-0.03", "y": "-0.8"})
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_fault_is_reported(self):
        fault = f'<s:Envelope xmlns:s="{cli.SOAP}"><s:Body><s:Fault><s:Reason>failed</s:Reason></s:Fault></s:Body></s:Envelope>'.encode()
        with patch("urllib.request.urlopen", return_value=FakeResponse(fault)):
            with self.assertRaisesRegex(cli.OnvifError, "SOAP Fault"):
                cli.soap_request("http://proxy/ptz", "user", "secret", cli.PTZ, "GetStatus")


if __name__ == "__main__":
    unittest.main()
