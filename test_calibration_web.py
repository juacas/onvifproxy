import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

import httpx

import onvif_fov_proxy as proxy


SOAP = "http://www.w3.org/2003/05/soap-envelope"
PTZ = "http://www.onvif.org/ver20/ptz/wsdl"
SCHEMA = "http://www.onvif.org/ver10/schema"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.fail_operation = None
        self.status_position = '<tt:Position><tt:PanTilt x="0.240951" y="1.000000"/></tt:Position>'
        test = self

        class FakeUpstream:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, headers):
                test.calls.append(("snapshot", url))
                content = b"before" if sum(x[0] == "snapshot" for x in test.calls) == 1 else b"after"
                return httpx.Response(200, content=content, headers={"content-type": "image/jpeg"},
                                      request=httpx.Request("GET", url))

            async def request(self, method, url, content, headers):
                operation = proxy.get_soap_operation(content)
                test.calls.append((operation, ET.fromstring(content)))
                if operation == test.fail_operation:
                    body = f'<s:Envelope xmlns:s="{SOAP}"><s:Body><s:Fault/></s:Body></s:Envelope>'
                elif operation == "GetStatus":
                    body = (f'<s:Envelope xmlns:s="{SOAP}" xmlns:t="{PTZ}" xmlns:tt="{SCHEMA}">'
                            f'<s:Body><t:GetStatusResponse><t:PTZStatus>{test.status_position}</t:PTZStatus>'
                            '</t:GetStatusResponse></s:Body></s:Envelope>')
                else:
                    body = f'<s:Envelope xmlns:s="{SOAP}" xmlns:t="{PTZ}"><s:Body><t:{operation}Response/></s:Body></s:Envelope>'
                return httpx.Response(200, content=body, request=httpx.Request(method, url))

        self.fake = FakeUpstream
        self.app = proxy.create_camera_app("test", {
            "ip": "fake-camera", "proxy_port": 8211, "snapshot_url": "http://frigate/api/test/latest.jpg",
            "pan_seconds": 1.3, "tilt_seconds": 0.9, "speed": 0.8, "sequential_axes": True,
        })
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
            base_url="http://proxy:8211",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_cross_origin_is_rejected(self):
        with patch.object(proxy.httpx, "AsyncClient", self.fake):
            cross_origin = await self.client.get("/calibration/snapshot", headers={
                "Origin": "http://evil.example",
            })
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(self.calls, [])

    async def test_snapshot_and_fov_before_after(self):
        headers = {"Origin": "http://proxy:8211"}
        with patch.object(proxy.httpx, "AsyncClient", self.fake), \
             patch.object(proxy.asyncio, "sleep", return_value=None):
            response = await self.client.post("/calibration/move", headers=headers, json={
                "username": "cam-user", "password": "cam-secret", "profile": "p&1", "x": -0.1, "y": -0.5,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([call[0] for call in self.calls], [
            "snapshot", "ContinuousMove", "Stop", "ContinuousMove", "Stop", "snapshot",
        ])
        self.assertEqual(response.json()["before"], "data:image/jpeg;base64,YmVmb3Jl")
        self.assertEqual(response.json()["after"], "data:image/jpeg;base64,YWZ0ZXI=")
        root = self.calls[1][1]
        self.assertEqual(root.find(f".//{{{PTZ}}}ProfileToken").text, "p&1")
        self.assertEqual(root.find(f".//{{{WSSE}}}Username").text, "cam-user")
        self.assertNotIn("cam-secret", ET.tostring(root, encoding="unicode"))
        self.assertEqual(root.find(f".//{{{SCHEMA}}}PanTilt").attrib, {"x": "0.0", "y": "-0.8"})

    async def test_invalid_fov_does_not_move(self):
        with patch.object(proxy.httpx, "AsyncClient", self.fake):
            response = await self.client.post("/calibration/move", json={
                "username": "u", "password": "p", "profile": "p", "x": 1.01, "y": 0,
            })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls, [])

    async def test_full_range_fov_matches_frigate(self):
        with patch.object(proxy.httpx, "AsyncClient", self.fake), \
             patch.object(proxy.asyncio, "sleep", return_value=None) as sleep:
            response = await self.client.post("/calibration/move", json={
                "username": "u", "password": "p", "profile": "p", "x": -0.92, "y": -0.54,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([call[0] for call in self.calls], [
            "snapshot", "ContinuousMove", "Stop", "ContinuousMove", "Stop", "snapshot",
        ])
        self.assertEqual(self.calls[1][1].find(f".//{{{SCHEMA}}}PanTilt").attrib,
                         {"x": "0.0", "y": "-0.8"})
        self.assertEqual(self.calls[3][1].find(f".//{{{SCHEMA}}}PanTilt").attrib,
                         {"x": "-0.8", "y": "0.0"})
        self.assertAlmostEqual(sleep.call_args_list[0].args[0], 0.486)
        self.assertAlmostEqual(sleep.call_args_list[1].args[0], 1.196)

    async def test_read_camera_position(self):
        with patch.object(proxy.httpx, "AsyncClient", self.fake):
            response = await self.client.post("/calibration/status", json={
                "username": "u", "password": "p", "profile": "p&1",
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"pan": 0.240951, "tilt": 1.0})
        self.assertEqual(self.calls[0][0], "GetStatus")
        self.assertEqual(self.calls[0][1].find(f".//{{{PTZ}}}ProfileToken").text, "p&1")

    async def test_missing_position_is_error(self):
        self.status_position = ""
        with patch.object(proxy.httpx, "AsyncClient", self.fake):
            response = await self.client.post("/calibration/status", json={"username": "u", "password": "p"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("posición", response.json()["detail"])

    async def test_move_absolute_sends_position(self):
        with patch.object(proxy.httpx, "AsyncClient", self.fake):
            response = await self.client.post("/calibration/absolute", json={
                "username": "u", "password": "p", "profile": "p", "pan": -1, "tilt": 0.25,
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("podría seguir moviéndose", response.json()["message"])
        self.assertEqual(self.calls[0][0], "AbsoluteMove")
        target = self.calls[0][1].find(f".//{{{PTZ}}}Position/{{{SCHEMA}}}PanTilt")
        self.assertEqual(target.attrib, {"x": "-1", "y": "0.25"})

    async def test_absolute_fault_and_invalid_coordinates(self):
        self.fail_operation = "AbsoluteMove"
        with patch.object(proxy.httpx, "AsyncClient", self.fake):
            failed = await self.client.post("/calibration/absolute", json={
                "username": "u", "password": "p", "pan": 0.5, "tilt": -1,
            })
            invalid = await self.client.post("/calibration/absolute", json={
                "username": "u", "password": "p", "pan": 1.1, "tilt": 0,
            })
        self.assertEqual(failed.status_code, 502)
        self.assertIn("SOAP Fault", failed.json()["detail"])
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(len(self.calls), 1)

    async def test_optical_calibration_endpoint(self):
        samples = [{"pan": 0.1}]
        with patch("optical_calibration.fit_optical_calibration",
                   return_value={"horizontal_fov_degrees": 80, "samples": 1}) as fit:
            response = await self.client.post("/calibration/optical", json={"samples": samples})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["horizontal_fov_degrees"], 80)
        fit.assert_called_once_with(samples)
        self.assertEqual(self.calls, [])

    async def test_optical_calibration_rejects_bad_samples(self):
        response = await self.client.post("/calibration/optical", json={"samples": []})
        self.assertEqual(response.status_code, 400)
        self.assertIn("8", response.json()["detail"])
        self.assertEqual(self.calls, [])

    async def test_page_and_snapshot(self):
        page = await self.client.get("/calibration")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Calibración PTZ", page.text)
        with patch.object(proxy.httpx, "AsyncClient", self.fake):
            image = await self.client.get("/calibration/snapshot")
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.content, b"before")


if __name__ == "__main__":
    unittest.main()
