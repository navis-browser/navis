#!/usr/bin/env python3

"""Drive a packaged Navis process through Gecko's direct WebDriver BiDi API."""

from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import pathlib
import socket
import sys
import time
from typing import Any


WORKSPACE = pathlib.Path(__file__).resolve().parent.parent
VENDORED_WEBSOCKETS = (
    WORKSPACE
    / "gecko"
    / "testing"
    / "web-platform"
    / "tests"
    / "tools"
    / "third_party"
    / "websockets"
    / "src"
)
MAX_SESSION_MESSAGE = 1024 * 1024


class WebDriverError(RuntimeError):
    def __init__(self, response: dict[str, Any]):
        self.response = response
        super().__init__(
            f"{response.get('error', 'WebDriver error')}: "
            f"{response.get('message', response)!r}"
        )


def import_connect():
    if VENDORED_WEBSOCKETS.is_dir():
        sys.path.insert(0, str(VENDORED_WEBSOCKETS))
    try:
        from websockets.sync.client import connect
    except ImportError as error:
        raise SystemExit(
            "The WebDriver client needs Gecko's vendored Python websockets "
            f"package at {VENDORED_WEBSOCKETS}"
        ) from error
    return connect


def wait_for_server(profile: pathlib.Path, timeout: float) -> str:
    server_file = profile / "WebDriverBiDiServer.json"
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            payload = json.loads(server_file.read_text(encoding="utf-8"))
            host = payload["ws_host"]
            port = int(payload["ws_port"])
            if host in {"127.0.0.1", "::1", "localhost"} and 0 < port < 65536:
                formatted_host = f"[{host}]" if ":" in host else host
                return f"ws://{formatted_host}:{port}/session"
            raise ValueError(f"unsafe WebDriver listener: {host}:{port}")
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError) as error:
            last_error = error
            time.sleep(0.05)
    raise TimeoutError(
        f"WebDriver server did not become ready via {server_file}: {last_error}"
    )


def wait_for_marionette(profile: pathlib.Path, timeout: float) -> int:
    port_file = profile / "MarionetteActivePort"
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            port = int(port_file.read_text(encoding="utf-8").strip())
            if 0 < port < 65536:
                return port
            raise ValueError(f"unsafe Marionette port: {port}")
        except (FileNotFoundError, UnicodeError, ValueError) as error:
            last_error = error
            time.sleep(0.05)
    raise TimeoutError(
        f"Marionette did not become ready via {port_file}: {last_error}"
    )


