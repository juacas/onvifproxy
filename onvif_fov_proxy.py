import asyncio
import base64
import logging
import math
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from xml.sax.saxutils import escape

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from fov_math import calculate_fov_move, calculate_sequential_fov_moves
from onvif_security import username_security

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("onvif-fov-proxy")

CONFIG_PATH = os.getenv("CONFIG_PATH", "/app/config/cameras.yaml")
SOAP_NS = "http://www.w3.org/2003/05/soap-envelope"
PTZ_NS = "http://www.onvif.org/ver20/ptz/wsdl"
SCHEMA_NS = "http://www.onvif.org/ver10/schema"

CONTINUOUS_MOVE_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"
            xmlns:tt="http://www.onvif.org/ver10/schema">
  <s:Header>{security_header}</s:Header>
  <s:Body>
    <tptz:ContinuousMove>
      <tptz:ProfileToken>{profile_token}</tptz:ProfileToken>
      <tptz:Velocity>
        <tt:PanTilt x="{vx}" y="{vy}"/>
      </tptz:Velocity>
    </tptz:ContinuousMove>
  </s:Body>
</s:Envelope>"""

STOP_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
  <s:Header>{security_header}</s:Header>
  <s:Body>
    <tptz:Stop>
      <tptz:ProfileToken>{profile_token}</tptz:ProfileToken>
      <tptz:PanTilt>true</tptz:PanTilt>
      <tptz:Zoom>false</tptz:Zoom>
    </tptz:Stop>
  </s:Body>
</s:Envelope>"""

RELATIVE_MOVE_RESPONSE = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
  <s:Body>
    <tptz:RelativeMoveResponse/>
  </s:Body>
