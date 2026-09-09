#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


"""Reject WebAuthn API, prompt-boundary, policy and package drift."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent.parent
SEMANTIC_PORT = (
    WORKSPACE
    / "../runtime/patches/gecko/0030-route-desktop-embedder-webauthn-related-origin.patch"
)
VIRTUAL_AUTHENTICATOR_PORT = (
    WORKSPACE
    / "../runtime/patches/gecko/0032-route-webdriver-virtual-authenticators.patch"
)
WINDOWS_LIFETIME_TIMEOUT_PORT = (
    WORKSPACE
    / "../runtime/patches/gecko/0034-enforce-windows-webauthn-transaction-timeout.patch"
)
ACTIVE_VIRTUAL_ROUTING_PORT = (
    WORKSPACE
    / "../runtime/patches/gecko/0035-route-active-webdriver-virtual-authenticators.patch"
)


def require(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def read(relative: str, failures: list[str]) -> str:
    try:
        return (WORKSPACE / relative).read_text(encoding="utf-8")
    except OSError as error:
        failures.append(f"cannot read {relative}: {error}")
        return ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_markers(
    failures: list[str], source: str, name: str, markers: tuple[str, ...]
) -> None:
    for marker in markers:
        require(failures, marker in source, f"{name} lacks {marker}")


def verify_source() -> list[str]:
    failures: list[str] = []
    binding = read("../runtime/embedder/components/DesktopWebAuthnPrompt.sys.mjs", failures)
    credential_child = read(
        "../runtime/embedder/components/DesktopCredentialChild.sys.mjs", failures
    )
    credential_parent = read(
        "../runtime/embedder/components/DesktopCredentialParent.sys.mjs", failures
    )
    startup = read("../runtime/embedder/components/DesktopEmbedderStartup.sys.mjs", failures)
    build = read("../runtime/embedder/components/moz.build", failures)
    engine = read("../runtime/embedder/modules/DesktopEngine.sys.mjs", failures)
    fullscreen = read("../runtime/embedder/components/DesktopFullscreenParent.sys.mjs", failures)
    platform = read("../platform/gecko-chrome/chrome/content/main.mjs", failures)
    xhtml = read("../platform/gecko-chrome/chrome/content/main.xhtml", failures)
    en_us = read("../platform/gecko-chrome/chrome/content/locales/en-US.mjs", failures)
    zh_cn = read("../platform/gecko-chrome/chrome/content/locales/zh-CN.mjs", failures)
    prefs = read("../platform/gecko-chrome/app/profile/navis.js", failures)
    driver = read("../runtime/gecko/remote/marionette/driver.sys.mjs", failures)
    related_origin = read(
        "../runtime/gecko/dom/webauthn/WebAuthnRelatedOriginFetcher.sys.mjs", failures
    )
    authrs = read("../runtime/gecko/dom/webauthn/authrs_bridge/src/lib.rs", failures)
    service_header = read("../runtime/gecko/dom/webauthn/WebAuthnService.h", failures)
    service = read("../runtime/gecko/dom/webauthn/WebAuthnService.cpp", failures)
    windows_service = read(
        "../runtime/gecko/dom/webauthn/WinWebAuthnService.cpp", failures
    )
    windows_service_header = read(
        "../runtime/gecko/dom/webauthn/WinWebAuthnService.h", failures
    )
    linux_monitor = read(
        "../runtime/gecko/third_party/rust/authenticator/src/transport/linux/monitor.rs",
        failures,
    )
    libudev = read("../runtime/gecko/dom/webauthn/libudev-sys/src/lib.rs", failures)
    test_token = read(
        "../runtime/gecko/dom/webauthn/authrs_bridge/src/test_token.rs", failures
    )

    try:
        capabilities = json.loads(
            read("config/runtime-capabilities.json", failures) or "{}"
        )
        webauthn = next(
            item
            for item in capabilities.get("capabilities", [])
            if item.get("id") == "webauthn"
        )
    except (json.JSONDecodeError, StopIteration):
        failures.append("runtime capability ledger has no WebAuthn row")
    else:
        required = webauthn.get("checks", {}).get("graph_required", [])
        require(
            failures,
            "^dom/webauthn/" in required,
            "WebAuthn source graph is not required by capability policy",
        )

    require_markers(
        failures,
        binding,
        "WebAuthn observer binding",
        (
            'const WEB_AUTHN_TOPIC = "webauthn-prompt"',
            "const MAX_ORIGIN_LENGTH = 4096",
            "const MAX_ACCOUNT_COUNT = 32",
            "const MAX_ACCOUNT_TEXT_LENGTH = 256",
            '"attestation-consent"',
            '"pin-required"',
            '"select-sign-result"',
            "new WeakRef(browser)",
            "webAuthnService().cancel(tid)",
            "browserForContext(prompt.browsingContextId)",
            "Object.freeze({ name, displayName })",
        ),
    )
    for forbidden in ("credentialId", "deviceId", "rawId", "principal"):
        require(
            failures,
            forbidden not in binding,
            f"WebAuthn observer projects forbidden native field {forbidden}",
        )
    require_markers(
        failures,
        startup + build,
        "WebAuthn startup/package binding",
        (
            "initializeDesktopWebAuthnPrompt",
            "shutdownDesktopWebAuthnPrompt",
            '"DesktopWebAuthnPrompt.sys.mjs"',
        ),
    )

    require_markers(
        failures,
        engine,
        "WebAuthn Core contract",
        (
            "webAuthn: true",
            '"webAuthn"',
            "setWebAuthnDelegate(delegate)",
            '"WebAuthnDelegate"',
            "authenticatedWebAuthnOrigin",
            'uri.schemeIs("https")',
            'uri.schemeIs("http") && isLoopbackHost(uri.asciiHost)',
            "principal.originNoSuffix === origin",
            'category: "webauthn"',
            "Object.freeze({ origin, host: uri.asciiHost })",
            '"allow-identifying-attestation"',
            '"continue-anonymized"',
            '"related-origin-create"',
            '"related-origin-use"',
            "new TextEncoder().encode(response.pin).length > 63",
            "service.setHasAttestationConsent(tid, true)",
            "service.setHasAttestationConsent(tid, false)",
            "service.pinCallback(tid, response.pin)",
            "service.selectionCallback(tid, response.selected)",
            "service.cancel(tid)",
            "#cancelWebAuthnPrompts(reason)",
            "#exitContentFullscreenForWebAuthn()",
            "desktopEmbedderWebAuthnRelatedOriginPrompt",
            "projectRelatedOriginWebAuthnDescriptor",
            "projectConditionalWebAuthnDescriptor",
            "desktopEmbedderWebAuthnConditionalPrompt",
            "#handleConditionalWebAuthnPrompt(event)",
            "#cancelNativeConditionalWebAuthnPrompt(event)",
            'kind: "conditional-get"',
        ),
    )
    public_descriptor = engine[
        engine.find("function projectWebAuthnDescriptor") : engine.find(
            "function normalizeWebAuthnResponse"
        )
    ]
    for forbidden in ("tid:", "browsingContextId:", "principal:", "device:"):
        require(
            failures,
            forbidden not in public_descriptor,
            f"public WebAuthn descriptor leaks {forbidden[:-1]}",
        )
    require(
        failures,
        'promptEntry.delegateType === "webAuthn"' in engine
        and "this.#prompts.push(promptEntry)" in engine,
        "WebAuthn does not share the Session prompt FIFO",
    )
    require(
        failures,
        "browser.desktopEmbedderWebAuthnPromptActive?.()" in fullscreen,
        "content fullscreen is not blocked while WebAuthn UI is active",
    )
    require_markers(
        failures,
        credential_child + credential_parent,
        "conditional WebAuthn Binding",
        (
            'includes("webauthn")',
            'input.getAttribute("autocomplete")',
            "autocomplete.length <= 256",
            '"DesktopCredential:CheckConditional"',
            '"DesktopCredential:ConditionalWebAuthn"',
            '"webauthn:conditional-get-pending"',
            'QueryInterface: ChromeUtils.generateQI(["nsIObserver"])',
            "Services.obs.addObserver(\n      this.#conditionalObserver,",
            "Services.obs.removeObserver(\n          this.#conditionalObserver,",
            "hasPendingConditionalGet",
            "getAutoFillEntries",
            "selectAutoFillEntry",
            "resumeConditionalGet",
            "MAX_ACCOUNT_COUNT = 32",
            "MAX_CREDENTIAL_ID_LENGTH = 1024",
            "desktopEmbedderWebAuthnConditionalPrompt",
            "desktopEmbedderWebAuthnConditionalPromptCanceled",
        ),
    )
    require(
        failures,
        "credentialId" not in engine,
        "WebAuthn credential IDs cross the Binding/Core boundary",
    )

    require_markers(
        failures,
        related_origin,
        "related-origin Gecko hook",
        (
            "desktopEmbedderWebAuthnRelatedOriginPrompt",
            "desktopEmbedderWebAuthnRelatedOriginPromptCanceled",
            "browsingContextId: aManager.browsingContext.id",
            "origin: aManager.documentPrincipal.originNoSuffix",
            "Promise.resolve(decision).then(settle, () => settle(false))",
            "chromeWin.PopupNotifications.show(",
        ),
    )

    try:
        ledger = json.loads(read("../runtime/config/gecko-semantic-ports.json", failures) or "{}")
        port = next(
            item
            for item in ledger.get("ports", [])
            if item.get("id") == "desktop-embedder-webauthn-related-origin-consent"
        )
    except (json.JSONDecodeError, StopIteration):
        failures.append("semantic port ledger has no related-origin WebAuthn port")
    else:
        require(
            failures, port.get("order") == 30, "WebAuthn semantic port is misordered"
        )
        require(
            failures,
            SEMANTIC_PORT.is_file() and port.get("sha256") == sha256(SEMANTIC_PORT),
            "WebAuthn semantic port hash differs from ledger",
        )

    require_markers(
        failures,
        authrs + test_token,
        "privileged virtual-authenticator routing",
        (
            "pub fn has_virtual_authenticators(&self) -> bool",
            "self.test_token_manager.has_virtual_authenticators()",
            "changing the product's locked production USB-token policy",
            ".register(timeout_ms.into(), info, status_tx, state_callback)",
            ".sign(timeout_ms as u64, info, status_tx, state_callback)",
            ".get_autofill_entries(&rp_id_string, &allow_list)",
        ),
    )
    require_markers(
        failures,
        service + service_header,
        "cross-platform active virtual-authenticator routing",
        (
            "nsTHashSet<nsCStringHashKey> mVirtualAuthenticatorIds",
            "!mVirtualAuthenticatorIds.IsEmpty()",
            "NS_SUCCEEDED(rv) && !aRetval.IsEmpty()",
            "mVirtualAuthenticatorIds.Insert(aRetval)",
            "mVirtualAuthenticatorIds.Remove(aAuthenticatorId)",
            "return mPlatformService",
        ),
    )
    try:
        ledger = json.loads(read("../runtime/config/gecko-semantic-ports.json", failures) or "{}")
        virtual_port = next(
            item
            for item in ledger.get("ports", [])
            if item.get("id") == "webdriver-virtual-authenticator-routing"
        )
    except (json.JSONDecodeError, StopIteration):
        failures.append("semantic port ledger has no virtual-authenticator route")
    else:
        require(
            failures,
            virtual_port.get("order") == 32,
            "virtual-authenticator semantic port is misordered",
        )
        require(
            failures,
            VIRTUAL_AUTHENTICATOR_PORT.is_file()
            and virtual_port.get("sha256") == sha256(VIRTUAL_AUTHENTICATOR_PORT),
            "virtual-authenticator semantic port hash differs from ledger",
        )
    try:
        ledger = json.loads(read("../runtime/config/gecko-semantic-ports.json", failures) or "{}")
        active_virtual_port = next(
            item
            for item in ledger.get("ports", [])
            if item.get("id")
            == "webdriver-virtual-authenticator-platform-routing"
        )
    except (json.JSONDecodeError, StopIteration):
        failures.append(
            "semantic port ledger has no active virtual-authenticator route"
        )
    else:
        require(
            failures,
            active_virtual_port.get("order") == 35,
            "active virtual-authenticator semantic port is misordered",
        )
        require(
            failures,
            ACTIVE_VIRTUAL_ROUTING_PORT.is_file()
            and active_virtual_port.get("sha256")
            == sha256(ACTIVE_VIRTUAL_ROUTING_PORT),
            "active virtual-authenticator semantic port hash differs from ledger",
        )
        require_markers(
            failures,
            read(
                "../runtime/patches/gecko/0035-route-active-webdriver-virtual-authenticators.patch",
                failures,
            ),
            "active virtual-authenticator semantic patch",
            (
                "nsTHashSet<nsCStringHashKey> mVirtualAuthenticatorIds",
                "!mVirtualAuthenticatorIds.IsEmpty()",
                "mVirtualAuthenticatorIds.Insert(aRetval)",
                "mVirtualAuthenticatorIds.Remove(aAuthenticatorId)",
            ),
        )

    require_markers(
        failures,
        prefs,
        "WebAuthn product policy",
        (
            'pref("security.webauth.webauthn", true, locked);',
            'pref("security.webauthn.ctap2", true, locked);',
            'pref("security.webauthn.enable_conditional_mediation", true, locked);',
            'pref("security.webauth.webauthn_enable_softtoken", false, locked);',
            'pref("security.webauth.webauthn_enable_usbtoken", true, locked);',
            'pref("security.webauthn.always_allow_direct_attestation", false, locked);',
            'pref("security.webauthn.related_origin_requests_mode", 2, locked);',
            'pref("security.webauthn.allow_with_certificate_override", false, locked);',
        ),
    )

    require_markers(
        failures,
        platform + xhtml,
        "WebAuthn Platform UI",
        (
            "webAuthnResolvers",
            "webAuthn: {",
            "answerWebAuthnPrompt",
            "const restoreContentFocus = () =>",
            "const focusOpenedPrompt = (request) =>",
            "promptPanel.contains(document.activeElement)",
            "activeRecord?.session.view?.focus()",
            'candidate?.category === "webauthn"',
            'request.category === "webauthn"',
            '"webauthn.continueAnonymized"',
            '"webauthn.relatedOriginCreate"',
            '"webauthn.conditionalGet"',
            '"webauthn.useAnother"',
            'id="prompt-password-label-text"',
        ),
    )
    for locale, source in (("en-US", en_us), ("zh-CN", zh_cn)):
        require_markers(
            failures,
            source,
            f"{locale} WebAuthn catalogue",
            (
                '"webauthn.title"',
                '"webauthn.pinRequired"',
                '"webauthn.selectAccount"',
                '"webauthn.allowIdentification"',
                '"webauthn.continueAnonymized"',
                '"webauthn.relatedOriginCreate"',
                '"webauthn.relatedOriginUse"',
                '"webauthn.conditionalGet"',
                '"webauthn.useAnother"',
            ),
        )

    require_markers(
        failures,
        driver,
        "retained Classic WebDriver WebAuthn automation",
        (
            '"WebAuthn:AddVirtualAuthenticator"',
            '"WebAuthn:RemoveVirtualAuthenticator"',
            '"WebAuthn:AddCredential"',
            '"WebAuthn:GetCredentials"',
            '"WebAuthn:SetUserVerified"',
        ),
    )
    require_markers(
        failures,
        service_header + linux_monitor + libudev,
        "Linux native WebAuthn provider",
        (
            "mPlatformService = mAuthrsService",
            'const UDEV_SUBSYSTEM: &str = "hidraw"',
            "libudev::Enumerator::new",
            'Library::open("libudev.so.1")',
        ),
    )
    require_markers(
        failures,
        windows_service + windows_service_header,
        "Windows native WebAuthn cancellation and lifetime bridge",
        (
            "gWinWebauthnGetCancellationId",
            "gWinWebauthnCancelCurrentOperation",
            "gWinWebauthnCancelCurrentOperation(&cancellationId);",
            "mActiveTransaction = Some(TransactionState{aTransactionId, cancellationId});",
            "&cancellationId,  // CancellationId",
            "ArmTransactionTimeout(uint64_t aTransactionId",
            "NS_NewTimerWithCallback(",
            "self->mActiveTransaction.ref().transactionId == aTransactionId",
            "mTransactionTimer->Cancel();",
            "rv = ArmTransactionTimeout(aTransactionId, timeout);",
            "nsCOMPtr<nsITimer> mTransactionTimer;",
        ),
    )
    require(
        failures,
        windows_service.count(
            "rv = ArmTransactionTimeout(aTransactionId, timeout);"
        )
        == 2,
        "Windows native WebAuthn lifetime timer does not cover both create and get",
    )
    try:
        ledger = json.loads(read("../runtime/config/gecko-semantic-ports.json", failures) or "{}")
        timeout_port = next(
            item
            for item in ledger.get("ports", [])
            if item.get("id") == "windows-webauthn-lifetime-timeout"
        )
    except (json.JSONDecodeError, StopIteration):
        failures.append("semantic port ledger has no Windows WebAuthn lifetime port")
    else:
        require(
            failures,
            timeout_port.get("order") == 34,
            "Windows WebAuthn lifetime semantic port is misordered",
        )
        require(
            failures,
            WINDOWS_LIFETIME_TIMEOUT_PORT.is_file()
            and timeout_port.get("sha256")
            == sha256(WINDOWS_LIFETIME_TIMEOUT_PORT),
            "Windows WebAuthn lifetime semantic port hash differs from ledger",
        )
        invariants = timeout_port.get("invariants", [])
        require(
            failures,
            len(invariants) == 4
            and any("exact Gecko transaction ID" in item for item in invariants)
            and any("WebAuthNCancelCurrentOperation" in item for item in invariants)
            and any("expired timer cannot cancel a later transaction" in item for item in invariants)
            and any("does not enable a software authenticator" in item for item in invariants),
            "Windows WebAuthn lifetime semantic port invariants are incomplete",
        )
        require_markers(
            failures,
            read(
                "../runtime/patches/gecko/0034-enforce-windows-webauthn-transaction-timeout.patch",
                failures,
            ),
            "Windows WebAuthn lifetime semantic patch",
            (
                "ArmTransactionTimeout(uint64_t aTransactionId",
                "NS_NewTimerWithCallback(",
                "self->mActiveTransaction.ref().transactionId == aTransactionId",
                "mTransactionTimer->Cancel();",
                "rv = ArmTransactionTimeout(aTransactionId, timeout);",
                "nsCOMPtr<nsITimer> mTransactionTimer;",
            ),
        )
    return failures


def verify_runtime(runtime: Path) -> list[str]:
    failures: list[str] = []
    omni = runtime / "omni.ja"
    if not omni.is_file():
        return [f"runtime omni.ja is missing: {omni}"]
    try:
        with zipfile.ZipFile(omni) as archive:
            names = set(archive.namelist())
            required = {
                "modules/DesktopWebAuthnPrompt.sys.mjs",
                "modules/DesktopCredentialChild.sys.mjs",
                "modules/DesktopCredentialParent.sys.mjs",
                "modules/WebAuthnRelatedOriginFetcher.sys.mjs",
                "modules/DesktopEngine.sys.mjs",
                "defaults/pref/navis.js",
                "chrome/navis/content/main.mjs",
                "chrome/navis/content/locales/en-US.mjs",
                "chrome/navis/content/locales/zh-CN.mjs",
            }
            require(
                failures,
                required <= names,
                "runtime WebAuthn inventory is incomplete: "
                + ", ".join(sorted(required - names)),
            )
            if required <= names:
                packaged_binding = archive.read(
                    "modules/DesktopWebAuthnPrompt.sys.mjs"
                ).decode("utf-8")
                packaged_related = archive.read(
                    "modules/WebAuthnRelatedOriginFetcher.sys.mjs"
                ).decode("utf-8")
                packaged_prefs = archive.read("defaults/pref/navis.js").decode("utf-8")
                packaged_credential_child = archive.read(
                    "modules/DesktopCredentialChild.sys.mjs"
                ).decode("utf-8")
                packaged_credential_parent = archive.read(
                    "modules/DesktopCredentialParent.sys.mjs"
                ).decode("utf-8")
                require(
                    failures,
                    'const WEB_AUTHN_TOPIC = "webauthn-prompt"' in packaged_binding,
                    "runtime WebAuthn observer binding differs",
                )
                require(
                    failures,
                    "desktopEmbedderWebAuthnRelatedOriginPrompt" in packaged_related,
                    "runtime related-origin hook is absent",
                )
                require(
                    failures,
                    'pref("security.webauth.webauthn", true, locked);'
                    in packaged_prefs
                    and 'pref("security.webauth.webauthn_enable_softtoken", false, locked);'
                    in packaged_prefs
                    and 'pref("security.webauth.webauthn_enable_usbtoken", true, locked);'
                    in packaged_prefs,
                    "runtime WebAuthn product policy is absent",
                )
                require(
                    failures,
                    'pref("security.webauthn.enable_conditional_mediation", true, locked);'
                    in packaged_prefs
                    and "DesktopCredential:ConditionalWebAuthn"
                    in packaged_credential_child
                    and "selectAutoFillEntry" in packaged_credential_parent
                    and 'QueryInterface: ChromeUtils.generateQI(["nsIObserver"])'
                    in packaged_credential_parent,
                    "runtime conditional WebAuthn binding is absent",
                )
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as error:
        failures.append(f"cannot inspect runtime omni.ja: {error}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()

    failures = verify_source()
    if args.runtime is not None:
        failures.extend(verify_runtime(args.runtime.resolve()))
    if failures:
        print("Navis WebAuthn verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(
        "Navis WebAuthn policy verified: native authenticators, bounded "
        "WebAuthnDelegate, conditional mediation, related-origin consent and "
        "deterministic automation"
    )
    if args.runtime is not None:
        print(f"- runtime: {args.runtime.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
