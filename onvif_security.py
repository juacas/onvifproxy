import base64
import hashlib
import secrets
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
TOKEN_PROFILE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0"
BASE64_ENCODING = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"


def username_security(username: str, password: str) -> ET.Element:
    nonce = secrets.token_bytes(16)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
    security = ET.Element(f"{{{WSSE}}}Security")
    token = ET.SubElement(security, f"{{{WSSE}}}UsernameToken")
    ET.SubElement(token, f"{{{WSSE}}}Username").text = username
    ET.SubElement(token, f"{{{WSSE}}}Password", {"Type": f"{TOKEN_PROFILE}#PasswordDigest"}).text = digest
    ET.SubElement(token, f"{{{WSSE}}}Nonce", {"EncodingType": BASE64_ENCODING}).text = base64.b64encode(nonce).decode()
    ET.SubElement(token, f"{{{WSU}}}Created").text = created
    return security
