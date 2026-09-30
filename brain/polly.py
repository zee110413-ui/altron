"""Amazon Polly: the "Maxim" voice (the IVONA voice robot teammates in videos talk with) from Amazon's own service,
for a PC that does not have it installed in Windows.

The keys are the PC's own: the environment (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION) or brain/polly.json
{"access_key": "...", "secret_key": "...", "region": "eu-central-1"} — a file git does not keep."""
import datetime
import hashlib
import hmac
import json
import os
from xml.sax.saxutils import escape

import numpy as np

from launcher import BRAIN_DIR

RATE = 16000                   # Polly's PCM comes at 16 kHz, 16 bit, mono
KEYS_FILE = BRAIN_DIR / "polly.json"


def credentials():
    """(access key, secret key, region) or None when the PC has no keys for Polly."""
    key, secret = os.environ.get("AWS_ACCESS_KEY_ID"), os.environ.get("AWS_SECRET_ACCESS_KEY")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not (key and secret) and KEYS_FILE.exists():
        try:
            d = json.loads(KEYS_FILE.read_text(encoding="utf-8-sig"))   # PowerShell writes a BOM
        except (OSError, ValueError):
            d = {}
        key, secret, region = d.get("access_key"), d.get("secret_key"), region or d.get("region")
    return (key, secret, region or "eu-central-1") if key and secret else None


def _hmac(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sign(method, host, path, headers, body, key, secret, region, service, when):
    """AWS Signature Version 4: the Authorization header for a request (headers: lower-case name -> value,
    host and x-amz-date among them)."""
    amz = when.strftime("%Y%m%dT%H%M%SZ")
    day = amz[:8]
    names = sorted(headers)
    canonical = "\n".join([method, path, "", "".join("%s:%s\n" % (n, headers[n].strip()) for n in names),
                           ";".join(names), hashlib.sha256(body).hexdigest()])
    scope = "%s/%s/%s/aws4_request" % (day, region, service)
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    k = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode("utf-8"), day), region), service), "aws4_request")
    signature = hmac.new(k, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (key, scope, ";".join(names), signature)


def speak(voice, text, rate=100, creds=None):
    """(int16 samples, sample rate) of `text` said by the Polly voice (Maxim); rate — the pace in percent."""
    import httpx
    key, secret, region = creds or credentials()
    host = "polly.%s.amazonaws.com" % region
    ssml = '<speak><prosody rate="%d%%">%s</prosody></speak>' % (max(20, min(200, int(rate))), escape(text))
    body = json.dumps({"Text": ssml, "TextType": "ssml", "VoiceId": voice, "OutputFormat": "pcm",
                       "SampleRate": str(RATE), "Engine": "standard"}).encode("utf-8")
    when = datetime.datetime.now(datetime.timezone.utc)
    headers = {"content-type": "application/json", "host": host, "x-amz-date": when.strftime("%Y%m%dT%H%M%SZ")}
    auth = sign("POST", host, "/v1/speech", headers, body, key, secret, region, "polly", when)
    r = httpx.post("https://%s/v1/speech" % host, content=body, timeout=20,
                   headers={"Content-Type": headers["content-type"], "X-Amz-Date": headers["x-amz-date"],
                            "Authorization": auth})
    if r.status_code != 200:
        raise RuntimeError("Polly %s: %s" % (r.status_code, r.text[:200]))
    return np.frombuffer(r.content, dtype="<i2"), RATE
