#!/usr/bin/env python3
"""Diagnóstico y calibración manual de movimientos ONVIF PTZ."""

import argparse
import getpass
import math
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from onvif_security import BASE64_ENCODING, TOKEN_PROFILE, WSSE, WSU, username_security

SOAP = "http://www.w3.org/2003/05/soap-envelope"
PTZ = "http://www.onvif.org/ver20/ptz/wsdl"
DEVICE = "http://www.onvif.org/ver10/device/wsdl"
MEDIA = "http://www.onvif.org/ver10/media/wsdl"
SCHEMA = "http://www.onvif.org/ver10/schema"
WSA = "http://www.w3.org/2005/08/addressing"

for prefix, namespace in (
    ("s", SOAP), ("tptz", PTZ), ("tds", DEVICE), ("trt", MEDIA),
    ("tt", SCHEMA), ("wsa", WSA), ("wsse", WSSE), ("wsu", WSU),
):
    ET.register_namespace(prefix, namespace)


class OnvifError(Exception):
    pass


def element(parent, namespace, name, text=None, **attributes):
    child = ET.SubElement(parent, f"{{{namespace}}}{name}", attributes)
    if text is not None:
        child.text = str(text)
    return child


def sanitized_xml(data):
    root = ET.fromstring(data)
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() in {
            "username", "password", "passworddigest", "nonce"
        }:
            node.text = "[REDACTED]"
    ET.indent(root)
    return ET.tostring(root, encoding="unicode")


