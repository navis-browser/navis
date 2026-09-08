#!/usr/bin/env bash

set -euo pipefail

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_dir="${1:-$workspace_dir/../runtime/gecko/obj-navis-runtime/dist/navis}"
cd "$workspace_dir"

fail() {
  printf 'Runtime package verification failed: %s\n' "$1" >&2
  exit 1
}

if [[ ! -d "$runtime_dir" ]]; then
  fail "directory does not exist: $runtime_dir"
fi

python3 "$workspace_dir/scripts/verify-remote-settings-policy.py" \
  --runtime "$runtime_dir"
python3 "$workspace_dir/scripts/verify-clean-links-policy.py" \
  --runtime "$runtime_dir"
python3 "$workspace_dir/scripts/verify-spellcheck.py" \
  --runtime "$runtime_dir"
python3 "$workspace_dir/scripts/verify-webauthn.py" \
  --runtime "$runtime_dir"
python3 "$workspace_dir/scripts/verify-deferred-web-apis.py" \
  --runtime "$runtime_dir"

required_common=(
  application.ini
  dependentlibs.list
  omni.ja
  platform.ini
)

for path in "${required_common[@]}"; do
  if [[ ! -f "$runtime_dir/$path" ]]; then
    fail "missing $path"
  fi
done

if [[ "$(awk -F= '$1 == "Name" { print $2; exit }' \
  "$runtime_dir/application.ini")" != Navis ]]; then
  fail "application.ini does not identify Navis"
fi

if [[ -x "$runtime_dir/navis" ]]; then
  platform=linux
  required_platform=(
    glxtest
    libfreeblpriv3.so
    libmozsandbox.so
    libnss3.so
    libsoftokn3.so
    libxul.so
    navis
  )
elif [[ -f "$runtime_dir/navis.exe" ]]; then
  platform=win64
  required_platform=(
    AccessibleMarshal.dll
    CoreMessagingXP.dll
    libEGL.dll
    libGLESv2.dll
    mozglue.dll
    navis.exe
    nss3.dll
    plugin-container.exe
    softokn3.dll
    xul.dll
  )
else
  fail "no Navis executable found"
fi

for path in "${required_platform[@]}"; do
  if [[ ! -f "$runtime_dir/$path" ]]; then
    fail "missing $path for $platform"
  fi
done

remaining_link="$(find "$runtime_dir" -type l -print -quit)"
if [[ -n "$remaining_link" ]]; then
  fail "symbolic link remains: $remaining_link"
fi

inventory_file="$(mktemp -t navis-runtime-inventory.XXXXXX)"
omni_file="$(mktemp -t navis-runtime-omni.XXXXXX)"
extension_manifest_file="$(mktemp -t navis-extension-manifest.XXXXXX)"
chrome_manifest_file="$(mktemp -t navis-chrome-manifest.XXXXXX)"
product_pref_file="$(mktemp -t navis-product-prefs.XXXXXX)"
error_markup_file="$(mktemp -t navis-error-markup.XXXXXX)"
cleanup() {
  rm -f -- "$inventory_file" "$omni_file" "$extension_manifest_file" \
    "$chrome_manifest_file" "$product_pref_file" "$error_markup_file"
}
trap cleanup EXIT HUP INT TERM

find "$runtime_dir" -mindepth 1 -printf '%P\n' | sort > "$inventory_file"
unzip -Z1 "$runtime_dir/omni.ja" | sort > "$omni_file"
if ! unzip -p "$runtime_dir/omni.ja" components/components.manifest \
  > "$extension_manifest_file"; then
  fail "cannot read built-in extension registrations from omni.ja"
fi
if ! unzip -p "$runtime_dir/omni.ja" chrome/chrome.manifest \
  > "$chrome_manifest_file"; then
  fail "cannot read chrome registrations from omni.ja"
fi
if ! unzip -p "$runtime_dir/omni.ja" defaults/pref/navis.js \
  > "$product_pref_file"; then
  fail "cannot read product preferences from omni.ja"
