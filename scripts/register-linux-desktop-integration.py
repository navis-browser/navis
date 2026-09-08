#!/usr/bin/env python3
"""Register one portable Navis runtime with the current Linux desktop.

The Gecko/GTK runtime already carries the canonical window icons.  Wayland
desktops identify an application by its XDG app id instead of reading those
window icon pixels, so a portable runtime also needs a matching desktop entry
and hicolor icon mapping.  This helper installs only user-scoped integration;
it neither creates a desktop shortcut nor changes the default browser.
"""

from __future__ import annotations

import argparse
import configparser
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile


ICON_SIZES = (16, 32, 48, 64, 128, 256)
MANAGED_MARKER = "X-Navis-Managed=true"
VALID_DESKTOP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class IntegrationError(RuntimeError):
    """The runtime cannot be registered without risking unrelated files."""


def read_runtime_identity(runtime: Path) -> tuple[Path, str]:
    try:
        runtime = runtime.expanduser().resolve(strict=True)
    except OSError as error:
        raise IntegrationError(f"runtime does not exist: {runtime}") from error
    if not runtime.is_dir():
        raise IntegrationError(f"runtime is not a directory: {runtime}")

    binary = runtime / "navis"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise IntegrationError(f"Navis executable is missing: {binary}")

    parser = configparser.ConfigParser(interpolation=None)
    try:
        with (runtime / "application.ini").open(encoding="utf-8") as stream:
            parser.read_file(stream)
        name = parser.get("App", "Name")
        remoting_name = parser.get("App", "RemotingName")
    except (OSError, configparser.Error, KeyError) as error:
        raise IntegrationError("runtime application.ini has no valid App identity") from error
    if name != "Navis":
        raise IntegrationError(f"runtime product is not Navis: {name!r}")
    if not VALID_DESKTOP_ID.fullmatch(remoting_name):
        raise IntegrationError(f"unsafe Navis desktop id: {remoting_name!r}")
    return binary, remoting_name


def read_icons(runtime: Path) -> dict[int, bytes]:
    icons: dict[int, bytes] = {}
    for size in ICON_SIZES:
        path = runtime / "chrome/icons/default" / f"default{size}.png"
        try:
            data = path.read_bytes()
        except OSError as error:
            raise IntegrationError(f"runtime icon is missing: {path}") from error
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
            raise IntegrationError(f"runtime icon is not a PNG: {path}")
        width, height = struct.unpack(">II", data[16:24])
        if (width, height) != (size, size):
            raise IntegrationError(
                f"runtime icon has size {width}x{height}, expected {size}x{size}: {path}"
            )
        icons[size] = data
    return icons


def quote_exec_argument(value: str) -> str:
    if not value or "\0" in value or "\n" in value or "\r" in value:
        raise IntegrationError("desktop Exec path contains an invalid character")
    # Desktop-entry field codes are expanded even inside quotes.  A literal
    # percent is therefore doubled before the standard quoted escaping.
    escaped = value.replace("%", "%%")
    escaped = escaped.replace("\\", "\\\\")
    for character in ('"', "`", "$"):
        escaped = escaped.replace(character, "\\" + character)
    return f'"{escaped}"'


def desktop_entry(binary: Path, desktop_id: str) -> bytes:
    return (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=Navis\n"
        "GenericName=Web Browser\n"
        "GenericName[zh_CN]=网页浏览器\n"
        "Comment=Browse the Web\n"
        "Comment[zh_CN]=浏览网页\n"
        f"Exec={quote_exec_argument(str(binary))} %u\n"
        "Terminal=false\n"
        "Icon=navis\n"
        f"StartupWMClass={desktop_id}\n"
        "DBusActivatable=false\n"
        "Categories=Network;WebBrowser;\n"
        "MimeType=text/html;text/xml;application/xhtml+xml;application/xml;"
        "x-scheme-handler/http;x-scheme-handler/https;\n"
        "StartupNotify=true\n"
        f"{MANAGED_MARKER}\n"
        f"X-Navis-Runtime={binary.parent}\n"
    ).encode("utf-8")


def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary_name = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def default_data_home() -> Path:
    configured = os.environ.get("XDG_DATA_HOME")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return candidate
    return Path.home() / ".local/share"


def refresh_desktop_caches(data_home: Path) -> list[str]:
    commands = (
        ("update-desktop-database", str(data_home / "applications")),
        ("kbuildsycoca6",),
    )
    warnings: list[str] = []
    for command in commands:
        executable = shutil.which(command[0])
        if executable is None:
            continue
        result = subprocess.run(
            (executable, *command[1:]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.strip() or f"exit status {result.returncode}"
            warnings.append(f"{command[0]}: {detail}")
    return warnings


def install(runtime: Path, data_home: Path, *, refresh: bool = True) -> tuple[Path, list[Path], list[str]]:
    binary, desktop_id = read_runtime_identity(runtime)
    icons = read_icons(binary.parent)
    desktop_path = data_home / "applications" / f"{desktop_id}.desktop"
    desktop_data = desktop_entry(binary, desktop_id)

    if desktop_path.exists():
        try:
            previous = desktop_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise IntegrationError(f"cannot inspect existing desktop entry: {desktop_path}") from error
        if MANAGED_MARKER not in previous:
            raise IntegrationError(
                f"refusing to replace an unmanaged desktop entry: {desktop_path}"
            )

    icon_paths = [
        data_home / "icons/hicolor" / f"{size}x{size}" / "apps/navis.png"
        for size in ICON_SIZES
    ]
    if not desktop_path.exists():
        for path, size in zip(icon_paths, ICON_SIZES, strict=True):
            if path.exists() and path.read_bytes() != icons[size]:
                raise IntegrationError(f"refusing to replace an unmanaged icon: {path}")

    for path, size in zip(icon_paths, ICON_SIZES, strict=True):
        atomic_write(path, icons[size])
    atomic_write(desktop_path, desktop_data)
    warnings = refresh_desktop_caches(data_home) if refresh else []
    return desktop_path, icon_paths, warnings


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--runtime",
        required=True,
        type=Path,
        help="directory containing navis, application.ini and chrome/icons/default",
    )
    parser.add_argument(
        "--data-home",
        type=Path,
        default=default_data_home(),
        help="XDG user data directory (defaults to XDG_DATA_HOME or ~/.local/share)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = argument_parser().parse_args(argv)
    try:
        data_home = arguments.data_home.expanduser().resolve(strict=False)
        desktop_path, icon_paths, warnings = install(arguments.runtime, data_home)
    except IntegrationError as error:
        print(f"Navis desktop integration failed: {error}", file=sys.stderr)
        return 1
    print(f"Registered {desktop_path}")
    print(f"Registered {len(icon_paths)} hicolor Navis icons")
    for warning in warnings:
        print(f"Desktop cache refresh warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