class MarionetteClient:
    """Minimal WebDriver Classic transport for bounded content automation."""

    def __init__(self, port: int, timeout: float):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout)
        self.socket.settimeout(timeout)
        self.next_id = 1
        self.session_started = False
        hello = self._receive()
        if (
            not isinstance(hello, dict)
            or hello.get("applicationType") != "gecko"
            or not isinstance(hello.get("marionetteProtocol"), int)
            or hello["marionetteProtocol"] < 3
        ):
            raise RuntimeError(f"invalid Marionette greeting: {hello!r}")

    def close(self) -> None:
        try:
            self.socket.close()
        except OSError:
            pass

    def _receive_exact(self, length: int) -> bytes:
        chunks: list[bytes] = []
        remaining = length
        while remaining:
            chunk = self.socket.recv(remaining)
            if not chunk:
                raise OSError("Marionette closed the connection mid-packet")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _receive(self) -> Any:
        length_bytes = bytearray()
        while True:
            character = self.socket.recv(1)
            if not character:
                raise OSError("Marionette closed the connection")
            if character == b":":
                break
            if not character.isdigit() or len(length_bytes) >= 10:
                raise RuntimeError(
                    f"invalid Marionette packet length: "
                    f"{bytes(length_bytes + character)!r}"
                )
            length_bytes.extend(character)
        if not length_bytes:
            raise RuntimeError("empty Marionette packet length")
        length = int(length_bytes)
        if not 0 < length <= 2**32 - 1:
            raise RuntimeError(f"unsafe Marionette packet length: {length}")
        return json.loads(self._receive_exact(length))

    def request(self, name: str, params: dict[str, Any]) -> tuple[Any, Any]:
        command_id = self.next_id
        self.next_id += 1
        packet = json.dumps(
            [0, command_id, name, params], separators=(",", ":")
        ).encode("utf-8")
        self.socket.sendall(str(len(packet)).encode("ascii") + b":" + packet)
        response = self._receive()
        if (
            not isinstance(response, list)
            or len(response) != 4
            or response[0] != 1
            or response[1] != command_id
        ):
            raise RuntimeError(f"invalid Marionette response: {response!r}")
        return response[2], response[3]

    def start_session(self, capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
        parameters: dict[str, Any] = {"strictFileInteractability": True}
        if capabilities:
            parameters.update(capabilities)
        error, result = self.request("WebDriver:NewSession", parameters)
        if error is not None:
            raise RuntimeError(f"Marionette session creation failed: {error!r}")
        if not isinstance(result, dict) or not result.get("sessionId"):
            raise RuntimeError(f"invalid Marionette session result: {result!r}")
        self.session_started = True
        return result

    def end_session(self) -> None:
        if not self.session_started:
            return
        try:
            error, _ = self.request("WebDriver:DeleteSession", {})
            if error is not None:
                raise RuntimeError(f"Marionette session cleanup failed: {error!r}")
        finally:
            self.session_started = False


class BiDiClient:
    def __init__(self, url: str, timeout: float):
        connect = import_connect()
        self.timeout = timeout
        self.socket = connect(
            url,
            compression=None,
            open_timeout=timeout,
            close_timeout=2,
        )
        self.next_id = 1
        self.session_started = False

    def close(self) -> None:
        try:
            self.socket.close()
        except Exception:
            pass

    def command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        allow_error: bool = False,
    ) -> dict[str, Any]:
        command_id = self.next_id
        self.next_id += 1
        self.socket.send(
            json.dumps(
                {
                    "id": command_id,
                    "method": method,
                    "params": params or {},
                },
                separators=(",", ":"),
            )
        )
        while True:
            message = self.socket.recv(timeout=self.timeout)
            if isinstance(message, bytes):
                message = message.decode("utf-8")
            response = json.loads(message)
            if response.get("id") != command_id:
                continue
            if response.get("type") == "error" and not allow_error:
                raise WebDriverError(response)
            return response

    def start_session(self, *, accept_insecure_certs: bool = False) -> dict[str, Any]:
        response = self.command(
            "session.new",
            {
                "capabilities": {
                    "alwaysMatch": {
                        "acceptInsecureCerts": accept_insecure_certs,
                    }
                }
            },
        )
        self.session_started = True
        return response["result"]

    def end_session(self) -> None:
        if not self.session_started:
            return
        try:
            self.command("session.end")
        finally:
            self.session_started = False

    def context_tree(self, max_depth: int = 0) -> list[dict[str, Any]]:
        response = self.command(
            "browsingContext.getTree", {"maxDepth": max_depth}
        )
        return response["result"]["contexts"]

    def top_contexts(self) -> list[dict[str, Any]]:
        return self.context_tree()

    def all_contexts(self) -> list[dict[str, Any]]:
        response = self.command("browsingContext.getTree", {"maxDepth": 16})
        pending = list(response["result"]["contexts"])
        contexts: list[dict[str, Any]] = []
        while pending:
            context = pending.pop(0)
            contexts.append(context)
            children = context.get("children")
            if isinstance(children, list):
                pending[0:0] = children
        return contexts

    def resolve_context(self, context: str | None) -> str:
        if context:
            return context
        contexts = self.top_contexts()
        if not contexts:
            raise RuntimeError("Navis exposed no top-level WebDriver context")
        return contexts[0]["context"]


def remote_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    kind = value.get("type")
    if kind in {"null", "undefined"}:
        return None
    if kind in {"boolean", "string", "number", "bigint"}:
        return value.get("value")
    if kind == "array":
        return [remote_value(item) for item in value.get("value", [])]
    if kind in {"object", "map"}:
        return {
            str(remote_value(key)): remote_value(item)
            for key, item in value.get("value", [])
        }
    return value


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def parse_json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid JSON: {value}: {error}") from error


