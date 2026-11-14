import asyncio
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

import httpx

import onvif_fov_proxy as proxy


SOAP = "http://www.w3.org/2003/05/soap-envelope"
PTZ = "http://www.onvif.org/ver20/ptz/wsdl"
SCHEMA = "http://www.onvif.org/ver10/schema"
RELATIVE_MOVE = f"""<s:Envelope xmlns:s="{SOAP}" xmlns:t="{PTZ}" xmlns:tt="{SCHEMA}">
<s:Body><t:RelativeMove><t:ProfileToken>profile_1</t:ProfileToken>
<t:Translation><tt:PanTilt x="-0.1" y="-0.5"/></t:Translation>
</t:RelativeMove></s:Body></s:Envelope>"""


class ProxyMoveTests(unittest.IsolatedAsyncioTestCase):
    async def perform_move(self, sequential, failed_operation=None):
        requests = []

        class FakeUpstream:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def request(self, method, url, content, headers):
                operation = proxy.get_soap_operation(content)
                requests.append((operation, ET.fromstring(content)))
                body = f'<s:Envelope xmlns:s="{SOAP}" xmlns:t="{PTZ}"><s:Body><t:{operation}Response/></s:Body></s:Envelope>'
                return httpx.Response(
                    500 if operation == failed_operation else 200,
                    content=body,
                    request=httpx.Request(method, url),
                )

        config = {
            "ip": "fake-camera", "proxy_port": 8211, "pan_seconds": 1.3,
            "tilt_seconds": 0.9, "speed": 0.8, "sequential_axes": sequential,
        }
        app = proxy.create_camera_app("test", config)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as client:
            with patch.object(proxy.httpx, "AsyncClient", FakeUpstream), \
                 patch.object(proxy.asyncio, "sleep", return_value=None) as sleep:
                response = await client.post("/onvif/service", content=RELATIVE_MOVE)
        return response, requests, [call.args[0] for call in sleep.call_args_list]

    async def test_sequential_axes_stop_between_steps(self):
        response, requests, delays = await self.perform_move(True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([request[0] for request in requests], [
            "ContinuousMove", "Stop", "ContinuousMove", "Stop",
        ])
        self.assertEqual(requests[0][1].find(f".//{{{SCHEMA}}}PanTilt").attrib,
                         {"x": "0.0", "y": "-0.8"})
        self.assertEqual(requests[2][1].find(f".//{{{SCHEMA}}}PanTilt").attrib,
                         {"x": "-0.8", "y": "0.0"})
        self.assertEqual(delays, [0.45, 0.13])

    async def test_default_diagonal_preserved(self):
        response, requests, delays = await self.perform_move(False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([request[0] for request in requests], ["ContinuousMove", "Stop"])
        self.assertEqual(requests[0][1].find(f".//{{{SCHEMA}}}PanTilt").attrib,
                         {"x": "-0.231", "y": "-0.8"})
        self.assertEqual(delays, [0.45])

    async def test_failed_move_stops_and_does_not_report_success(self):
        response, requests, delays = await self.perform_move(True, "ContinuousMove")
        self.assertEqual(response.status_code, 500)
        self.assertEqual([request[0] for request in requests], ["ContinuousMove", "Stop"])
        self.assertEqual(delays, [])


if __name__ == "__main__":
    unittest.main()