def soap_request(url, username, password, namespace, operation, parameters=None,
                 action_operation=None, timeout=5.0, debug=False):
    action = f"{namespace}/{action_operation or operation}"
    envelope = ET.Element(f"{{{SOAP}}}Envelope")
    header = element(envelope, SOAP, "Header")
    element(header, WSA, "Action", action)
    element(header, WSA, "MessageID", f"urn:uuid:{uuid.uuid4()}")
    element(header, WSA, "To", url)
    header.append(username_security(username, password))
    body = element(envelope, SOAP, "Body")
    command = element(body, namespace, operation)
    if parameters:
        parameters(command)
    payload = ET.tostring(envelope, encoding="utf-8", xml_declaration=True)
    if debug:
        print(f"\n--> {operation} {url}\n{sanitized_xml(payload)}", flush=True)
    request = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read()
    except urllib.error.HTTPError as exc:
        content = exc.read()
        try:
            details = sanitized_xml(content)
        except ET.ParseError:
            details = "<respuesta no XML omitida>"
        raise OnvifError(f"{operation}: HTTP {exc.code}\n{details}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OnvifError(f"{operation}: error de conexión: {exc}") from exc
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise OnvifError(f"{operation}: la respuesta no es XML válido") from exc
    fault = root.find(f".//{{{SOAP}}}Fault")
    if fault is not None:
        raise OnvifError(f"{operation}: SOAP Fault\n{sanitized_xml(content)}")
    if root.find(f".//{{{namespace}}}{operation}Response") is None:
        raise OnvifError(f"{operation}: la respuesta SOAP no contiene {operation}Response")
    if debug:
        print(f"<-- {operation}\n{sanitized_xml(content)}", flush=True)
    return root


def service_url(base_url, root, service):
    address = root.find(f".//{{{SCHEMA}}}{service}/{{{SCHEMA}}}XAddr")
    if address is None or not address.text or not address.text.strip():
        raise OnvifError(f"GetCapabilities no anuncia XAddr para {service}")
    endpoint = urlsplit(address.text.strip())
    if endpoint.scheme not in ("http", "https") or not endpoint.netloc:
        raise OnvifError(f"XAddr inválido para {service}")
    base = urlsplit(base_url)
    return urlunsplit((base.scheme, base.netloc, endpoint.path or "/", endpoint.query, ""))


def print_result(title, root):
    print(f"\n=== {title} ===\n{sanitized_xml(ET.tostring(root))}", flush=True)


def number(text):
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("debe ser un número") from exc
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError("debe ser finito")
    return value


def bounded(value, minimum, maximum, name):
    if not minimum <= value <= maximum:
        raise OnvifError(f"{name} debe estar entre {minimum} y {maximum}")


def confirm(message, yes):
    print(message, flush=True)
    if not yes:
        if not sys.stdin.isatty():
            raise OnvifError("se necesita confirmación interactiva o --yes")
        if input("¿Continuar? [s/N] ").strip().lower() not in ("s", "si", "sí"):
            raise OnvifError("movimiento cancelado")


def profile_parameter(profile):
    return lambda command: element(command, PTZ, "ProfileToken", profile)


def main():
    parser = argparse.ArgumentParser(
        description="Inspecciona ONVIF y prueba/calibra movimientos PTZ. Sin movimientos automáticos."
    )
    parser.add_argument("--url", help="URL del servicio ONVIF de cámara o proxy")
    parser.add_argument("--user", default=os.environ.get("ONVIF_USER"), help="Usuario ONVIF (o ONVIF_USER)")
    parser.add_argument("--profile", default="profile_1", help="Token del perfil PTZ (por defecto profile_1)")
    parser.add_argument("--timeout", type=number, default=5.0, help="Timeout HTTP en segundos")
    parser.add_argument("--debug", action="store_true", help="Mostrar XML SOAP con credenciales redactadas")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe", help="Consultar capacidades, perfiles, opciones y estado sin mover")
    sub.add_parser("status", help="Consultar GetStatus")
    move = sub.add_parser("move", help="ContinuousMove directo seguido de Stop")
    move.add_argument("--x", type=number, required=True, help="Velocidad horizontal entre -1 y 1")
    move.add_argument("--y", type=number, required=True, help="Velocidad vertical entre -1 y 1")
    move.add_argument("--seconds", type=number, required=True, help="Duración entre 0.03 y 5 segundos")
    fov = sub.add_parser("fov", help="Reproducir el cálculo FOV del proxy mediante ContinuousMove")
    fov.add_argument("--x", type=number, required=True, help="Desplazamiento horizontal FOV entre -1 y 1")
    fov.add_argument("--y", type=number, required=True, help="Desplazamiento vertical FOV entre -1 y 1")
    fov.add_argument("--camera", required=True, help="Nombre de cámara en cameras.yaml")
    fov.add_argument("--config", default="cameras.yaml", help="Ruta a cameras.yaml")
    mode = fov.add_mutually_exclusive_group()
    mode.add_argument("--sequential", dest="sequential", action="store_true", default=None,
                      help="Probar tilt y pan por separado")
    mode.add_argument("--diagonal", dest="sequential", action="store_false",
                      help="Probar ambos ejes simultáneamente")
    for command in (move, fov):
        command.add_argument("--yes", action="store_true", help="Confirmar el movimiento sin preguntar")
        command.add_argument(
            "--proxy-action", action="store_true",
            help="Reproducir la acción RelativeMove en cabeceras de ContinuousMove y Stop",
        )
    estimate = sub.add_parser("estimate", help="Calcular segundos/FOV a partir de una prueba observada")
    estimate.add_argument("--seconds", type=number, required=True, help="Duración del movimiento")
    estimate.add_argument("--fov", type=number, required=True, help="Distancia observada en fracciones del encuadre")
    args = parser.parse_args()

    if args.command == "estimate":
        if args.seconds <= 0 or args.fov <= 0:
            raise OnvifError("--seconds y --fov deben ser mayores que cero")
        print(f"Segundos por FOV: {args.seconds / args.fov:.3f}")
        return

    parsed = urlsplit(args.url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise OnvifError("--url debe ser una URL HTTP(S) sin credenciales")
    if not args.user:
        raise OnvifError("indica --user o la variable ONVIF_USER")
    if args.timeout <= 0:
        raise OnvifError("--timeout debe ser mayor que cero")
    password = os.environ.get("ONVIF_PASSWORD")
    if password is None:
        password = getpass.getpass("Contraseña ONVIF: ")

    def request(url, namespace, operation, parameters=None, action_operation=None):
        return soap_request(
            url, args.user, password, namespace, operation, parameters,
            action_operation=action_operation, timeout=args.timeout, debug=args.debug,
        )

    def status():
        return request(args.url, PTZ, "GetStatus", profile_parameter(args.profile))

    if args.command == "status":
        print_result("GetStatus", status())
        return
    if args.command == "probe":
        capabilities = request(
            args.url, DEVICE, "GetCapabilities",
            lambda command: element(command, DEVICE, "Category", "All"),
        )
        print_result("GetCapabilities", capabilities)
        media_url = service_url(args.url, capabilities, "Media")
        ptz_url = service_url(args.url, capabilities, "PTZ")
        print(f"\nMedia: {media_url}\nPTZ: {ptz_url}")
        profiles = request(media_url, MEDIA, "GetProfiles")
        print_result("GetProfiles (busca el token del perfil y de PTZConfiguration)", profiles)
        profiles_found = profiles.findall(f".//{{{MEDIA}}}Profiles")
        selected = next((p for p in profiles_found if p.get("token") == args.profile), None)
        if selected is None:
            raise OnvifError(f"no se encontró el perfil {args.profile!r}; selecciona uno con --profile")
        configuration = selected.find(f"{{{SCHEMA}}}PTZConfiguration")
        token = configuration.get("token") if configuration is not None else None
        if not token:
            raise OnvifError(f"el perfil {args.profile!r} no tiene token PTZConfiguration")
        failed = []
        for operation, parameters in (
            ("GetServiceCapabilities", None),
            ("GetNodes", None),
            ("GetConfigurationOptions", lambda command: element(command, PTZ, "ConfigurationToken", token)),
            ("GetStatus", profile_parameter(args.profile)),
        ):
            try:
                print_result(operation, request(ptz_url, PTZ, operation, parameters))
            except OnvifError as exc:
                print(f"\n{operation}: {exc}", file=sys.stderr)
                failed.append(operation)
        if failed:
            raise OnvifError(f"fallaron las consultas: {', '.join(failed)}")
        return

    bounded(args.x, -1, 1, "--x")
    bounded(args.y, -1, 1, "--y")
    if args.command == "fov":
        from fov_math import calculate_fov_move, calculate_sequential_fov_moves
        try:
            import yaml
        except ImportError as exc:
            raise OnvifError("el subcomando fov necesita PyYAML; ejecuta el script en el contenedor") from exc

        with Path(args.config).open(encoding="utf-8") as file:
            try:
                config = yaml.safe_load(file)
            except yaml.YAMLError as exc:
                raise OnvifError(f"YAML inválido en {args.config}: {exc}") from exc
        try:
            camera = config["cameras"][args.camera]
        except (KeyError, TypeError) as exc:
            raise OnvifError(f"no se encontró la cámara {args.camera!r} en {args.config}") from exc
        if not isinstance(camera, dict):
            raise OnvifError(f"configuración inválida para la cámara {args.camera!r}")
        for key, default in (("pan_seconds", 1.6), ("tilt_seconds", 0.9), ("speed", 0.8)):
            value = camera.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise OnvifError(f"{key} debe ser un número finito mayor que cero")
        sequential = camera.get("sequential_axes", False) if args.sequential is None else args.sequential
        if not isinstance(sequential, bool):
            raise OnvifError("sequential_axes debe ser true o false")
        if sequential:
            moves = calculate_sequential_fov_moves(args.x, args.y, camera)
        else:
            x, y, duration = calculate_fov_move(args.x, args.y, camera)
            moves = [(x, y, duration)] if duration else []
        print(f"Modo FOV: {'secuencial' if sequential else 'diagonal'}")
        if not moves:
            print("El proxy descartaría este movimiento por duración mínima.")
            return
    else:
        moves = [(args.x, args.y, args.seconds)]
        bounded(args.seconds, 0.03, 5, "duración")
    for x, y, duration in moves:
        bounded(x, -1, 1, "vx")
        bounded(y, -1, 1, "vy")
        if not 0 < duration <= 5:
            raise OnvifError("la duración de cada movimiento debe ser mayor que cero y como máximo 5 segundos")
        if x == 0 and y == 0:
            raise OnvifError("ambas velocidades son cero; no se enviará movimiento")
        print(f"Cálculo: vx={x:+.3f}, vy={y:+.3f}, duración={duration:.3f}s")

    confirm(
        f"Se moverá {args.url} en {len(moves)} paso(s). "
        "Vigila la cámara y pulsa Ctrl+C para intentar detenerla.",
        args.yes,
    )
    print_result("GetStatus antes (la posición puede no ser fiable)", status())

    def move_parameters(command):
        element(command, PTZ, "ProfileToken", args.profile)
        velocity = element(command, PTZ, "Velocity")
        element(velocity, SCHEMA, "PanTilt", x=str(x), y=str(y))

    def stop_parameters(command):
        element(command, PTZ, "ProfileToken", args.profile)
        element(command, PTZ, "PanTilt", "true")
        element(command, PTZ, "Zoom", "false")

    action = "RelativeMove" if args.proxy_action else None
    for x, y, duration in moves:
        try:
            print_result("ContinuousMove", request(args.url, PTZ, "ContinuousMove", move_parameters, action))
            time.sleep(duration)
        finally:
            original_error = sys.exc_info()[1]
            print("Enviando Stop...", flush=True)
            try:
                print_result("Stop", request(args.url, PTZ, "Stop", stop_parameters, action))
            except OnvifError:
                if original_error is not None:
                    print(f"Error anterior durante el movimiento: {original_error!r}", file=sys.stderr)
                raise
    print_result("GetStatus después (la posición puede no ser fiable)", status())
    print("Mide visualmente el desplazamiento sobre el vídeo; no confíes solo en MoveStatus.")


if __name__ == "__main__":
    try:
        main()
    except (OnvifError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        sys.exit(130)