def receive_session_message(connection: socket.socket) -> dict[str, Any]:
    payload = bytearray()
    while len(payload) <= MAX_SESSION_MESSAGE:
        chunk = connection.recv(min(65536, MAX_SESSION_MESSAGE + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if b"\n" in chunk:
            break
    if len(payload) > MAX_SESSION_MESSAGE:
        raise RuntimeError("WebDriver session message exceeds the size limit")
    line, separator, remainder = bytes(payload).partition(b"\n")
    if not separator or remainder:
        raise RuntimeError("invalid WebDriver session message framing")
    message = json.loads(line)
    if not isinstance(message, dict):
        raise RuntimeError("WebDriver session message is not an object")
    return message


def send_session_message(connection: socket.socket, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > MAX_SESSION_MESSAGE:
        raise RuntimeError("WebDriver session message exceeds the size limit")
    connection.sendall(encoded + b"\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=pathlib.Path, required=True)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--session-socket", type=pathlib.Path)
    parser.add_argument(
        "--accept-insecure-certs",
        action="store_true",
        help="opt in to the WebDriver certificate override for this session",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("session-server")
    subparsers.add_parser("wait")
    tree = subparsers.add_parser("tree")
    tree.add_argument("--max-depth", type=int, choices=range(17), default=0)
    subparsers.add_parser("context")

    wait_context = subparsers.add_parser("wait-context")
    wait_context.add_argument("--url-prefix", default="")
    wait_context.add_argument("--url-contains", default="")
    wait_context.add_argument("--url-suffix", default="")
    wait_context.add_argument(
        "--field", choices=("context", "url"), default="context"
    )

    navigate = subparsers.add_parser("navigate")
    navigate.add_argument("url")
    navigate.add_argument("--context")
    navigate.add_argument(
        "--wait", choices=("none", "interactive", "complete"), default="complete"
    )

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("expression")
    evaluate.add_argument("--context")
    evaluate.add_argument("--await-promise", action="store_true")

    wait_evaluate = subparsers.add_parser("wait-evaluate")
    wait_evaluate.add_argument("expression")
    wait_evaluate.add_argument("expected_json")
    wait_evaluate.add_argument("--context")
    wait_evaluate.add_argument("--poll", type=float, default=0.05)

    create = subparsers.add_parser("create")
    create.add_argument("--type", choices=("tab", "window"), default="tab")
    create.add_argument("--reference-context")
    create.add_argument("--background", action="store_true")
    create.add_argument("--url")

    for name in ("activate", "close-context"):
        operation = subparsers.add_parser(name)
        operation.add_argument("context")

    traverse = subparsers.add_parser("traverse")
    traverse.add_argument("delta", type=int)
    traverse.add_argument("--context")

    viewport = subparsers.add_parser("set-viewport")
    viewport.add_argument("width", type=int)
    viewport.add_argument("height", type=int)
    viewport.add_argument("--context")

    screenshot = subparsers.add_parser("screenshot")
    screenshot.add_argument("output", type=pathlib.Path)
    screenshot.add_argument("--context")
    screenshot.add_argument("--origin", choices=("viewport", "document"), default="viewport")

    actions = subparsers.add_parser("actions")
    actions.add_argument("actions_json")
    actions.add_argument("--context")

    for name in ("click", "context-click"):
        click_element = subparsers.add_parser(name)
        click_element.add_argument("selector")
        click_element.add_argument("--context")
        click_element.add_argument(
            "--frame-selector",
            help="locate the target in one same-origin child frame",
        )
        click_element.add_argument(
            "--x-position", choices=("start", "center", "end"), default="center"
        )
        click_element.add_argument(
            "--y-position", choices=("start", "center", "end"), default="center"
        )

    click_point = subparsers.add_parser("click-point")
    click_point.add_argument("x", type=int)
    click_point.add_argument("y", type=int)
    click_point.add_argument("--context")

    type_text = subparsers.add_parser("type")
    type_text.add_argument("text")
    type_text.add_argument("--context")
    type_text.add_argument("--replace", action="store_true")

    raw = subparsers.add_parser("raw")
    raw.add_argument("method")
    raw.add_argument("params_json", nargs="?", default="{}")
    raw.add_argument("--expect-error")

    classic_raw = subparsers.add_parser("classic-raw")
    classic_raw.add_argument("command")
    classic_raw.add_argument("params_json", nargs="?", default="{}")
    classic_raw.add_argument("--expect-error")

    subparsers.add_parser("close-browser")
    return parser


def evaluate(client: BiDiClient, context: str, expression: str, await_promise: bool) -> Any:
    response = client.command(
        "script.evaluate",
        {
            "expression": expression,
            "target": {"context": context},
            "awaitPromise": await_promise,
            "resultOwnership": "none",
        },
    )["result"]
    if response.get("type") == "exception":
        raise RuntimeError(f"WebDriver script exception: {response}")
    return remote_value(response["result"])


def run_classic_raw(args: argparse.Namespace) -> int:
    port = wait_for_marionette(args.profile, args.timeout)
    client = MarionetteClient(port, args.timeout)
    try:
        client.start_session()
        error, result = client.request(args.command, parse_json(args.params_json))
        if args.expect_error is None:
            if error is not None:
                raise RuntimeError(f"{args.command} failed: {error!r}")
        else:
            if not isinstance(error, dict):
                raise RuntimeError(
                    f"{args.command} unexpectedly succeeded: {result!r}"
                )
            if error.get("error") != args.expect_error:
                raise RuntimeError(
                    f"{args.command} returned {error.get('error')!r}, "
                    f"expected {args.expect_error!r}: {error!r}"
                )
        print_json({"command": args.command, "error": error, "result": result})
    finally:
        try:
            client.end_session()
        finally:
            client.close()
    return 0


def run_bidi_action(args: argparse.Namespace, client: BiDiClient) -> bool:
    browser_closing = False
    if args.action == "wait":
        print("ready")
    elif args.action == "tree":
        print_json(client.context_tree(args.max_depth))
    elif args.action == "context":
        print(client.resolve_context(None))
    elif args.action == "wait-context":
        deadline = time.monotonic() + args.timeout
        last_urls: list[str] = []
        while time.monotonic() < deadline:
            matches = []
            contexts = client.all_contexts()
            last_urls = [
                context.get("url", "")
                for context in contexts
                if isinstance(context.get("url"), str)
            ]
            for context in contexts:
                url = context.get("url")
                if not isinstance(url, str):
                    continue
                if args.url_prefix and not url.startswith(args.url_prefix):
                    continue
                if args.url_contains and args.url_contains not in url:
                    continue
                if args.url_suffix and not url.endswith(args.url_suffix):
                    continue
                matches.append(context)
            if len(matches) == 1:
                print(matches[0][args.field])
                break
            if len(matches) > 1:
                raise RuntimeError(
                    "WebDriver context selector is ambiguous: "
                    f"{[match.get('url') for match in matches]!r}"
                )
            time.sleep(0.05)
        else:
            raise TimeoutError(
                "WebDriver context did not appear for "
                f"prefix={args.url_prefix!r}, "
                f"contains={args.url_contains!r}, "
                f"suffix={args.url_suffix!r}; "
                f"last URLs={last_urls!r}"
            )
    elif args.action == "navigate":
        context = client.resolve_context(args.context)
        result = client.command(
            "browsingContext.navigate",
            {"context": context, "url": args.url, "wait": args.wait},
        )["result"]
        print_json(result)
    elif args.action == "evaluate":
        context = client.resolve_context(args.context)
        print_json(evaluate(client, context, args.expression, args.await_promise))
    elif args.action == "wait-evaluate":
        context = client.resolve_context(args.context)
        expected = parse_json(args.expected_json)
        deadline = time.monotonic() + args.timeout
        last: Any = None
        while time.monotonic() < deadline:
            last = evaluate(client, context, args.expression, False)
            if last == expected:
                print_json(last)
                break
            time.sleep(args.poll)
        else:
            raise TimeoutError(
                f"WebDriver expression did not become {expected!r}; last={last!r}"
            )
    elif args.action == "create":
        params: dict[str, Any] = {
            "type": args.type,
            "background": args.background,
        }
        if args.reference_context:
            params["referenceContext"] = args.reference_context
        result = client.command("browsingContext.create", params)["result"]
        context = result["context"]
        if args.url:
            client.command(
                "browsingContext.navigate",
                {"context": context, "url": args.url, "wait": "complete"},
            )
        print(context)
    elif args.action == "activate":
        client.command("browsingContext.activate", {"context": args.context})
        print(args.context)
    elif args.action == "close-context":
        client.command("browsingContext.close", {"context": args.context})
        print(args.context)
    elif args.action == "traverse":
        context = client.resolve_context(args.context)
        client.command(
            "browsingContext.traverseHistory",
            {"context": context, "delta": args.delta},
        )
        print(context)
    elif args.action == "set-viewport":
        context = client.resolve_context(args.context)
        client.command(
            "browsingContext.setViewport",
            {
                "context": context,
                "viewport": {"width": args.width, "height": args.height},
            },
        )
        print(context)
    elif args.action == "screenshot":
        context = client.resolve_context(args.context)
        result = client.command(
            "browsingContext.captureScreenshot",
            {"context": context, "origin": args.origin},
        )["result"]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(base64.b64decode(result["data"], validate=True))
        print(args.output)
    elif args.action == "actions":
        context = client.resolve_context(args.context)
        client.command(
            "input.performActions",
            {"context": context, "actions": parse_json(args.actions_json)},
        )
        print(context)
    elif args.action == "click-point":
        context = client.resolve_context(args.context)
        if args.x < 0 or args.y < 0:
            raise RuntimeError(f"invalid pointer coordinates: {args.x},{args.y}")
        client.command(
            "input.performActions",
            {
                "context": context,
                "actions": [
                    {
                        "type": "pointer",
                        "id": "navis-pointer",
                        "parameters": {"pointerType": "mouse"},
                        "actions": [
                            {
                                "type": "pointerMove",
                                "x": args.x,
                                "y": args.y,
                                "duration": 0,
                                "origin": "viewport",
                            },
                            {"type": "pointerDown", "button": 0},
                            {"type": "pointerUp", "button": 0},
                        ],
                    }
                ],
            },
        )
        print(context)
    elif args.action in {"click", "context-click"}:
        context = client.resolve_context(args.context)
        selector = json.dumps(args.selector)
        frame_selector = json.dumps(args.frame_selector)
        x_position = json.dumps(args.x_position)
        y_position = json.dumps(args.y_position)
        point = evaluate(
            client,
            context,
            "(() => {"
            f"const selector={selector};"
            f"const frameSelector={frame_selector};"
            "let targetDocument=document;"
            "let offsetX=0;"
            "let offsetY=0;"
            "if(frameSelector!==null){"
            "const frame=document.querySelector(frameSelector);"
            "if(!frame) throw new Error('frame selector did not match');"
            "if(!frame.contentDocument) throw new Error('frame is not same-origin');"
            "const frameRect=frame.getBoundingClientRect();"
            "if(!frameRect.width||!frameRect.height) throw new Error('frame has no bounds');"
            "offsetX=frameRect.left+frame.clientLeft;"
            "offsetY=frameRect.top+frame.clientTop;"
            "targetDocument=frame.contentDocument;"
            "}"
            "const element=targetDocument.querySelector(selector);"
            "if (!element) throw new Error('selector did not match');"
            "element.scrollIntoView({block:'center',inline:'center'});"
            "const rect = element.getBoundingClientRect();"
            "if (!rect.width || !rect.height) throw new Error('element has no bounds');"
            "const xInset=Math.min(8,rect.width/4);"
            "const yInset=Math.min(8,rect.height/4);"
            f"const xPosition={x_position};"
            f"const yPosition={y_position};"
            "const x=xPosition==='start'?rect.left+xInset:"
            "xPosition==='end'?rect.right-xInset:rect.left+rect.width/2;"
            "const y=yPosition==='start'?rect.top+yInset:"
            "yPosition==='end'?rect.bottom-yInset:rect.top+rect.height/2;"
            "return {x:Math.floor(offsetX+x),y:Math.floor(offsetY+y)};"
            "})()",
            False,
        )
        if (
            not isinstance(point, dict)
            or not isinstance(point.get("x"), (int, float))
            or not isinstance(point.get("y"), (int, float))
        ):
            raise RuntimeError(f"invalid element click point: {point!r}")
        client.command(
            "input.performActions",
            {
                "context": context,
                "actions": [
                    {
                        "type": "pointer",
                        "id": "navis-pointer",
                        "parameters": {"pointerType": "mouse"},
                        "actions": [
                            {
                                "type": "pointerMove",
                                "x": int(point["x"]),
                                "y": int(point["y"]),
                                "duration": 0,
                                "origin": "viewport",
                            },
                            {
                                "type": "pointerDown",
                                "button": 2 if args.action == "context-click" else 0,
                            },
                            {
                                "type": "pointerUp",
                                "button": 2 if args.action == "context-click" else 0,
                            },
                        ],
                    }
                ],
            },
        )
        print(context)
    elif args.action == "type":
        context = client.resolve_context(args.context)
        key_actions: list[dict[str, str]] = []
        if args.replace:
            control = "\ue009"
            key_actions.extend(
                [
                    {"type": "keyDown", "value": control},
                    {"type": "keyDown", "value": "a"},
                    {"type": "keyUp", "value": "a"},
                    {"type": "keyUp", "value": control},
                ]
            )
        for character in args.text:
            key_actions.extend(
                [
                    {"type": "keyDown", "value": character},
                    {"type": "keyUp", "value": character},
                ]
            )
        client.command(
            "input.performActions",
            {
                "context": context,
                "actions": [
                    {"type": "key", "id": "navis-keyboard", "actions": key_actions}
                ],
            },
        )
        print(context)
    elif args.action == "raw":
        response = client.command(
            args.method,
            parse_json(args.params_json),
            allow_error=args.expect_error is not None,
        )
        if args.expect_error is not None:
            if response.get("type") != "error":
                raise RuntimeError(f"{args.method} unexpectedly succeeded: {response}")
            if response.get("error") != args.expect_error:
                raise RuntimeError(
                    f"{args.method} returned {response.get('error')!r}, "
                    f"expected {args.expect_error!r}"
                )
        print_json(response)
    elif args.action == "close-browser":
        client.command("browser.close")
        browser_closing = True
        client.session_started = False
        print("closed")
    else:
        raise AssertionError(args.action)
    return browser_closing


def request_arguments(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in vars(args).items():
        if key in {"profile", "session_socket"}:
            continue
        payload[key] = str(value) if isinstance(value, pathlib.Path) else value
    return payload


def request_session(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # The session server runs the requested action with ``args.timeout``.
        # Leave a bounded transport margin so it can serialize a timeout
        # result instead of racing the client socket deadline and dying on a
        # broken pipe before the caller can collect the last observed state.
        connection.settimeout(args.timeout + 5.0)
        try:
            connection.connect(str(args.session_socket))
            send_session_message(connection, request_arguments(args))
            response = receive_session_message(connection)
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error", "session request failed")))
            output = response.get("stdout", "")
            if not isinstance(output, str):
                raise RuntimeError("WebDriver session returned invalid output")
            sys.stdout.write(output)
            return 0
        except (FileNotFoundError, ConnectionRefusedError) as error:
            last_error = error
            time.sleep(0.05)
        finally:
            connection.close()
    raise TimeoutError(
        f"WebDriver session server did not become ready at "
        f"{args.session_socket}: {last_error}"
    )


def session_request_namespace(
    payload: dict[str, Any], profile: pathlib.Path, session_socket: pathlib.Path
) -> argparse.Namespace:
    action = payload.get("action")
    if action in {None, "session-server", "classic-raw"}:
        raise RuntimeError(f"unsupported persistent WebDriver action: {action!r}")
    payload["profile"] = profile
    payload["session_socket"] = session_socket
    if action == "screenshot":
        payload["output"] = pathlib.Path(payload["output"])
    return argparse.Namespace(**payload)


def run_session_server(args: argparse.Namespace) -> int:
    if args.session_socket is None:
        raise RuntimeError("session-server requires --session-socket")
    session_socket = args.session_socket.resolve()
    if session_socket.exists():
        raise RuntimeError(f"WebDriver session socket already exists: {session_socket}")
    if not session_socket.parent.is_dir():
        raise RuntimeError(
            f"WebDriver session socket directory is missing: {session_socket.parent}"
        )

    url = wait_for_server(args.profile, args.timeout)
    client = BiDiClient(url, args.timeout)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    socket_bound = False
    browser_closing = False
    try:
        client.start_session(accept_insecure_certs=args.accept_insecure_certs)
        listener.bind(str(session_socket))
        socket_bound = True
        session_socket.chmod(0o600)
        listener.listen(4)
        while not browser_closing:
            connection, _ = listener.accept()
            with connection:
                try:
                    payload = receive_session_message(connection)
                    request_args = session_request_namespace(
                        payload, args.profile, session_socket
                    )
                    client.timeout = request_args.timeout
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        browser_closing = run_bidi_action(request_args, client)
                    response = {"ok": True, "stdout": output.getvalue()}
                except Exception as error:
                    response = {
                        "ok": False,
                        "error": f"{type(error).__name__}: {error}",
                    }
                send_session_message(connection, response)
    finally:
        if not browser_closing:
            try:
                client.end_session()
            finally:
                client.close()
        else:
            client.close()
        listener.close()
        if socket_bound:
            session_socket.unlink(missing_ok=True)
    return 0


def run(args: argparse.Namespace) -> int:
    if args.action == "classic-raw":
        return run_classic_raw(args)
    if args.action == "session-server":
        return run_session_server(args)
    if args.session_socket is not None:
        return request_session(args)

    url = wait_for_server(args.profile, args.timeout)
    if args.action == "wait":
        print(url)
        return 0

    client = BiDiClient(url, args.timeout)
    browser_closing = False
    try:
        client.start_session(accept_insecure_certs=args.accept_insecure_certs)
        browser_closing = run_bidi_action(args, client)
    finally:
        if not browser_closing:
            try:
                client.end_session()
            finally:
                client.close()
        else:
            client.close()
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except (OSError, RuntimeError, TimeoutError, WebDriverError) as error:
        print(f"Navis WebDriver failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