fi
if ! unzip -p "$runtime_dir/omni.ja" \
  chrome/toolkit/content/global/aboutNetError.html \
  chrome/toolkit/content/global/httpsonlyerror/errorpage.html \
  > "$error_markup_file"; then
  fail "cannot read engine error-page markup from omni.ja"
fi

while IFS= read -r path; do
  basename="${path##*/}"
  basename="${basename,,}"
  case "$basename" in
    certutil | certutil.exe | desktop-launcher.exe | firefox.exe | \
      nsinstall | pk12util | pk12util.exe | rapl | xpcshell | xpcshell.exe | \
      pingsender | pingsender.exe | desktop-embedder-test-shell*)
      fail "development or Firefox product file entered package: $path"
      ;;
    *mozinference* | *onnxruntime*)
      fail "disabled local-ML runtime entered package: $path"
      ;;
  esac
  case "$path" in
    gmp-fake/* | gmp-fakeopenh264/*)
      fail "test media plugin entered package: $path"
      ;;
  esac
done < "$inventory_file"

required_omni=(
  chrome/en-US/locale/branding/brand.properties
  localization/en-US/branding/brand.ftl
  localization/en-US/toolkit/neterror/certError.ftl
  localization/en-US/toolkit/neterror/netError.ftl
  localization/en-US/toolkit/neterror/nsserrors.ftl
  chrome/navis/content/internal-pages.mjs
  chrome/navis/content/main.mjs
  chrome/navis/content/main.xhtml
  chrome/navis/content/omnibox-edit-state.mjs
  chrome/navis/content/omnibox-validity.mjs
  chrome/toolkit/content/global/certviewer/certDecoder.mjs
  chrome/toolkit/content/global/certviewer/components/utils.mjs
  chrome/toolkit/content/global/certviewer/components/logNameTable.mjs
  chrome/toolkit/content/global/certviewer/vendor/pkijs.js
  chrome/toolkit/content/global/aboutNetError.html
  chrome/toolkit/content/global/httpsonlyerror/errorpage.html
  chrome/toolkit/skin/classic/global/aboutHttpsOnlyError.css
  chrome/toolkit/skin/classic/global/aboutNetError.css
  chrome/toolkit/skin/classic/global/icons/desktop-embedder-error.svg
  chrome/toolkit/skin/classic/global/in-content/info-pages.css
  defaults/pref/navis.js
  defaults/settings/security-state/intermediates.json
  defaults/settings/security-state/onecrl.json
  modules/DesktopEmbedderAuthPromptFactory.sys.mjs
  modules/DesktopEmbedderPromptCollection.sys.mjs
  modules/DesktopEmbedderPromptParent.sys.mjs
  modules/DesktopEmbedderStartup.sys.mjs
  modules/DesktopWebAuthnPrompt.sys.mjs
  modules/DesktopFullscreenChild.sys.mjs
  modules/DesktopFullscreenParent.sys.mjs
  modules/DesktopInternalPageChild.sys.mjs
  modules/DesktopInternalPageParent.sys.mjs
  modules/DesktopInternalPages.sys.mjs
  modules/DesktopNavisProtocolHandler.sys.mjs
  modules/DesktopCredentialChild.sys.mjs
  modules/DesktopCredentialParent.sys.mjs
  modules/DesktopContextMenuChild.sys.mjs
  modules/DesktopContextMenuParent.sys.mjs
  modules/DesktopCleanLinks.sys.mjs
  modules/DesktopFaviconChild.sys.mjs
  modules/DesktopFaviconParent.sys.mjs
  modules/DesktopEngine.sys.mjs
  modules/WebAuthnRelatedOriginFetcher.sys.mjs
  modules/DesktopProfileStore.sys.mjs
  modules/DesktopExtensionActions.sys.mjs
  modules/DesktopExtensionManager.sys.mjs
  modules/DesktopExtensionMenus.sys.mjs
  modules/DesktopExtensionOmnibox.sys.mjs
  modules/DesktopExtensionPermissionPrompts.sys.mjs
  modules/DesktopExtensionPopupHost.sys.mjs
  modules/DesktopExtensionTabs.sys.mjs
  modules/AboutNewTab.sys.mjs
  modules/ExtensionBrowsingData.sys.mjs
  modules/ExtensionShortcuts.sys.mjs
  modules/Extension.sys.mjs
  modules/ExtensionParent.sys.mjs
  modules/ExtensionStorage.sys.mjs
  modules/Schemas.sys.mjs
  modules/addons/XPIProvider.sys.mjs
  chrome/navis-extensions/content/ext-browserAction.js
  chrome/navis-extensions/content/ext-pageAction.js
  chrome/navis-extensions/content/ext-commands.js
  chrome/navis-extensions/content/ext-menus.js
  chrome/navis-extensions/content/ext-omnibox.js
  chrome/navis-extensions/content/ext-navis.js
  chrome/navis-extensions/content/ext-navis.json
  chrome/navis-extensions/content/ext-c-navis.js
  chrome/navis-extensions/content/ext-c-menus.js
  chrome/navis-extensions/content/ext-c-omnibox.js
  chrome/navis-extensions/content/ext-c-tabs.js
  chrome/navis-extensions/content/ext-tabs.js
  chrome/navis-extensions/content/ext-windows.js
  chrome/navis-extensions/content/ext-history.js
  chrome/navis-extensions/content/ext-bookmarks.js
  chrome/navis-extensions/content/ext-topSites.js
  chrome/navis-extensions/content/ext-search.js
  chrome/navis-extensions/content/ext-sessions.js
  chrome/navis-extensions/content/schemas/page_action.json
  chrome/navis-extensions/content/schemas/commands.json
  chrome/navis-extensions/content/schemas/menus.json
  chrome/navis-extensions/content/schemas/omnibox.json
  chrome/navis-extensions/content/schemas/sessions.json
  components/components.manifest
  chrome/toolkit/content/extensions/ext-browser-content.js
  chrome/toolkit/content/extensions/ext-toolkit.json
  chrome/toolkit/content/extensions/parent/ext-storage.js
  chrome/toolkit/content/extensions/parent/ext-browsingData.js
  chrome/toolkit/content/extensions/parent/ext-tabs-base.js
  chrome/toolkit/content/extensions/parent/ext-toolkit.js
)

for path in "${required_omni[@]}"; do
  if ! rg --fixed-strings --line-regexp --quiet "$path" "$omni_file"; then
    fail "omni.ja is missing $path"
  fi
done

if ! rg --fixed-strings --quiet \
  'pref("browser.desktop-embedder.material-error-pages", true, locked);' \
  "$product_pref_file"; then
  fail "packaged product preferences do not enable Material error pages"
fi
if ! rg --fixed-strings --quiet \
  'pref("app.support.baseURL", "", locked);' \
  "$product_pref_file"; then
  fail "packaged product preferences expose unowned Firefox error help"
fi

if [[ "$(rg --fixed-strings --count-matches \
  'class="desktop-embedder-error-document"' "$error_markup_file")" != 2 ]]; then
  fail "packaged network and HTTPS-only errors lack the product style scope"
fi

for path in \
  modules/DesktopExtensionContributions.sys.mjs \
  modules/DesktopUBlockOriginIntegration.sys.mjs \
  modules/DesktopUBlockOriginIntegrationChild.sys.mjs; do
  if rg --fixed-strings --line-regexp --quiet "$path" "$omni_file"; then
    fail "obsolete specialized built-in bridge remains in omni.ja: $path"
  fi
done

while IFS= read -r path; do
  case "$path" in
    chrome/toolkit/content/global/certviewer/certDecoder.mjs | \
      chrome/toolkit/content/global/certviewer/components/utils.mjs | \
      chrome/toolkit/content/global/certviewer/components/logNameTable.mjs | \
      chrome/toolkit/content/global/certviewer/vendor/pkijs.js) ;;
    *) fail "Firefox certificate-viewer frontend entered omni.ja: $path" ;;
  esac
done < <(rg '^chrome/toolkit/content/global/certviewer/' "$omni_file" || true)

if ! rg --fixed-strings --line-regexp --quiet \
  'content navis-extensions navis-extensions/content/' \
  "$chrome_manifest_file"; then
  fail "built-in extension adapter chrome package is not registered"
fi

required_extension_registrations=(
  'category webextension-modules navis chrome://navis-extensions/content/ext-navis.json'
  'category webextension-scripts c-navis chrome://navis-extensions/content/ext-navis.js'
  'category webextension-scripts-addon navis chrome://navis-extensions/content/ext-c-navis.js'
)
for registration in "${required_extension_registrations[@]}"; do
  if ! rg --fixed-strings --line-regexp --quiet "$registration" \
    "$extension_manifest_file"; then
    fail "built-in extension adapter registration is missing: $registration"
  fi
done

if rg --quiet \
  '(^browser/|^chrome/devtools/|^chrome/toolkit/content/global/(?:ml/|about(?:Glean|Telemetry)|gmp-sources/)|^chrome/toolkit/skin/classic/mozapps/extensions/|desktop-embedder-test-shell|^modules/(?:BHRTelemetryService|BrowserTelemetryUtils|ClientID|CoveragePing|EventPing|GMPInstallManager|GMPUtils|HealthPing|Telemetry[^/]*|UninstallPing|UntrustedModulesPing|UpdatePing|UsageReporting)\.sys\.mjs$|^modules/GMPExtractor\.worker\.js$|^modules/backgroundtasks/BackgroundTask_pingsender\.sys\.mjs$|^modules/addons/GMPProvider\.sys\.mjs$|^modules/(?:NativeManifests|NativeMessaging)\.sys\.mjs$|^modules/amInstallTrigger\.sys\.mjs$|/(MLEngine|ModelHubProvider|Rust|FxAccounts|ExtensionStorageSync)[^/]*$|/(LoginManager|LoginAutoComplete|LoginFormFactory|LoginHelper|LoginRecipes|LoginStore|PasswordGenerator)[^/]*\.sys\.mjs$|/(BookmarkHTMLUtils|BookmarkJSONUtils|BookmarkList|Bookmarks|ExtensionSearchHandler|History|PlacesBackups|PlacesDBUtils|PlacesExpiration|PlacesFrecencyRecalculator|PlacesPreviews|PlacesQuery|PlacesSemanticHistoryDatabase|PlacesSemanticHistoryManager|PlacesSyncUtils|PlacesTransactions|PlacesUtils|SyncedBookmarksMirror|TaggingService)\.sys\.mjs$|^actors/Printing[^/]*\.sys\.mjs$)' \
  "$omni_file"; then
  fail "disabled Firefox product subsystem entered omni.ja"
fi

if rg --fixed-strings --line-regexp --quiet \
  'category addon-provider-module GMPProvider resource://gre/modules/addons/GMPProvider.sys.mjs' \
  "$extension_manifest_file"; then
  fail "disabled GMP product provider entered omni.ja"
fi

if awk '
  /^chrome\/toolkit\/content\/mozapps\/extensions\// &&
    $0 != "chrome/toolkit/content/mozapps/extensions/OpenH264-license.txt" {
      found = 1
    }
  END { exit !found }
' "$omni_file"; then
  fail "disabled add-ons UI entered omni.ja"
fi

if [[ "$platform" == linux ]]; then
  if env LD_LIBRARY_PATH="$runtime_dir" ldd "$runtime_dir/libxul.so" | \
    rg --quiet 'not found'; then
    fail "libxul.so has an unresolved dynamic dependency"
  fi
fi

file_count="$(find "$runtime_dir" -type f | wc -l)"
byte_count="$(du -sb "$runtime_dir" | awk '{print $1}')"
printf 'Navis %s runtime package verified: %s files, %s bytes\n' \
  "$platform" "$file_count" "$byte_count"
