#!/usr/bin/env python3

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import base64
import hashlib
import json
import re
import socket
import struct
import ssl
import threading
import time
import urllib.parse


ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "tests" / "m2-site" / "index.html").read_bytes()
HISTORY = (ROOT / "tests" / "m2-site" / "history.html").read_bytes()
HISTORY_TARGET = (
    ROOT / "tests" / "m2-site" / "history-target.html"
).read_bytes()
INTERACTION_BASELINE = (
    ROOT / "tests" / "m2-site" / "interaction-baseline.html"
).read_bytes()
CONTEXT_IMAGE = b"""<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">
<rect width="320" height="180" rx="24" fill="#d3e3fd"/>
<path d="M70 125l54-58 42 42 31-30 53 46z" fill="#0b57d0"/>
<circle cx="230" cy="53" r="19" fill="#fff"/>
</svg>
"""
NAVIS_TEST_FAVICON = b"""<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
<rect width="32" height="32" rx="8" fill="#0b57d0"/>
<path d="M21.5 9.5 18 19l-9.5 3.5L12 13z" fill="#fff"/>
</svg>
"""
UI_LONG_TITLE = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>这是一个用于验证标签截断与地址栏布局的很长中文页面标题 — Navis</title>
<style>
  :root { color-scheme: light dark; font: 16px/1.6 system-ui, sans-serif; }
  body { max-width: 56rem; margin: 5rem auto; padding: 0 2rem; }
  h1 { font-size: clamp(2rem, 5vw, 4rem); line-height: 1.1; }
</style>
<h1>Navis 确定性界面夹具</h1>
<p>这个页面用于验证长中文标题、地址、缩放和窗口尺寸，不依赖互联网内容。</p>
</html>
""".encode("utf-8")
CREDENTIAL_FIXTURE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Navis credential fixture</title>
<style>
  :root { color-scheme: light dark; font: 16px/1.5 system-ui, sans-serif; }
  body { max-width: 28rem; margin: 4rem auto; padding: 0 2rem; }
  form, label { display: grid; gap: .75rem; }
  form { gap: 1rem; }
  input, button { min-height: 2.5rem; font: inherit; }
</style>
<h1>Credential fixture</h1>
<form id="login" action="/credential-submit" method="post">
  <label>Username
    <input id="username" name="username" autocomplete="username">
  </label>
  <label>Password
    <input id="password" name="password" type="password" autocomplete="current-password">
  </label>
  <button id="sign-in" type="submit">Sign in</button>
</form>
<p id="status" role="status">Ready</p>
<script>
document.querySelector('#login').addEventListener('submit', event => {
  event.preventDefault();
  const username = document.querySelector('#username').value;
  document.querySelector('#status').textContent = `Submitted ${username}`;
  fetch('/report', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: 'credential_submission', value: username}),
  });
});
</script>
</html>
""".encode("utf-8")
WEBAUTHN_FIXTURE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Navis WebAuthn fixture</title>
<style>
  :root { color-scheme: light dark; font: 16px/1.5 system-ui, sans-serif; }
  body { max-width: 34rem; margin: 4rem auto; padding: 0 2rem; }
  main { display: grid; gap: 1rem; }
  .actions { display: flex; flex-wrap: wrap; gap: .75rem; }
  button { min-height: 2.5rem; padding: .5rem 1rem; font: inherit; }
  #status { padding: 1rem; border-radius: .75rem; background: color-mix(in srgb, CanvasText 8%, Canvas); }
</style>
<main>
  <h1>Navis WebAuthn fixture</h1>
  <p>Exercise standards-facing passkey registration and assertion.</p>
  <label>Passkey account <input id="passkey-account" autocomplete="username webauthn"></label>
  <div class="actions">
    <button id="create" type="button">Create credential</button>
    <button id="assert" type="button">Get assertion</button>
  </div>
  <p id="status" role="status">Ready</p>