</s:Envelope>"""


def log_body(level: int, label: str, body: bytes | str):
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    text = re.sub(
        r"(<(?P<tag>(?:[\w.-]+:)?(?:Username|Password|PasswordDigest|Nonce))\b[^>]*>).*?(</(?P=tag)\s*>)",
        r"\1[REDACTED]\3",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    logger.log(level, "%s: %s", label, text if text else "<vacío>")


def get_soap_operation(body: bytes | str) -> str | None:
    if not body:
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None

    soap_body = next(
        (element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "Body"),
        None,
    )
    if soap_body is None or not len(soap_body):
        return None
    return soap_body[0].tag.rsplit("}", 1)[-1]


def get_soap_action(headers) -> str | None:
    action = headers.get("soapaction") or headers.get("action")
    if not action:
        return None
    action = action.strip().strip('"').strip("'")
    return re.split(r"[/#]", action)[-1] or action


async def request_upstream(
    client: httpx.AsyncClient, cam_name: str, url: str, method: str,
    content: bytes, headers: dict
) -> httpx.Response:
    operation = get_soap_operation(content)
    operation_label = f" operación={operation}" if operation else ""
    logger.info("[%s] -> Cámara: %s %s%s", cam_name, method, url, operation_label)
    log_body(logging.DEBUG, f"[{cam_name}] Petición a cámara", content)
    try:
        response = await client.request(
            method=method,
            url=url,
            content=content,
            headers=headers,
        )
    except httpx.RequestError:
        logger.exception("[%s] Error en petición a cámara: %s %s", cam_name, method, url)
        raise

    response_operation = get_soap_operation(response.content)
    response_operation_label = f" operación={response_operation}" if response_operation else ""
    logger.info(
        "[%s] <- Cámara: %s %s status=%s%s",
        cam_name,
        method,
        url,
        response.status_code,
        response_operation_label,
    )
    log_body(logging.DEBUG, f"[{cam_name}] Respuesta de cámara", response.content)
    return response


def log_proxy_response(cam_name: str, status_code: int, body: bytes | str):
    operation = get_soap_operation(body)
    operation_label = f" operación={operation}" if operation else ""
    logger.info("[%s] <- Cliente: status=%s%s", cam_name, status_code, operation_label)
    log_body(logging.DEBUG, f"[{cam_name}] Respuesta al cliente", body)


def load_config() -> dict:
    path = Path(CONFIG_PATH)
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el archivo de configuración: {CONFIG_PATH}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    cameras = data.get("cameras", {})
    if not cameras:
        raise ValueError("No hay cámaras definidas bajo la clave 'cameras' en cameras.yaml")
    return cameras


def extract_soap_parts(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    ns = {
        "s": "http://www.w3.org/2003/05/soap-envelope",
        "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
        "tt": "http://www.onvif.org/ver10/schema",
    }
    header = root.find("s:Header", ns)
    security_xml = "".join(ET.tostring(c, encoding="unicode") for c in header) if header is not None else ""

    token_el = root.find(".//tptz:ProfileToken", ns)
    profile_token = token_el.text if token_el is not None else "profile_1"

    pantilt_el = root.find(".//tptz:Translation/tt:PanTilt", ns)
    dx = float(pantilt_el.attrib.get("x", 0.0)) if pantilt_el is not None else 0.0
    dy = float(pantilt_el.attrib.get("y", 0.0)) if pantilt_el is not None else 0.0

    return security_xml, profile_token, dx, dy


def inject_fov_capability(xml_text: str) -> str:
    fov_block = """
    <tt:RelativePanTiltTranslationSpace>
      <tt:URI>http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationSpaceFov</tt:URI>
      <tt:XRange><tt:Min>-1.0</tt:Min><tt:Max>1.0</tt:Max></tt:XRange>
      <tt:YRange><tt:Min>-1.0</tt:Min><tt:Max>1.0</tt:Max></tt:YRange>
    </tt:RelativePanTiltTranslationSpace>
    """
    if "TranslationSpaceFov" not in xml_text and "<tt:Spaces>" in xml_text:
        xml_text = xml_text.replace("<tt:Spaces>", f"<tt:Spaces>{fov_block}", 1)
    return xml_text


def rewrite_service_xaddrs(xml_text: str, proxy_host: str) -> str:
    pattern = r"(<(?:[\w.-]+:)?XAddr\b[^>]*>)(.*?)(</(?:[\w.-]+:)?XAddr\s*>)"

    def rewrite(match: re.Match) -> str:
        value = match.group(2)
        address = value.strip()
        try:
            parsed = urlsplit(address)
        except ValueError:
            return match.group(0)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return match.group(0)

        rewritten = urlunsplit(parsed._replace(netloc=proxy_host))
        whitespace_before = value[: len(value) - len(value.lstrip())]
        whitespace_after = value[len(value.rstrip()):]
        return f"{match.group(1)}{whitespace_before}{rewritten}{whitespace_after}{match.group(3)}"

    return re.sub(pattern, rewrite, xml_text, flags=re.IGNORECASE | re.DOTALL)


def advertise_move_status(xml_text: str) -> str | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    response = next(
        (element for element in root.iter()
         if element.tag.rsplit("}", 1)[-1] == "GetServiceCapabilitiesResponse"),
        None,
    )
    if response is None:
        return None

    capabilities = next(
        (element for element in response
         if element.tag.rsplit("}", 1)[-1] == "Capabilities"),
        None,
    )
    if capabilities is None:
        return None

    capabilities.set("MoveStatus", "true")
    return ET.tostring(
        root,
        encoding="unicode",
        xml_declaration=xml_text.lstrip().startswith("<?xml"),
    )


def update_move_status(xml_text: str, moving: bool) -> str | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    ptz_status = next(
        (element for element in root.iter()
         if element.tag.rsplit("}", 1)[-1] == "PTZStatus"),
        None,
    )
    if ptz_status is None:
        return None

    position = next(
        (element for element in ptz_status
         if element.tag.rsplit("}", 1)[-1] == "Position"),
        None,
    )
    namespace = ""
    if position is not None and position.tag.startswith("{"):
        namespace = position.tag.split("}", 1)[0] + "}"
    elif ptz_status.tag.startswith("{"):
        namespace = ptz_status.tag.split("}", 1)[0] + "}"

    move_status = next(
        (element for element in ptz_status
         if element.tag.rsplit("}", 1)[-1] == "MoveStatus"),
        None,
    )
    if move_status is None:
        move_status = ET.SubElement(ptz_status, f"{namespace}MoveStatus")

    pan_tilt = next(
        (element for element in move_status
         if element.tag.rsplit("}", 1)[-1] == "PanTilt"),
        None,
    )
    if pan_tilt is None:
        pan_tilt = ET.SubElement(move_status, f"{namespace}PanTilt")
    pan_tilt.text = "MOVING" if moving else "IDLE"

    zoom = next(
        (element for element in move_status
         if element.tag.rsplit("}", 1)[-1] == "Zoom"),
        None,
    )
    if zoom is None:
        zoom = ET.SubElement(move_status, f"{namespace}Zoom")
    zoom.text = "IDLE"

    return ET.tostring(
        root,
        encoding="unicode",
        xml_declaration=xml_text.lstrip().startswith("<?xml"),
    )


def create_camera_app(cam_name: str, cfg: dict) -> FastAPI:
    app = FastAPI(title=f"ONVIF FOV Proxy - {cam_name}")
    move_lock = asyncio.Lock()
    onvif_port = cfg.get("onvif_port", 2020)
    tapo_base_url = f"http://{cfg['ip']}:{onvif_port}"

    async def execute_emulated_fov_move(
        client: httpx.AsyncClient, path: str, headers: dict,
        security_header: str, profile_token: str, dx: float, dy: float
    ):
        if move_lock.locked():
            logger.debug(f"[{cam_name}] Movimiento descartado: motor ocupado")
            return False

        async with move_lock:
            if cfg.get("sequential_axes", False):
                moves = calculate_sequential_fov_moves(dx, dy, cfg)
            else:
                vx, vy, duration = calculate_fov_move(dx, dy, cfg)
                moves = [(vx, vy, duration)] if duration else []
            if not moves:
                return True

            stop_payload = STOP_TEMPLATE.format(
                security_header=security_header,
                profile_token=profile_token,
            )

            for vx, vy, duration in moves:
                logger.info(
                    f"[{cam_name}] FOV ({dx:+.2f}, {dy:+.2f}) -> "
                    f"ContinuousMove (vx={vx:+.2f}, vy={vy:+.2f}) durante {duration:.2f}s"
                )
                move_payload = CONTINUOUS_MOVE_TEMPLATE.format(
                    security_header=security_header,
                    profile_token=profile_token,
                    vx=vx,
                    vy=vy,
                )
                try:
                    response = await request_upstream(
                        client,
                        cam_name,
                        f"{tapo_base_url}{path}",
                        "POST",
                        move_payload.encode("utf-8"),
                        headers,
                    )
                    response.raise_for_status()
                    if get_soap_operation(response.content) == "Fault":
                        raise RuntimeError(f"[{cam_name}] La cámara rechazó ContinuousMove con un SOAP Fault")
                    await asyncio.sleep(duration)
                finally:
                    stop_response = await request_upstream(
                        client,
                        cam_name,
                        f"{tapo_base_url}{path}",
                        "POST",
                        stop_payload.encode("utf-8"),
                        headers,
                    )
                    stop_response.raise_for_status()
                    if get_soap_operation(stop_response.content) == "Fault":
                        raise RuntimeError(f"[{cam_name}] La cámara rechazó Stop con un SOAP Fault")
            return True

    def check_calibration_origin(request: Request):
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https") or parsed.netloc.lower() != request.headers.get("host", "").lower():
                raise HTTPException(403, "Origen no permitido.")

    async def calibration_data(request: Request) -> dict:
        check_calibration_origin(request)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(415, "Se requiere application/json.")
        try:
            data = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "JSON inválido.") from exc
        if not isinstance(data, dict):
            raise HTTPException(400, "La petición debe ser un objeto JSON.")
        if not all(isinstance(value, str) and value for value in (
            data.get("username"), data.get("password"), data.get("profile", "profile_1")
        )):
            raise HTTPException(400, "Indica usuario, contraseña y perfil ONVIF.")
        return data

    def calibration_ptz_path() -> str:
        path = cfg.get("ptz_path", "/onvif/service")
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
            raise HTTPException(503, "ptz_path debe ser una ruta ONVIF absoluta.")
        return path

    async def camera_ptz_request(client: httpx.AsyncClient, data: dict, operation: str,
                                 position: tuple[float, float] | None = None) -> ET.Element:
        envelope = ET.Element(f"{{{SOAP_NS}}}Envelope")
        header = ET.SubElement(envelope, f"{{{SOAP_NS}}}Header")
        header.append(username_security(data["username"], data["password"]))
        body = ET.SubElement(envelope, f"{{{SOAP_NS}}}Body")
        command = ET.SubElement(body, f"{{{PTZ_NS}}}{operation}")
        ET.SubElement(command, f"{{{PTZ_NS}}}ProfileToken").text = data.get("profile", "profile_1")
        if position is not None:
            target = ET.SubElement(command, f"{{{PTZ_NS}}}Position")
            ET.SubElement(target, f"{{{SCHEMA_NS}}}PanTilt", x=str(position[0]), y=str(position[1]))
        try:
            response = await request_upstream(
                client, cam_name, f"{tapo_base_url}{calibration_ptz_path()}", "POST",
                ET.tostring(envelope, encoding="utf-8", xml_declaration=True),
                {"content-type": f'application/soap+xml; charset=utf-8; action="{PTZ_NS}/{operation}"'},
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except (httpx.HTTPError, ET.ParseError) as exc:
            logger.warning("[%s] Error en %s: %s", cam_name, operation, type(exc).__name__)
            raise HTTPException(502, f"No se pudo completar {operation}; revisa los logs y las capacidades ONVIF.") from exc
        if root.find(f".//{{{SOAP_NS}}}Fault") is not None:
            raise HTTPException(502, f"La cámara rechazó {operation} con un SOAP Fault; revisa las capacidades ONVIF.")
        if root.find(f".//{{{PTZ_NS}}}{operation}Response") is None:
            raise HTTPException(502, f"La cámara no devolvió una respuesta válida a {operation}.")
        return root

    async def get_snapshot(client: httpx.AsyncClient):
        url = cfg.get("snapshot_url", "")
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise HTTPException(503, "Configura snapshot_url HTTP(S) sin credenciales para esta cámara.")
        try:
            response = await client.get(url, headers={"Cache-Control": "no-cache"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("[%s] Error al obtener snapshot: %s", cam_name, type(exc).__name__)
            raise HTTPException(502, "No se pudo obtener el snapshot; revisa snapshot_url y su acceso.") from exc
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in ("image/jpeg", "image/png", "image/webp") or not response.content:
            raise HTTPException(502, "El endpoint de snapshot no devolvió una imagen JPEG, PNG o WebP.")
        if len(response.content) > 8 * 1024 * 1024:
            raise HTTPException(502, "El snapshot supera el límite de 8 MiB.")
        return response.content, content_type

    @app.get("/calibration")
    async def calibration_page():
        return Response(
            content=Path(__file__).with_name("calibration.html").read_text(encoding="utf-8"),
            media_type="text/html",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/calibration/snapshot")
    async def calibration_snapshot(request: Request):
        check_calibration_origin(request)
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            image, content_type = await get_snapshot(client)
        return Response(image, media_type=content_type, headers={"Cache-Control": "no-store"})

    @app.post("/calibration/move")
    async def calibration_move(request: Request):
        data = await calibration_data(request)
        username, password = data["username"], data["password"]
        profile = data.get("profile", "profile_1")
        x, y = data.get("x"), data.get("y")
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not math.isfinite(v) or abs(v) > 1 for v in (x, y)):
            raise HTTPException(400, "x e y deben ser números entre -1 y 1.")
        if abs(x) <= 0.01 and abs(y) <= 0.01:
            raise HTTPException(400, "El desplazamiento es demasiado pequeño.")
        path = calibration_ptz_path()
        if move_lock.locked():
            raise HTTPException(409, "La cámara está ocupada.")
        security = ET.tostring(username_security(username, password), encoding="unicode")
        headers = {"content-type": "application/soap+xml; charset=utf-8"}
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                before, before_type = await get_snapshot(client)
                moved = await execute_emulated_fov_move(
                    client, path, headers, security, escape(profile), x, y,
                )
                if not moved:
                    raise HTTPException(409, "La cámara está ocupada.")
                await asyncio.sleep(0.5)
                try:
                    after, after_type = await get_snapshot(client)
                except HTTPException as exc:
                    raise HTTPException(exc.status_code, f"El movimiento se ejecutó, pero falló la captura posterior: {exc.detail}") from exc
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.exception("[%s] Error durante calibración PTZ", cam_name)
            raise HTTPException(502, "Falló el movimiento o Stop; comprueba la cámara y los logs.") from exc
        return {
            "before": f"data:{before_type};base64,{base64.b64encode(before).decode()}",
            "after": f"data:{after_type};base64,{base64.b64encode(after).decode()}",
            "x": x,
            "y": y,
        }

    @app.post("/calibration/status")
    async def calibration_status(request: Request):
        data = await calibration_data(request)
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            root = await camera_ptz_request(client, data, "GetStatus")
        pan_tilt = root.find(
            f".//{{{PTZ_NS}}}GetStatusResponse/{{{PTZ_NS}}}PTZStatus/"
            f"{{{SCHEMA_NS}}}Position/{{{SCHEMA_NS}}}PanTilt"
        )
        try:
            if pan_tilt is None:
                raise ValueError("PanTilt ausente")
            pan = float(pan_tilt.attrib["x"])
            tilt = float(pan_tilt.attrib["y"])
            if not math.isfinite(pan) or not math.isfinite(tilt):
                raise ValueError("coordenadas no finitas")
        except (KeyError, ValueError) as exc:
            logger.warning("[%s] GetStatus no contiene una posición PanTilt válida", cam_name)
            raise HTTPException(502, "La cámara no devolvió una posición PanTilt válida.") from exc
        return {"pan": pan, "tilt": tilt}

    @app.post("/calibration/optical")
    async def calibration_optical(request: Request):
        check_calibration_origin(request)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(415, "Se requiere application/json.")
        try:
            data = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "JSON inválido.") from exc
        if not isinstance(data, dict):
            raise HTTPException(400, "La petición debe ser un objeto JSON.")
        try:
            from optical_calibration import fit_optical_calibration
        except ImportError as exc:
            logger.exception("[%s] Dependencia de calibración óptica no disponible", cam_name)
            raise HTTPException(503, "Reconstruye la imagen Docker para instalar las dependencias de calibración óptica.") from exc
        try:
            return await run_in_threadpool(fit_optical_calibration, data.get("samples"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/calibration/absolute")
    async def calibration_absolute(request: Request):
        data = await calibration_data(request)
        pan, tilt = data.get("pan"), data.get("tilt")
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not math.isfinite(v) or abs(v) > 1 for v in (pan, tilt)):
            raise HTTPException(400, "Pan y tilt deben ser números entre -1 y 1.")
        if move_lock.locked():
            raise HTTPException(409, "La cámara está ocupada.")
        async with move_lock:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                await camera_ptz_request(client, data, "AbsoluteMove", (pan, tilt))
        return {"message": "Orden AbsoluteMove aceptada; la cámara podría seguir moviéndose."}

    @app.api_route("/{path:path}", methods=["POST", "GET"])
    async def onvif_proxy(request: Request, path: str):
        body = await request.body()
        body_str = body.decode("utf-8", errors="ignore")
        operation = get_soap_operation(body) or get_soap_action(request.headers)
        operation_label = f" operación={operation}" if operation else ""
        logger.info(
            "[%s] -> Cliente: %s /%s%s",
            cam_name,
            request.method,
            path,
            operation_label,
        )
        log_body(logging.DEBUG, f"[{cam_name}] Petición del cliente", body)
        forward_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length")
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            if "RelativeMove" in body_str:
                sec_header, token, dx, dy = extract_soap_parts(body)
                await execute_emulated_fov_move(client, f"/{path}", forward_headers, sec_header, token, dx, dy)
                log_proxy_response(cam_name, 200, RELATIVE_MOVE_RESPONSE)
                return Response(content=RELATIVE_MOVE_RESPONSE, media_type="application/soap+xml")

            resp = await request_upstream(
                client,
                cam_name,
                f"{tapo_base_url}/{path}",
                request.method,
                body,
                forward_headers,
            )
            resp_text = resp.text

            if resp.is_success and operation == "GetServiceCapabilities":
                updated_response = advertise_move_status(resp_text)
                if updated_response is None:
                    logger.warning(
                        "[%s] No se pudo anunciar MoveStatus en GetServiceCapabilities",
                        cam_name,
                    )
                else:
                    logger.info("[%s] Anunciando soporte ONVIF MoveStatus", cam_name)
                    resp_text = updated_response
            elif resp.is_success and operation == "GetStatus":
                updated_response = update_move_status(resp_text, move_lock.locked())
                if updated_response is None:
                    logger.warning(
                        "[%s] No se pudo actualizar MoveStatus en GetStatus",
                        cam_name,
                    )
                else:
                    resp_text = updated_response

            proxy_host = request.headers.get("host", f"127.0.0.1:{cfg['proxy_port']}")
            resp_text = re.sub(
                rf"http://{re.escape(cfg['ip'])}:{onvif_port}",
                f"http://{proxy_host}",
                resp_text,
            )
            resp_text = rewrite_service_xaddrs(resp_text, proxy_host)

            if "GetConfigurationOptions" in body_str:
                logger.info(f"[{cam_name}] Inyectando soporte TranslationSpaceFov para Frigate")
                resp_text = inject_fov_capability(resp_text)

            log_proxy_response(cam_name, resp.status_code, resp_text)
            return Response(
                content=resp_text.encode("utf-8"),
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "application/soap+xml"),
            )

    return app


async def main():
    cameras = load_config()
    servers = []
    for name, cfg in cameras.items():
        port = cfg["proxy_port"]
        logger.info(f"Iniciando proxy para '{name}' en puerto {port} -> {cfg['ip']}:{cfg.get('onvif_port', 2020)}")
        app = create_camera_app(name, cfg)
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level=LOG_LEVEL.lower())
        servers.append(uvicorn.Server(config).serve())

    await asyncio.gather(*servers)


if __name__ == "__main__":
    asyncio.run(main())