</main>
<script>
const state = {credentialId: null};
const status = document.querySelector('#status');
const challenge = () => crypto.getRandomValues(new Uint8Array(32));
const result = (ok, operation, detail) => {
  const value = {ok, operation, ...detail};
  status.textContent = ok ? `${operation} complete` : `${operation}: ${detail.name}`;
  return value;
};
async function createCredential() {
  status.textContent = 'Waiting for authenticator…';
  try {
    const credential = await navigator.credentials.create({publicKey: {
      challenge: challenge(),
      rp: {id: location.hostname, name: 'Navis WebAuthn fixture'},
      user: {
        id: new Uint8Array([78, 97, 118, 105, 115, 45, 49]),
        name: 'navis-webauthn-user',
        displayName: 'Navis WebAuthn User',
      },
      pubKeyCredParams: [{type: 'public-key', alg: -7}, {type: 'public-key', alg: -257}],
      timeout: 60000,
      authenticatorSelection: {residentKey: 'required', userVerification: 'preferred'},
      attestation: 'none',
    }});
    state.credentialId = new Uint8Array(credential.rawId);
    return result(true, 'registration', {
      type: credential.type,
      idLength: state.credentialId.byteLength,
      clientExtensionResults: credential.getClientExtensionResults(),
    });
  } catch (error) {
    return result(false, 'registration', {name: error.name, message: error.message});
  }
}
async function createCredentialRequiringVerification() {
  status.textContent = 'Waiting for verified user…';
  try {
    const credential = await navigator.credentials.create({publicKey: {
      challenge: challenge(),
      rp: {id: location.hostname, name: 'Navis WebAuthn fixture'},
      user: {
        id: new Uint8Array([78, 97, 118, 105, 115, 45, 85, 86]),
        name: 'navis-user-verification-user',
        displayName: 'Navis User Verification User',
      },
      pubKeyCredParams: [{type: 'public-key', alg: -7}, {type: 'public-key', alg: -257}],
      timeout: 15000,
      authenticatorSelection: {residentKey: 'required', userVerification: 'required'},
      attestation: 'none',
    }});
    return result(true, 'uv-required-registration', {
      type: credential.type,
      idLength: credential.rawId.byteLength,
    });
  } catch (error) {
    return result(false, 'uv-required-registration', {
      name: error.name,
      message: error.message,
    });
  }
}
async function createNativeUsbCredential() {
  status.textContent = 'Waiting for a native security key…';
  try {
    const credential = await navigator.credentials.create({publicKey: {
      challenge: challenge(),
      rp: {id: location.hostname, name: 'Navis WebAuthn fixture'},
      user: {
        id: new Uint8Array([78, 97, 118, 105, 115, 45, 85, 83, 66]),
        name: 'navis-native-usb-user',
        displayName: 'Navis Native USB User',
      },
      pubKeyCredParams: [{type: 'public-key', alg: -7}, {type: 'public-key', alg: -257}],
      timeout: 15000,
      authenticatorSelection: {
        authenticatorAttachment: 'cross-platform',
        residentKey: 'discouraged',
        userVerification: 'discouraged',
      },
      attestation: 'none',
    }});
    return result(true, 'native-usb-registration', {
      type: credential.type,
      idLength: credential.rawId.byteLength,
    });
  } catch (error) {
    return result(false, 'native-usb-registration', {
      name: error.name,
      message: error.message,
    });
  }
}
async function createPhysicalUsbCredential() {
  status.textContent = 'Touch the physical security key to register…';
  try {
    const credential = await navigator.credentials.create({publicKey: {
      challenge: challenge(),
      rp: {id: location.hostname, name: 'Navis WebAuthn fixture'},
      user: {
        id: new Uint8Array([78, 97, 118, 105, 115, 45, 80, 72, 89]),
        name: 'navis-physical-usb-user',
        displayName: 'Navis Physical USB User',
      },
      pubKeyCredParams: [{type: 'public-key', alg: -7}, {type: 'public-key', alg: -257}],
      timeout: 120000,
      authenticatorSelection: {
        authenticatorAttachment: 'cross-platform',
        residentKey: 'discouraged',
        userVerification: 'discouraged',
      },
      attestation: 'none',
    }});
    state.credentialId = new Uint8Array(credential.rawId);
    return result(true, 'physical-usb-registration', {
      type: credential.type,
      idLength: state.credentialId.byteLength,
    });
  } catch (error) {
    return result(false, 'physical-usb-registration', {
      name: error.name,
      message: error.message,
    });
  }
}
async function getPhysicalUsbAssertion() {
  status.textContent = 'Touch the physical security key to assert…';
  if (!state.credentialId) {
    return result(false, 'physical-usb-assertion', {name: 'MissingCredential'});
  }
  try {
    const credential = await navigator.credentials.get({publicKey: {
      challenge: challenge(),
      rpId: location.hostname,
      allowCredentials: [{
        type: 'public-key',
        id: state.credentialId,
        transports: ['usb'],
      }],
      userVerification: 'discouraged',
      timeout: 120000,
    }});
    return result(true, 'physical-usb-assertion', {
      type: credential.type,
      idLength: credential.rawId.byteLength,
      signatureLength: credential.response.signature.byteLength,
    });
  } catch (error) {
    return result(false, 'physical-usb-assertion', {
      name: error.name,
      message: error.message,
    });
  }
}
async function getAssertion() {
  status.textContent = 'Waiting for assertion…';
  if (!state.credentialId) {
    return result(false, 'assertion', {name: 'MissingCredential'});
  }
  try {
    const credential = await navigator.credentials.get({publicKey: {
      challenge: challenge(),
      rpId: location.hostname,
      allowCredentials: [{type: 'public-key', id: state.credentialId}],
      userVerification: 'preferred',
      timeout: 60000,
    }});
    return result(true, 'assertion', {
      type: credential.type,
      idLength: credential.rawId.byteLength,
      signatureLength: credential.response.signature.byteLength,
    });
  } catch (error) {
    return result(false, 'assertion', {name: error.name, message: error.message});
  }
}
async function getMissingCredentialAssertion() {
  status.textContent = 'Checking an unavailable credential…';
  try {
    const credential = await navigator.credentials.get({publicKey: {
      challenge: challenge(),
      rpId: location.hostname,
      allowCredentials: [{type: 'public-key', id: challenge()}],
      userVerification: 'preferred',
      timeout: 15000,
    }});
    return result(true, 'missing-credential-assertion', {
      type: credential.type,
      idLength: credential.rawId.byteLength,
    });
  } catch (error) {
    return result(false, 'missing-credential-assertion', {
      name: error.name,
      message: error.message,
    });
  }
}
async function getConditionalAssertion() {
  status.textContent = 'Waiting for conditional assertion…';
  try {
    const credential = await navigator.credentials.get({
      mediation: 'conditional',
      publicKey: {
        challenge: challenge(),
        rpId: location.hostname,
        userVerification: 'preferred',
        timeout: 60000,
      },
    });
    return result(true, 'conditional-assertion', {
      type: credential.type,
      idLength: credential.rawId.byteLength,
      signatureLength: credential.response.signature.byteLength,
    });
  } catch (error) {
    return result(false, 'conditional-assertion', {
      name: error.name,
      message: error.message,
    });
  }
}
async function createDirectCredential() {
  status.textContent = 'Waiting for direct attestation decision…';
  try {
    const credential = await navigator.credentials.create({publicKey: {
      challenge: challenge(),
      rp: {id: location.hostname, name: 'Navis WebAuthn fixture'},
      user: {
        id: new Uint8Array([78, 97, 118, 105, 115, 45, 50]),
        name: 'navis-direct-attestation-user',
        displayName: 'Navis Direct Attestation User',
      },
      pubKeyCredParams: [{type: 'public-key', alg: -7}, {type: 'public-key', alg: -257}],
      timeout: 60000,
      authenticatorSelection: {residentKey: 'discouraged', userVerification: 'preferred'},
      attestation: 'direct',
    }});
    return result(true, 'direct-registration', {
      type: credential.type,
      idLength: credential.rawId.byteLength,
      attestationLength: credential.response.attestationObject.byteLength,
    });
  } catch (error) {
    return result(false, 'direct-registration', {
      name: error.name,
      message: error.message,
    });
  }
}
window.navisWebAuthn = Object.freeze({
  createCredential,
  createCredentialRequiringVerification,
  createDirectCredential,
  createNativeUsbCredential,
  createPhysicalUsbCredential,
  getAssertion,
  getConditionalAssertion,
  getMissingCredentialAssertion,
  getPhysicalUsbAssertion,
});
document.querySelector('#create').addEventListener('click', createCredential);
document.querySelector('#assert').addEventListener('click', getAssertion);
</script>
</html>
""".encode("utf-8")
STORAGE_WORKER = b"""self.addEventListener("install", event => {
  event.waitUntil(self.skipWaiting());
});
self.addEventListener("activate", event => {
  event.waitUntil(self.clients.claim());
});
"""
RESULTS = {}
RESULTS_LOCK = threading.Lock()
MEDIA_FIXTURES = {}
MEDIA_FIXTURE_SPECS = {
    "/media/av1.mp4": (
        "av1.mp4",
        "video/mp4",
        "6f60b5d15d31c6cc11f02804bdf8f52ae597fe20a0ffa0a66d2881e42ed73869",
    ),
    "/media/h264-aac.mp4": (
        "bipbop_360w_253kbps.mp4",
        "video/mp4",
        "046fb73eaac1952e1d5a60a6faad482c410aafaee5d2c56fcfd47272b8a9d844",
    ),
    "/media/opus.opus": (
        "detodos-short.opus",
        "audio/ogg",
        "c378cc0a78398d95e35947786b72d13d0a4870b96052b5ff0d32eabada0203d3",
    ),
    "/media/vorbis.ogg": (
        "sound.ogg",
        "audio/ogg",
        "755b874b1b8964394abb48211a2c95ce81ebfca69e5765e6724c0155757e6557",
    ),
    "/media/vp9.webm": (
        "vp9-short.webm",
        "video/webm",
        "59974aba0b9fbf7823273300115bdeeb9c49710a03cfd18eea6c3f8c29695c75",
    ),
}
JPEG_XL = base64.b64decode(
    "/woYExAJCAQBAFQASxLFggVRbMf/XyACgC3ZTG2fjKoH"
)
DOWNLOAD_SLOW = (b"Navis slow download fixture\n" * 200000)[: 4 * 1024 * 1024]
DOWNLOAD_FAILURE = (b"Navis failure download fixture\n" * 40000)[: 1024 * 1024]


class ThreadingHTTPServerV6(ThreadingHTTPServer):
    address_family = socket.AF_INET6


class Handler(BaseHTTPRequestHandler):
    def send_download_headers(self, filename, length):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Disposition", f'attachment; filename="{filename}"'
        )
        self.send_header("Content-Length", str(length))
        self.end_headers()

    def send_slow_download(self):
        with RESULTS_LOCK:
            request_count = RESULTS.get("download_slow_requests", 0) + 1
            RESULTS["download_slow_requests"] = request_count
            RESULTS["download_slow_bytes_served"] = 0
        self.send_download_headers("navis-slow.bin", len(DOWNLOAD_SLOW))
        served = 0
        try:
            for offset in range(0, len(DOWNLOAD_SLOW), 65536):
                chunk = DOWNLOAD_SLOW[offset : offset + 65536]
                self.wfile.write(chunk)
                self.wfile.flush()
                served += len(chunk)
                with RESULTS_LOCK:
                    RESULTS["download_slow_bytes_served"] = served
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            with RESULTS_LOCK:
                RESULTS["download_slow_canceled"] = True

    def send_failure_download(self):
        with RESULTS_LOCK:
            request_count = RESULTS.get("download_failure_requests", 0) + 1
            RESULTS["download_failure_requests"] = request_count
            allow_success = RESULTS.get(
                "download_failure_allow_success", False
            )
        self.send_download_headers("navis-failure.bin", len(DOWNLOAD_FAILURE))
        if allow_success:
            self.wfile.write(DOWNLOAD_FAILURE)
            with RESULTS_LOCK:
                RESULTS["download_failure_recovered"] = True
            return

        partial = DOWNLOAD_FAILURE[:131072]
        self.wfile.write(partial)
        self.wfile.flush()
        with RESULTS_LOCK:
            RESULTS["download_failure_bytes_before_disconnect"] = len(partial)
        try:
            self.connection.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_LINGER,
                struct.pack("ii", 1, 0),
            )
        except OSError:
            pass
        self.connection.close()
        self.close_connection = True

    def send_media_fixture(self, body, content_type):
        start = 0
        end = len(body) - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header:
            try:
                unit, requested = range_header.split("=", 1)
                first, last = requested.split("-", 1)
                if unit != "bytes" or "," in requested or not first:
                    raise ValueError("unsupported byte range")
                start = int(first)
                end = int(last) if last else end
                if start < 0 or end < start or start >= len(body):
                    raise ValueError("invalid byte range")
                end = min(end, len(body) - 1)
                status = 206
            except (TypeError, ValueError):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(body)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        payload = body[start : end + 1]
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, payload):
        body = json.dumps(payload, sort_keys=True).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        request = urllib.parse.urlsplit(self.path)
        path = request.path
        query = urllib.parse.parse_qs(request.query)

        if path == "/blocker-fixture":
            stage = query.get("stage", [""])[0]
            if not re.fullmatch(r"[a-z0-9-]{1,48}", stage):
                self.send_error(400, "invalid blocker stage")
                return
            escaped_stage = json.dumps(stage)
            body = f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Navis blocker {stage}</title>
<h1>Built-in content blocker fixture</h1>
<p id="stage">{stage}</p>
<div id="cosmetic-ad" class="ADBAR">Cosmetic filtering sentinel</div>
<script src="/allowed.js?stage={stage}"></script>
<script src="/common/ad.js?stage={stage}"></script>
<script>
const report = name => fetch('/report', {{
  method: 'POST', keepalive: true,
  headers: {{'Content-Type': 'application/json'}},
  body: JSON.stringify({{name, value: true}}),
}});
report({escaped_stage} + '_page_complete');
const cosmeticDeadline = Date.now() + 3000;
const observeCosmeticFilter = () => {{
  if (getComputedStyle(document.getElementById('cosmetic-ad')).display === 'none') {{
    report({escaped_stage} + '_cosmetic_hidden');
  }} else if (Date.now() >= cosmeticDeadline) {{
    report({escaped_stage} + '_cosmetic_visible');
  }} else {{
    setTimeout(observeCosmeticFilter, 50);
  }}
}};
observeCosmeticFilter();
</script>
</html>
""".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/allowed.js", "/common/ad.js"):
            stage = query.get("stage", [""])[0]
            if not re.fullmatch(r"[a-z0-9-]{1,48}", stage):
                self.send_error(400, "invalid blocker stage")
                return
            kind = "allowed" if path == "/allowed.js" else "ad"
            request_key = f"{stage}_{kind}_requests"
            with RESULTS_LOCK:
                RESULTS[request_key] = RESULTS.get(request_key, 0) + 1
            report_name = json.dumps(f"{stage}_{kind}_executed")
            body = (
                "fetch('/report',{method:'POST',keepalive:true,"
                "headers:{'Content-Type':'application/json'},"
                f"body:JSON.stringify({{name:{report_name},value:true}})}});"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/ubo-update-fixture":
            stage = query.get("stage", [""])[0]
            if not re.fullmatch(r"[a-z0-9-]{1,48}", stage):
                self.send_error(400, "invalid uBO update stage")
                return
            escaped_stage = json.dumps(stage)
            body = f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Navis uBO update {stage}</title>
<h1>Built-in filter-list update fixture</h1>
<p id="stage">{stage}</p>
<script src="/allowed.js?stage={stage}"></script>
<script src="/navis-rule-one.js?stage={stage}"></script>
<script src="/navis-rule-two.js?stage={stage}"></script>
<script>
fetch('/report', {{
  method: 'POST', keepalive: true,
  headers: {{'Content-Type': 'application/json'}},
  body: JSON.stringify({{name: {escaped_stage} + '_page_complete', value: true}}),
}});
</script>
</html>
""".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/navis-rule-one.js", "/navis-rule-two.js"):
            stage = query.get("stage", [""])[0]
            if not re.fullmatch(r"[a-z0-9-]{1,48}", stage):
                self.send_error(400, "invalid uBO update stage")
                return
            rule = "one" if path == "/navis-rule-one.js" else "two"
            request_key = f"{stage}_rule_{rule}_requests"
            with RESULTS_LOCK:
                RESULTS[request_key] = RESULTS.get(request_key, 0) + 1
            report_name = json.dumps(f"{stage}_rule_{rule}_executed")
            body = (
                "fetch('/report',{method:'POST',keepalive:true,"
                "headers:{'Content-Type':'application/json'},"
                f"body:JSON.stringify({{name:{report_name},value:true}})}});"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/ubo-update-list.txt":
            with RESULTS_LOCK:
                revision = RESULTS.get("ubo_filter_list_revision", 1)
                if revision not in (1, 2):
                    revision = 1
                RESULTS["ubo_filter_list_requests"] = (
                    RESULTS.get("ubo_filter_list_requests", 0) + 1
                )
                RESULTS["ubo_filter_list_last_served_revision"] = revision
            rule_path = (
                "/navis-rule-one.js" if revision == 1 else "/navis-rule-two.js"
            )
            body = (
                "! Title: Navis deterministic filter-list update fixture\n"
                f"! Revision: {revision}\n"
                "! Expires: 1 hour\n"
                f"{rule_path}$script\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("ETag", f'"navis-ubo-revision-{revision}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/state":
            with RESULTS_LOCK:
                payload = dict(RESULTS)
            self.send_json(payload)
            return

        if path == "/network-geolocation-trap":
            with RESULTS_LOCK:
                RESULTS["network_geolocation_requests"] = (
                    RESULTS.get("network_geolocation_requests", 0) + 1
                )
            self.send_json(
                {
                    "location": {"lat": 1.0, "lng": 2.0},
                    "accuracy": 9999.0,
                }
            )
            return

        if path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(INDEX)))
            self.end_headers()
            self.wfile.write(INDEX)
            return

        if path == "/history":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(HISTORY)))
            self.end_headers()
            self.wfile.write(HISTORY)
            return

        if path == "/history-target":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(HISTORY_TARGET)))
            self.end_headers()
            self.wfile.write(HISTORY_TARGET)
            return

        if path == "/interaction-baseline":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(INTERACTION_BASELINE)))
            self.end_headers()
            self.wfile.write(INTERACTION_BASELINE)
            return

        if path == "/context-image.svg":
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(CONTEXT_IMAGE)))
            self.end_headers()
            self.wfile.write(CONTEXT_IMAGE)
            return

        if path == "/navis-test-favicon.svg":
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(NAVIS_TEST_FAVICON)))
            self.end_headers()
            self.wfile.write(NAVIS_TEST_FAVICON)
            return

        if path == "/ui-long-title":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(UI_LONG_TITLE)))
            self.end_headers()
            self.wfile.write(UI_LONG_TITLE)
            return

        if path == "/credential-fixture":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(CREDENTIAL_FIXTURE)))
            self.end_headers()
            self.wfile.write(CREDENTIAL_FIXTURE)
            return

        if path == "/webauthn":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(WEBAUTHN_FIXTURE)))
            self.end_headers()
            self.wfile.write(WEBAUTHN_FIXTURE)
            return

        if path == "/storage-worker.js":
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Service-Worker-Allowed", "/")
            self.send_header("Content-Length", str(len(STORAGE_WORKER)))
            self.end_headers()
            self.wfile.write(STORAGE_WORKER)
            return

        if path == "/slow":
            prefix = (
                b"<!doctype html><meta charset=utf-8>"
                b"<title>Navis cancellable load</title>"
                b"<h1>Cancellable load</h1>"
            )
            total_bytes = 512 * 1024
            with RESULTS_LOCK:
                RESULTS["slow_started"] = True
                RESULTS.pop("slow_canceled", None)
                RESULTS.pop("slow_completed", None)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(total_bytes))
            self.end_headers()
            try:
                self.wfile.write(prefix)
                self.wfile.flush()
                remaining = total_bytes - len(prefix)
                chunk = b" " * 2048
                while remaining:
                    payload = chunk[:remaining]
                    self.wfile.write(payload)
                    self.wfile.flush()
                    remaining -= len(payload)
                    time.sleep(0.025)
            except OSError:
                with RESULTS_LOCK:
                    RESULTS["slow_canceled"] = True
                return
            with RESULTS_LOCK:
                RESULTS["slow_completed"] = True
            return

        if path == "/popup":
            with RESULTS_LOCK:
                RESULTS["popup_loaded"] = True
            body = (
                b"<!doctype html><meta charset=utf-8>"
                b"<title>Navis popup</title>"
                b"<h1>Popup mapped to an EngineSession</h1>"
                b'<button id="arm-before-unload">Arm popup before-unload</button>'
                b'<button id="before-unload">'
                b"Navigate popup with before-unload</button>"
                b"<script>const report=(name)=>fetch('/report',{method:'POST',"
                b"keepalive:true,"
                b"headers:{'Content-Type':'application/json'},body:JSON.stringify("
                b"{name,value:true})});"
                b"document.querySelector('#arm-before-unload').onclick=()=>{"
                b"addEventListener('beforeunload',event=>{event.preventDefault();"
                b"event.returnValue='';},{once:true});"
                b"report('before_unload_armed');};"
                b"document.querySelector('#before-unload').onclick=()=>{"
                b"report('before_unload_navigation');"
                b"location.href='/prompt-target';};</script>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/prompt-target":
            with RESULTS_LOCK:
                RESULTS["before_unload"] = "Before-unload: left page"
            body = (
                b"<!doctype html><meta charset=utf-8>"
                b"<title>Navis prompt target</title>"
                b"<h1>Before-unload navigation completed</h1>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/auth", "/auth-empty"):
            credentials = b"navis:embedder" if path == "/auth" else b":"
            expected = "Basic " + base64.b64encode(
                credentials
            ).decode("ascii")
            authorization = self.headers.get("Authorization")
            if authorization != expected:
                self.send_response(401)
                self.send_header(
                    "WWW-Authenticate",
                    'Basic realm="Navis embedder empty"'
                    if path == "/auth-empty"
                    else 'Basic realm="Navis embedder"',
                )
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"authorized"
            with RESULTS_LOCK:
                result_key = (
                    "auth_empty_challenge_satisfied"
                    if path == "/auth-empty"
                    else "auth_challenge_satisfied"
                )
                RESULTS[result_key] = True
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/download":
            body = (b"Navis M2 download fixture\n" * 4096)
            with RESULTS_LOCK:
                RESULTS["download_bytes_served"] = len(body)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header(
                "Content-Disposition", 'attachment; filename="navis-m2.txt"'
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/download-slow":
            self.send_slow_download()
            return

        if path == "/download-failure":
            self.send_failure_download()
            return

        if path == "/jpeg-xl":
            self.send_response(200)
            self.send_header("Content-Type", "image/jxl")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(JPEG_XL)))
            self.end_headers()
            self.wfile.write(JPEG_XL)
            return

        if path in MEDIA_FIXTURES:
            body, content_type = MEDIA_FIXTURES[path]
            self.send_media_fixture(body, content_type)
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        self.send_error(404)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/network-geolocation-trap":
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length:
                self.rfile.read(min(content_length, 65536))
            with RESULTS_LOCK:
                RESULTS["network_geolocation_requests"] = (
                    RESULTS.get("network_geolocation_requests", 0) + 1
                )
            self.send_json(
                {
                    "location": {"lat": 1.0, "lng": 2.0},
                    "accuracy": 9999.0,
                }
            )
            return
        if path != "/report":
            self.send_error(404)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length > 4096:
                raise ValueError("report is too large")
            payload = json.loads(self.rfile.read(content_length))
            name = payload["name"]
            value = payload["value"]
            if not isinstance(name, str) or not isinstance(value, (str, bool, int)):
                raise ValueError("invalid report")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.send_error(400, str(error))
            return

        with RESULTS_LOCK:
            RESULTS[name] = value
        self.send_json({"recorded": name})

    def log_message(self, message, *args):
        print(f"{self.client_address[0]} - {message % args}", flush=True)


def main():
    global MEDIA_FIXTURES
    parser = argparse.ArgumentParser(description="Serve the Navis M2 fixture")
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="address to listen on (default: loopback only)",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--tls-cert",
        type=Path,
        help="serve HTTPS with this PEM certificate (requires --tls-key)",
    )
    parser.add_argument(
        "--tls-key",
        type=Path,
        help="serve HTTPS with this PEM private key (requires --tls-cert)",
    )
    parser.add_argument(
        "--media-fixture-root",
        type=Path,
        default=ROOT / "gecko" / "dom" / "media" / "test",
    )
    args = parser.parse_args()
    media_root = args.media_fixture_root.resolve()
    fixtures = {}
    for route, (filename, content_type, expected_sha256) in MEDIA_FIXTURE_SPECS.items():
        source = media_root / filename
        try:
            body = source.read_bytes()
        except OSError as error:
            parser.error(f"media fixture is unavailable: {source}: {error}")
        actual_sha256 = hashlib.sha256(body).hexdigest()
        if actual_sha256 != expected_sha256:
            parser.error(
                f"media fixture digest mismatch: {source}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        fixtures[route] = (body, content_type)
    MEDIA_FIXTURES = fixtures
    server_class = ThreadingHTTPServerV6 if ":" in args.bind else ThreadingHTTPServer
    server = server_class((args.bind, args.port), Handler)
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("--tls-cert and --tls-key must be provided together")
    scheme = "http"
    if args.tls_cert and args.tls_key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            context.load_cert_chain(args.tls_cert, args.tls_key)
        except (OSError, ssl.SSLError) as error:
            parser.error(f"cannot load TLS certificate and key: {error}")
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    server_host = server.server_address[0]
    display_host = f"[{server_host}]" if ":" in server_host else server_host
    print(
        f"Navis M2 fixture: {scheme}://{display_host}:{server.server_port}/",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
