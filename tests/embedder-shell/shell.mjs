/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/* This test shell is deliberately independent of the Navis product UI. */

const {
  DESKTOP_EMBEDDER_API_VERSION,
  EngineRuntime,
  EngineSession,
  EngineView,
} = ChromeUtils.importESModule("resource://gre/modules/DesktopEngine.sys.mjs");

const status = document.getElementById("status");
const viewHost = document.getElementById("view-host");
const visitedUrls = new Set();
const ignoredStableLoadSessionIds = new Set();
const mainWindowViews = new WeakSet();
const navigationObservations = new Map();
let stableLoads = 0;
let quitScheduled = false;
let rejectFirstPopupDelivery = true;
let multiWindowComplete = false;
let lastActiveContentProcessIdentity = "";
let faviconProjected = false;
const silentInternalURI = "navis://newtab/";
let silentInternalLifecycleObserved = false;
const silentInternalNavigationIds = new Set();

function emit(type, details = {}) {
  const payload = JSON.stringify({ type, ...details });
  dump(`DESKTOP_EMBEDDER_SHELL_EVENT ${payload}\n`);
  status.value = `${type}: ${details.url || details.sessionId || "ok"}`;
}

function requireContract(condition, message) {
  if (condition) {
    return;
  }
  emit("contract-failure", { message });
  throw new Error(`Desktop Gecko Embedder contract failed: ${message}`);
}

function verifyNavigationProjection(session, state) {
  requireContract(
    Number.isSafeInteger(state.navigationId) && state.navigationId >= 0,
    "navigation identity is not a non-negative safe integer"
  );
  requireContract(
    Number.isSafeInteger(state.navigationRevision) &&
      state.navigationRevision >= 0,
    "navigation revision is not a non-negative safe integer"
  );
  requireContract(
      typeof state.url === "string" &&
      typeof state.title === "string" &&
      typeof state.baseDomain === "string" &&
      typeof state.favicon === "string" &&
      state.favicon.length <= 786432 &&
      typeof state.loading === "boolean" &&
      ["idle", "pending", "visible", "silent"].includes(
        state.loadingActivity
      ) &&
      state.loading === (state.loadingActivity !== "idle") &&
      ["unknown", "insecure", "broken", "secure"].includes(state.security) &&
      typeof state.canGoBack === "boolean" &&
      typeof state.canGoForward === "boolean" &&
      (state.failureCode === null ||
        (Number.isSafeInteger(state.failureCode) && state.failureCode >= 0)),
    "navigation state is not normalized plain data"
  );

  const previous = navigationObservations.get(session.id);
  if (previous) {
    requireContract(
      state.navigationRevision >= previous.revision,
      "navigation revision moved backward"
    );
    requireContract(
      state.navigationId >= previous.id,
      "navigation identity moved backward"
    );
    if (state.navigationId !== previous.id) {
      requireContract(
        state.navigationId > previous.id &&
          state.navigationRevision > previous.revision,
        "new navigation identity did not advance its revision"
      );
    }
  }
  navigationObservations.set(session.id, {
    id: state.navigationId,
    revision: state.navigationRevision,
  });
}

function verifyObservabilityIsConstantOff() {
  const telemetry = Services.telemetry;
  requireContract(telemetry, "nsITelemetry compatibility service is missing");
  requireContract(
    !telemetry.canRecordBase &&
      !telemetry.canRecordExtended &&
      !telemetry.canRecordReleaseData &&
      !telemetry.canRecordPrereleaseData,
    "observability compatibility service reports recording enabled"
  );

  telemetry.canRecordBase = true;
  telemetry.canRecordExtended = true;
  requireContract(
    !telemetry.canRecordBase && !telemetry.canRecordExtended,
    "observability compatibility service accepted an enable request"
  );

  const histograms = telemetry.getSnapshotForHistograms();
  const scalars = telemetry.getSnapshotForScalars();
  requireContract(
    Object.keys(histograms).length === 0 && Object.keys(scalars).length === 0,
    "observability compatibility service retained recorded data"
  );
  requireContract(
    Array.isArray(telemetry.getAllStores()) &&
      telemetry.getAllStores().length === 0,
    "observability compatibility service exposes a data store"
  );
  requireContract(
    Number.isFinite(telemetry.msSinceProcessStart()) &&
      telemetry.msSinceProcessStart() >= 0 &&
      Number.isFinite(telemetry.msSystemNow()) &&
      telemetry.msSystemNow() > 0,
    "process-clock compatibility methods are unavailable"
  );
  emit("observability-disabled");
}

function emitActiveContentProcess(targetSession) {
  if (
    targetSession.state.loading ||
    targetSession.state.crashed ||
    !targetSession.view?.visible ||
    !mainWindowViews.has(targetSession.view)
  ) {
    return;
  }

  // This is deliberately a test-shell observation of its own chrome DOM.  A
  // native process identifier must not become part of the public EngineView
  // or EngineSession contract merely to support external crash injection.
  const browser = viewHost.querySelector(
    "browser.desktop-engine-view[primary]"
  );
  const browsingContext = browser?.browsingContext;
  const processId =
    browser?.frameLoader?.remoteTab?.osPid ||
    browsingContext?.currentWindowGlobal?.osPid;
  if (!Number.isSafeInteger(processId) || processId <= 0 || !browsingContext) {
    return;
  }

  const identity = `${targetSession.id}:${browsingContext.id}:${processId}`;
  if (identity === lastActiveContentProcessIdentity) {
    return;
  }
  lastActiveContentProcessIdentity = identity;
  emit("active-content-process", {
    sessionId: targetSession.id,
    browsingContextId: browsingContext.id,
    processId,
  });
}

function maybeQuit() {
  if (
    quitScheduled ||
    !multiWindowComplete ||
    !Services.prefs.getBoolPref("desktop.embedder.shell.autoclose", false)
  ) {
    return;
  }
  const expectedStableLoads = Services.prefs.getIntPref(
    "desktop.embedder.shell.expectedStableLoads",
    1
  );
  if (stableLoads < expectedStableLoads) {
    return;
  }
  quitScheduled = true;
  emit("contract-complete", { stableLoads });
  setTimeout(() => {
    requireContract(runtime.close(), "Runtime did not close");
    requireContract(runtime.closed, "Runtime did not enter closed state");
    requireContract(runtime.sessionCount === 0, "Runtime retained a Session");
    requireContract(runtime.windowCount === 0, "Runtime retained a window");
    requireContract(runtime.downloads.length === 0, "Runtime retained downloads");
    requireContract(!runtime.close(), "Runtime close was not idempotent");
    let rejectedAfterClose = false;
    try {
      runtime.createSession();
    } catch {
      rejectedAfterClose = true;
    }
    requireContract(rejectedAfterClose, "closed Runtime accepted a Session");
    emit("runtime-closed");
    Services.startup.quit(Ci.nsIAppStartup.eAttemptQuit);
  }, 750);
}

const createView = () => {
  const view = new EngineView({ document, host: viewHost });
  mainWindowViews.add(view);
  return view;
};
let legacyRuntimeConstructionRejected = false;
try {
  new EngineRuntime({ document, createView });
} catch {
  legacyRuntimeConstructionRejected = true;
}
requireContract(
  legacyRuntimeConstructionRejected,
  "Runtime accepted the source API 1 window/factory constructor"
);

const runtime = new EngineRuntime();
verifyObservabilityIsConstantOff();
requireContract(
  DESKTOP_EMBEDDER_API_VERSION === 2,
  "unexpected Desktop Embedder source API version"
);
requireContract(
  typeof EngineSession.prototype.stop === "function" &&
    typeof EngineView.prototype.focus === "function" &&
    typeof EngineView.prototype.blur === "function" &&
    typeof EngineRuntime.prototype.measureSessionMemoryUsage === "function",
  "loading, View input, or tab-memory lifecycle methods are missing"
);
requireContract(
  runtime.windowCount === 0,
  "process Runtime captured a window during construction"
);
let implicitViewRejected = false;
try {
  runtime.createSession();
} catch {
  implicitViewRejected = true;
}
requireContract(
  implicitViewRejected && runtime.windowCount === 0,
  "Runtime accepted an implicit View or captured its caller window"
);
requireContract(
  typeof runtime.addEventListener === "undefined",
  "Runtime still exposes the migration EventTarget surface"
);
requireContract(Object.isFrozen(runtime.capabilities), "capabilities are mutable");
requireContract(
  typeof runtime.capabilities.jpegXL === "boolean" &&
    typeof runtime.capabilities.mediaCapture === "boolean" &&
    typeof runtime.capabilities.peerConnection === "boolean" &&
    runtime.capabilities.webAuthn === true &&
    runtime.capabilities.passwordManager === true &&
    runtime.capabilities.persistentData === true &&
    runtime.capabilities.privateBrowsing === true,
  "capabilities are not Boolean build facts"
);
emit("runtime-capabilities", runtime.capabilities);

async function expectProfileRejection(operation, message) {
  let rejected = false;
  try {
    await operation();
  } catch {
    rejected = true;
  }
  requireContract(rejected, message);
}

async function verifyPersistentProfileContract() {
  await runtime.ready;
  emit("persistent-profile-ready");
  const initialHistory = await runtime.queryHistory({ limit: 10 });
  const initialBookmarks = await runtime.listBookmarks();
  const initialCredentials = await runtime.listCredentials();
  requireContract(
    Object.isFrozen(initialHistory) &&
      Object.isFrozen(initialBookmarks) &&
      Object.isFrozen(initialCredentials) &&
      initialBookmarks.length === 0 &&
      initialCredentials.length === 0,
    "persistent profile queries are not frozen or did not start empty"
  );
  emit("persistent-profile-initial-state-complete");

  const folder = await runtime.createBookmark({
    type: "folder",
    title: "Contract folder",
  });
  const bookmark = await runtime.createBookmark({
    title: "Contract bookmark",
    url: "https://example.com/contract",
  });
  const leadingFolder = await runtime.createBookmark({
    type: "folder",
    title: "Leading folder",
  });
  requireContract(
    [folder, bookmark, leadingFolder].every(Object.isFrozen),
    "bookmark mutations returned mutable data"
  );

  const updated = await runtime.updateBookmark(bookmark.id, {
    title: "Updated contract bookmark",
    url: "https://example.org/updated?from=navis",
  });
  requireContract(
    updated.title === "Updated contract bookmark" &&
      updated.url === "https://example.org/updated?from=navis",
    "bookmark update did not persist normalized fields"
  );
  await runtime.moveBookmark(leadingFolder.id, "root", 0);
  let rootEntries = await runtime.listBookmarks();
  requireContract(
    rootEntries.map(entry => entry.id).join(",") ===
      [leadingFolder.id, folder.id, bookmark.id].join(",") &&
      rootEntries.every((entry, index) => entry.position === index),
    "root bookmark reordering is not contiguous"
  );

  await runtime.moveBookmark(bookmark.id, folder.id, 0);
  const childFolder = await runtime.createBookmark({
    parentId: folder.id,
    type: "folder",
    title: "Nested folder",
  });
  await runtime.moveBookmark(childFolder.id, folder.id, 0);
  const folderEntries = await runtime.listBookmarks(folder.id);
  requireContract(
    Object.isFrozen(folderEntries) &&
      folderEntries.map(entry => entry.id).join(",") ===
        [childFolder.id, bookmark.id].join(",") &&
      folderEntries.every((entry, index) => entry.position === index),
    "nested bookmark organization is not ordered or frozen"
  );

  await expectProfileRejection(
    () => runtime.moveBookmark(folder.id, childFolder.id, 0),
    "bookmark folder cycle was accepted"
  );
  await expectProfileRejection(
    () => runtime.createBookmark({ type: "separator", title: "Invalid" }),
    "unsupported bookmark type was accepted"
  );
  await expectProfileRejection(
    () =>
      runtime.createBookmark({
        parentId: "unknown-parent",
        type: "folder",
        title: "Orphan",
      }),
    "unknown bookmark parent was accepted"
  );
  await expectProfileRejection(
    () => runtime.updateBookmark(folder.id, { url: "https://example.net/" }),
    "bookmark folder accepted a URL"
  );
  await expectProfileRejection(
    () => runtime.moveBookmark(bookmark.id, folder.id, -1),
    "negative bookmark position was accepted"
  );
  await expectProfileRejection(
    () => runtime.queryHistory({ limit: 0 }),
    "invalid history query limit was accepted"
  );
  await expectProfileRejection(
    () => runtime.deleteHistory([]),
    "empty history deletion was accepted"
  );

  await runtime.removeBookmark(folder.id);
  rootEntries = await runtime.listBookmarks();
  requireContract(
    rootEntries.length === 1 && rootEntries[0].id === leadingFolder.id,
    "recursive bookmark-folder deletion retained descendants"
  );
  await runtime.removeBookmark(leadingFolder.id);
  requireContract(
    (await runtime.listBookmarks()).length === 0,
    "bookmark cleanup did not restore the empty profile"
  );
  await runtime.clearHistory();
  emit("persistent-profile-bookmarks-complete");

  const savedCredential = await runtime.saveCredential({
    origin: "https://example.com",
    actionOrigin: "https://example.com",
    username: "contract-user",
    password: "contract-secret-one",
    usernameField: "username",
    passwordField: "password",
  });
  requireContract(
    Object.isFrozen(savedCredential) &&
      savedCredential.username === "contract-user" &&
      !("password" in savedCredential),
    "password save exposed a secret or returned mutable data"
  );
  emit("persistent-profile-credential-saved");
  let credentials = await runtime.listCredentials({
    origin: "https://example.com",
  });
  requireContract(
    credentials.length === 1 &&
      Object.isFrozen(credentials) &&
      Object.isFrozen(credentials[0]) &&
      !("password" in credentials[0]),
    "password summaries are not frozen and secret-free"
  );
  const expectReauthenticationCancellation = Services.prefs.getBoolPref(
    "desktop.embedder.shell.expectCredentialReauthenticationCancellation",
    false
  );
  emit("credential-reauthentication-requested", {
    expectCancellation: expectReauthenticationCancellation,
  });
  if (expectReauthenticationCancellation) {
    let revealCanceled = false;
    try {
      await runtime.revealCredential(savedCredential.id, { window });
    } catch {
      revealCanceled = true;
    }
    requireContract(
      revealCanceled,
      "canceled OS credential reauthentication exposed the password"
    );
    emit("credential-reauthentication-canceled");
  } else {
    const revealedCredential = await runtime.revealCredential(
      savedCredential.id,
      { window }
    );
    requireContract(
      Object.isFrozen(revealedCredential) &&
        revealedCredential.password === "contract-secret-one",
      "explicit password reveal did not decrypt the stored secret"
    );
    emit("credential-reauthentication-complete");
  }
  const updatedCredential = await runtime.saveCredential({
    origin: "https://example.com",
    username: "contract-user",
    password: "contract-secret-two",
  });
  if (expectReauthenticationCancellation) {
    requireContract(
      updatedCredential.id === savedCredential.id &&
        (await runtime.listCredentials({ origin: "https://example.com" }))
          .length === 1,
      "password update did not replace the matching account"
    );
  } else {
    requireContract(
      updatedCredential.id === savedCredential.id &&
        (
          await runtime.revealCredential(updatedCredential.id, { window })
        ).password === "contract-secret-two",
      "password update did not replace the matching account"
    );
  }
  await expectProfileRejection(
    () =>
      runtime.saveCredential({
        origin: "file:///tmp/not-a-web-origin",
        username: "invalid",
        password: "secret",
      }),
    "password manager accepted a non-web origin"
  );
  await expectProfileRejection(
    () =>
      runtime.saveCredential({
        origin: "https://example.net",
        username: "invalid",
        password: "",
      }),
    "password manager accepted an empty password"
  );
  requireContract(
    (await runtime.removeCredential(savedCredential.id)) === true &&
      (await runtime.removeCredential(savedCredential.id)) === false,
    "password deletion did not report its result"
  );
  await runtime.saveCredential({
    origin: "https://example.org",
    username: "clear-user",
    password: "clear-secret",
  });
  requireContract(
    (await runtime.clearCredentials()) === true &&
      (await runtime.listCredentials()).length === 0,
    "password clear did not remove every credential and its OS key"
  );
  emit("persistent-profile-contract-complete");
}

await verifyPersistentProfileContract();

let incompleteNavigationDelegateRejected = false;
try {
  runtime.setNavigationDelegate({
    onNewSession: () => ({ allow: false }),
  });
} catch {
  incompleteNavigationDelegateRejected = true;
}
requireContract(
  incompleteNavigationDelegateRejected,
  "NavigationDelegate accepted no new-Session lifecycle callback"
);

runtime.setNavigationDelegate({
  onNewSession: request => {
    requireContract(Object.isFrozen(request), "navigation request is mutable");
    emit("navigation-request", request);
    return { allow: true, activate: true, view: createView() };
  },
  onSessionCreated: details => {
    requireContract(
      Object.isFrozen(details),
      "new Session lifecycle details are mutable"
    );
    requireContract(
      details.session instanceof EngineSession,
      "NavigationDelegate did not receive an EngineSession"
    );
    requireContract(
      details.reason === "popup" && typeof details.activate === "boolean",
      "new Session lifecycle details are incomplete"
    );
    if (rejectFirstPopupDelivery) {
      rejectFirstPopupDelivery = false;
      emit("session-delivery-rejected", {
        sessionId: details.session.id,
      });
      throw new Error("intentional new-Session lifecycle rejection");
    }
    emit("session-created", {
      sessionId: details.session.id,
      reason: details.reason,
      activate: details.activate,
      openerSessionId: details.openerSessionId,
    });
  },
  onPopupBlocked: request => {
    if (request.reason === "product lifecycle failure") {
      requireContract(
        runtime.sessionCount === 1,
        "failed Session lifecycle delivery retained an unowned Session"
      );
    }
    emit("popup-blocked", request);
  },
});

runtime.setDownloadDelegate({
  onDownloadsChanged: downloads => {
    requireContract(Object.isFrozen(downloads), "download array is mutable");
    requireContract(
      downloads.every(Object.isFrozen),
      "download projection is mutable"
    );
    const expectedKeys = [
      "canCancel",
      "canRetry",
      "currentBytes",
      "fileName",
      "id",
      "private",
      "progress",
      "sourceUrl",
      "status",
      "totalBytes",
    ];
    for (const download of downloads) {
      requireContract(
        JSON.stringify(Object.keys(download).sort()) ===
          JSON.stringify(expectedKeys),
        "download projection shape changed"
      );
      requireContract(
        typeof download.id === "string" &&
          typeof download.fileName === "string" &&
          typeof download.sourceUrl === "string" &&
          typeof download.private === "boolean" &&
          ["pending", "downloading", "complete", "canceled", "failed"].includes(
            download.status
          ) &&
          (download.progress === null ||
            (Number.isInteger(download.progress) &&
              download.progress >= 0 &&
              download.progress <= 100)) &&
          Number.isSafeInteger(download.currentBytes) &&
          download.currentBytes >= 0 &&
          Number.isSafeInteger(download.totalBytes) &&
          download.totalBytes >= 0 &&
          typeof download.canCancel === "boolean" &&
          typeof download.canRetry === "boolean",
        "download projection is not normalized plain data"
      );
    }
    emit("downloads-changed", {
      count: downloads.length,
      states: downloads.map(download => ({
        status: download.status,
        private: download.private,
      })),
    });
  },
});

runtime.setHistoryDelegate({
  onVisited: request => {
    requireContract(Object.isFrozen(request), "history visit is mutable");
    visitedUrls.add(request.url);
    emit("history-visited", { url: request.url });
    return true;
  },
  getVisited: urls => {
    requireContract(Object.isFrozen(urls), "history query is mutable");
    return urls.map(url => visitedUrls.has(url));
  },
  onTitleChanged: request => {
    requireContract(Object.isFrozen(request), "history title is mutable");
    emit("history-title", request);
  },
  onClear: () => visitedUrls.clear(),
});

runtime.setSessionDelegateFactory(session => ({
  content: {
    onStateChanged: state => {
      requireContract(Object.isFrozen(state), "session state is mutable");
      requireContract(Object.isFrozen(state.media), "media state is mutable");
      verifyNavigationProjection(session, state);
      if (
        state.url === silentInternalURI &&
        state.loadingActivity === "silent"
      ) {
        silentInternalNavigationIds.add(state.navigationId);
        silentInternalLifecycleObserved = true;
      }
      if (silentInternalNavigationIds.has(state.navigationId)) {
        requireContract(
          state.loadingActivity !== "visible",
          "allowlisted internal page exposed user-visible loading feedback"
        );
      }
      if (state.favicon) {
        requireContract(
          /^data:image\//u.test(state.favicon),
          "favicon projection is not a bounded image data URL"
        );
        if (!faviconProjected) {
          faviconProjected = true;
          emit("favicon-projected", {
            sessionId: session.id,
            bytes: state.favicon.length,
          });
        }
      }
      emit("content-state", {
        sessionId: session.id,
        navigationId: state.navigationId,
        navigationRevision: state.navigationRevision,
        url: state.url,
        loading: state.loading,
        loadingActivity: state.loadingActivity,
        crashed: state.crashed,
      });
      if (!state.loading && !state.crashed) {
        setTimeout(() => emitActiveContentProcess(session));
      }
      if (
        !ignoredStableLoadSessionIds.has(session.id) &&
        !state.loading &&
        state.url !== "about:blank" &&
        !state.crashed
      ) {
        stableLoads += 1;
        maybeQuit();
      }
    },
  },
  contextMenu: {
    onShow: request => {
      requireContract(Object.isFrozen(request), "context menu request is mutable");
      requireContract(
        Object.isFrozen(request.position) &&
          Object.isFrozen(request.context) &&
          Object.isFrozen(request.items) &&
          request.items.every(Object.isFrozen),
        "context menu request contains mutable nested data"
      );
      requireContract(
        !("actor" in request) &&
          !("target" in request) &&
          !("principal" in request) &&
          !("browsingContext" in request),
        "context menu request leaked a Gecko implementation object"
      );
      emit("context-menu-shown", {
        sessionId: session.id,
        id: request.id,
        commands: request.items.map(item => item.id),
      });
      const requestedCommand = Services.prefs.getCharPref(
        "desktop.embedder.shell.contextMenuCommand",
        ""
      );
      const offered = request.items.find(
        item => item.id === requestedCommand && item.enabled
      );
      const unofferedRejected =
        session.executeContextMenuCommand(
          request.id,
          "navis-test-unoffered-command"
        ) === false;
      requireContract(
        unofferedRejected,
        "context menu accepted an unoffered command"
      );
      setTimeout(() => {
        if (offered) {
          const accepted = session.executeContextMenuCommand(
            request.id,
            requestedCommand
          );
          requireContract(accepted, "offered context-menu command was rejected");
          const staleCommandRejected =
            session.executeContextMenuCommand(request.id, requestedCommand) ===
            false;
          const staleDismissRejected =
            session.dismissContextMenu(request.id) === false;
          requireContract(
            staleCommandRejected && staleDismissRejected,
            "consumed context-menu request remained usable"
          );
          emit("context-menu-contract", {
            sessionId: session.id,
            command: requestedCommand,
            accepted,
            unofferedRejected,
            staleCommandRejected,
            staleDismissRejected,
          });
        } else {
          session.dismissContextMenu(request.id);
        }
      });
      return true;
    },
    onDismissed: request => {
      requireContract(
        Object.isFrozen(request),
        "context menu dismissal is mutable"
      );
      emit("context-menu-dismissed", request);
    },
  },
  permission: {
    onRequest: request => {
      requireContract(Object.isFrozen(request), "permission request is mutable");
      requireContract(
        Object.isFrozen(request.permissions),
        "permission list is mutable"
      );
      emit("permission-request", {
        sessionId: session.id,
        id: request.id,
        permissions: request.permissions,
      });
      return Promise.resolve(
        Services.prefs.getCharPref(
          "desktop.embedder.shell.permissionDecision",
          "dismiss"
        )
      );
    },
    onCanceled: request =>
      emit("permission-canceled", {
        sessionId: session.id,
        id: request.id,
        reason: request.reason,
      }),
  },
  prompt: {
    onPrompt: request => {
      requireContract(Object.isFrozen(request), "prompt request is mutable");
      requireContract(
        Object.isFrozen(request.buttons) &&
          request.buttons.every(Object.isFrozen),
        "prompt buttons are mutable"
      );
      requireContract(
        !(
          "channel" in request ||
          "principal" in request ||
          "browsingContext" in request
        ),
        "prompt leaked a Gecko implementation object"
      );
      if (request.input) {
        requireContract(Object.isFrozen(request.input), "prompt input is mutable");
        if (request.input.choices) {
          requireContract(
            Object.isFrozen(request.input.choices),
            "prompt choices are mutable"
          );
        }
      }
      if (request.checkbox) {
        requireContract(
          Object.isFrozen(request.checkbox),
          "prompt checkbox is mutable"
        );
      }
      emit("prompt-request", {
        sessionId: session.id,
        id: request.id,
        kind: request.kind,
        beforeUnload: request.beforeUnload,
      });

      if (request.message === "Navis invalid prompt response") {
        return { action: "invalid" };
      }
      if (request.message === "Navis rejected prompt response") {
        return Promise.reject(new Error("intentional prompt rejection"));
      }
      if (request.message === "Navis undefined prompt response") {
        return undefined;
      }
      if (request.kind === "text") {
        return Promise.resolve({
          action: "accept",
          value: "delegate-value",
        });
      }
      if (request.kind === "auth") {
        return Promise.resolve({
          action: "accept",
          username: "navis",
          password: "embedder",
        });
      }
      return Promise.resolve({ action: "accept" });
    },
    onCanceled: request =>
      emit("prompt-canceled", {
        sessionId: session.id,
        id: request.id,
        reason: request.reason,
      }),
  },
  webAuthn: {
    onRequest: request => {
      requireContract(Object.isFrozen(request), "WebAuthn request is mutable");
      requireContract(
        Object.isFrozen(request.actions) &&
          Object.isFrozen(request.accounts) &&
          request.accounts.every(Object.isFrozen),
        "WebAuthn request contains mutable nested data"
      );
      requireContract(
        request.category === "webauthn" &&
          typeof request.origin === "string" &&
          typeof request.host === "string" &&
          !(
            "tid" in request ||
            "principal" in request ||
            "browsingContext" in request ||
            "credentialId" in request ||
            "device" in request
          ),
        "WebAuthn request leaked native transaction state"
      );
      emit("webauthn-request", {
        sessionId: session.id,
        id: request.id,
        kind: request.kind,
        actions: request.actions,
      });
      return Promise.resolve({ action: "cancel" });
    },
    onCanceled: request =>
      emit("webauthn-canceled", {
        sessionId: session.id,
        id: request.id,
        reason: request.reason,
      }),
  },
  media: runtime.capabilities.mediaCapture
    ? {
        onStateChanged: media => {
          requireContract(Object.isFrozen(media), "media callback is mutable");
          emit("media-state", media);
        },
      }
    : null,
  crash: {
    onCrash: details => {
      requireContract(Object.isFrozen(details), "crash details are mutable");
      emit("content-crash", details);
      if (
        Services.prefs.getBoolPref(
          "desktop.embedder.shell.restoreAfterCrash",
          false
        )
      ) {
        setTimeout(() => {
          const restored = session.restore();
          emit("restore-requested", {
            sessionId: session.id,
            restored,
          });
        });
      }
    },
  },
}));

const explicitDelegateProbeView = createView();
let explicitClosedStateHadLiveBrowser = false;
let explicitClosedStateCount = 0;
const explicitDelegateProbe = runtime.createSession({
  view: explicitDelegateProbeView,
  delegates: {
    content: {
      onStateChanged: state => {
        requireContract(Object.isFrozen(state), "explicit Session state is mutable");
        if (state.closed) {
          explicitClosedStateCount += 1;
          // The shell owns this chrome DOM, so it can prove that the terminal
          // callback runs before EngineSession releases its browser without
          // depending on any product-specific extension contribution.
          explicitClosedStateHadLiveBrowser = Boolean(
            viewHost.querySelector("browser.desktop-engine-view")
          );
        }
      },
    },
  },
});
requireContract(
  explicitDelegateProbe.view === explicitDelegateProbeView &&
    explicitDelegateProbeView.session === explicitDelegateProbe,
  "Session/View binding is not explicit and bidirectional"
);
requireContract(
  runtime.windowCount === 1,
  "first explicit View did not register its chrome window"
);
requireContract(
  typeof explicitDelegateProbe.addEventListener === "undefined",
  "Session still exposes the migration EventTarget surface"
);
requireContract(
  typeof explicitDelegateProbe.respondToPrompt === "undefined",
  "Session still exposes direct prompt responses"
);
explicitDelegateProbe.open("about:blank");
explicitDelegateProbe.close();
requireContract(
  explicitClosedStateHadLiveBrowser && explicitClosedStateCount === 1,
  "closed state was duplicated or delivered after the Session browser was released"
);
requireContract(runtime.sessionCount === 0, "closed probe session was retained");
requireContract(
  explicitDelegateProbeView.session === null && runtime.windowCount === 1,
  "closing a Session did not release its View or keep the Runtime window"
);
emit("explicit-delegates-complete");

const session = runtime.createSession({ view: createView() });
runtime.activateSession(session);
const [startupUri] = runtime.getStartupURIs({
  window,
  fallbackURI: "about:blank",
});
emit("runtime-ready", { sessionId: session.id, url: startupUri });
const openedStartupUri = session.open(startupUri);
const startupNavigationId = session.state.navigationId;

async function waitForSessionState(targetSession, predicate, message) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    const state = targetSession.state;
    if (predicate(state)) {
      return state;
    }
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  requireContract(false, message);
  return null;
}

const startupState = await waitForSessionState(
  session,
  state => !state.loading && state.url === openedStartupUri,
  "main Session did not complete its startup navigation"
);
requireContract(
  startupState.navigationId === startupNavigationId,
  "implicit about:blank consumed the startup Core navigation identity"
);
const startupMemory = await runtime.measureSessionMemoryUsage(session);
requireContract(
  Object.isFrozen(startupMemory) &&
    typeof startupMemory.available === "boolean" &&
    Number.isSafeInteger(startupMemory.bytes) &&
    startupMemory.bytes >= 0 &&
    Number.isSafeInteger(startupMemory.processCount) &&
    startupMemory.processCount >= 0 &&
    startupMemory.estimated === true,
  "tab memory projection is not normalized immutable data"
);
emit("session-memory", startupMemory);

async function verifyMultiWindowLifecycle() {
  const auxiliaryURI = document.documentURI.replace(
    /shell\.xhtml(?:\?.*)?$/,
    "auxiliary.xhtml"
  );
  const auxiliaryWindow = Services.ww.openWindow(
    window,
    auxiliaryURI,
    "_blank",
    "chrome,dialog=no,resizable",
    null
  );
  await new Promise(resolve => {
    auxiliaryWindow.addEventListener("load", resolve, { once: true });
  });

  const auxiliaryHost = auxiliaryWindow.document.getElementById("view-host");
  const auxiliaryView = new EngineView({
    document: auxiliaryWindow.document,
    host: auxiliaryHost,
  });
  const auxiliarySession = runtime.createSession({ view: auxiliaryView });
  ignoredStableLoadSessionIds.add(auxiliarySession.id);
  runtime.activateSession(auxiliarySession);
  const firstURI = auxiliarySession.open(
    "data:text/html,%3Ctitle%3EEngineView%20one%3C%2Ftitle%3E%3Ch1%3Eone%3C%2Fh1%3E"
  );
  const firstNavigationId = auxiliarySession.state.navigationId;
  await waitForSessionState(
    auxiliarySession,
    state => !state.loading && state.url === firstURI,
    "auxiliary Session did not complete its first lifecycle load"
  );
  requireContract(
    auxiliarySession.state.navigationId === firstNavigationId,
    "implicit about:blank consumed the auxiliary Core navigation identity"
  );
  const secondURI = auxiliarySession.loadUri(
    "data:text/html,%3Ctitle%3EEngineView%20two%3C%2Ftitle%3E%3Ch1%3Etwo%3C%2Fh1%3E"
  );
  const stateBeforeTransfer = await waitForSessionState(
    auxiliarySession,
    state => !state.loading && state.url === secondURI && state.canGoBack,
    "auxiliary Session did not establish transferable history"
  );
  const auxiliaryBounds = auxiliaryView.bounds;
  requireContract(
    auxiliaryView.visible &&
      Object.isFrozen(auxiliaryBounds) &&
      auxiliaryBounds.width > 0 &&
      auxiliaryBounds.height > 0,
    "active EngineView did not expose immutable visible bounds"
  );
  requireContract(
    auxiliaryView.focus() &&
      auxiliaryView.focused &&
      auxiliaryView.blur() &&
      !auxiliaryView.focused &&
      !auxiliaryView.blur(),
    "EngineView did not acquire and release input focus"
  );
  requireContract(
    runtime.windowCount === 2 && runtime.sessionCount === 2,
    "one Runtime did not own two independent window contexts"
  );
  requireContract(
    session.view.session === session &&
      auxiliaryView.session === auxiliarySession,
    "multi-window Session/View ownership crossed window contexts"
  );

  requireContract(
    auxiliaryView.detach() === auxiliarySession &&
      auxiliarySession.view === null &&
      auxiliaryView.session === null &&
      !auxiliaryView.attached &&
      !auxiliaryView.visible &&
      !auxiliaryView.focused &&
      auxiliaryView.detach() === null,
    "EngineView.detach() did not release presentation ownership"
  );

  const disconnectedHost = document.createElement("div");
  const disconnectedView = new EngineView({
    document,
    host: disconnectedHost,
  });
  let disconnectedAttachRejected = false;
  try {
    disconnectedView.attach(auxiliarySession);
  } catch {
    disconnectedAttachRejected = true;
  }
  requireContract(
    disconnectedAttachRejected &&
      disconnectedView.session === null &&
      auxiliarySession.view === null &&
      !auxiliarySession.state.closed,
    "failed View attachment did not preserve the detached Session"
  );
  requireContract(
    auxiliarySession.goBack(),
    "detached Session could not use its live browsing lifecycle"
  );
  await waitForSessionState(
    auxiliarySession,
    state => !state.loading && state.url === firstURI && state.canGoForward,
    "detached Session did not retain its back history"
  );

  const mainTransferView = createView();
  requireContract(
    mainTransferView.attach(auxiliarySession) === mainTransferView &&
      auxiliarySession.view === mainTransferView &&
      mainTransferView.session === auxiliarySession &&
      mainTransferView.attached &&
      mainTransferView.visible,
    "EngineView.attach() did not move the Session into the main window"
  );
  requireContract(
    runtime.windowCount === 2 &&
      runtime.sessionCount === 2 &&
      auxiliarySession.state.url === firstURI &&
      auxiliarySession.state.title !== stateBeforeTransfer.title &&
      auxiliarySession.state.canGoForward &&
      !session.view.visible &&
      !session.view.focus(),
    "cross-window attachment replaced the Session browsing lifecycle"
  );
  requireContract(
    mainTransferView.focus() &&
      mainTransferView.focused &&
      mainTransferView.blur() &&
      !mainTransferView.focused,
    "transferred View did not own input focus"
  );
  requireContract(
    auxiliarySession.goForward(),
    "attached Session lost the detached lifecycle's forward history"
  );
  await waitForSessionState(
    auxiliarySession,
    state => !state.loading && state.url === secondURI && state.canGoBack,
    "attached Session did not continue the detached browsing lifecycle"
  );
  let missingDetachRejected = false;
  try {
    auxiliaryView.attach(auxiliarySession);
  } catch {
    missingDetachRejected = true;
  }
  requireContract(
    missingDetachRejected &&
      auxiliarySession.view === mainTransferView &&
      mainTransferView.session === auxiliarySession &&
      auxiliaryView.session === null,
    "EngineSession accepted a second View without explicit detachment"
  );

  requireContract(
    mainTransferView.detach() === auxiliarySession &&
      auxiliaryView.attach(auxiliarySession) === auxiliaryView &&
      auxiliarySession.view === auxiliaryView &&
      mainTransferView.session === null,
    "Session could not make a second cross-window View transfer"
  );
  requireContract(
    auxiliarySession.goBack(),
    "transferred Session lost its back-history operation"
  );
  await waitForSessionState(
    auxiliarySession,
    state => !state.loading && state.url === firstURI && state.canGoForward,
    "transferred Session did not preserve its back history"
  );
  requireContract(
    auxiliarySession.goForward(),
    "transferred Session lost its forward-history operation"
  );
  await waitForSessionState(
    auxiliarySession,
    state => !state.loading && state.url === secondURI && state.canGoBack,
    "transferred Session did not preserve its forward history"
  );
  auxiliarySession.loadUri(silentInternalURI);
  await waitForSessionState(
    auxiliarySession,
    state => !state.loading && state.url === silentInternalURI,
    "allowlisted internal page did not complete its document lifecycle"
  );
  requireContract(
    silentInternalLifecycleObserved &&
      auxiliarySession.state.loadingActivity === "idle" &&
      auxiliarySession.state.identity === "internal-page",
    "internal lifecycle was not kept distinct from loading presentation"
  );
  requireContract(
    auxiliarySession.goBack(),
    "internal-page presentation test did not retain back navigation"
  );
  await waitForSessionState(
    auxiliarySession,
    state => !state.loading && state.url === secondURI,
    "internal-page presentation test did not return to web content"
  );
  emit("internal-loading-presentation-complete", {
    sessionId: auxiliarySession.id,
    url: silentInternalURI,
  });
  const slowURI = new URL("/slow", startupUri).href;
  const navigationBeforeSlow = {
    id: auxiliarySession.state.navigationId,
    revision: auxiliarySession.state.navigationRevision,
  };
  auxiliarySession.loadUri(slowURI);
  await waitForSessionState(
    auxiliarySession,
    state =>
      state.loading && state.loadingActivity === "visible" && state.url === slowURI,
    "Session did not enter a cancellable loading state"
  );
  requireContract(
    auxiliarySession.state.navigationId > navigationBeforeSlow.id &&
      auxiliarySession.state.navigationRevision > navigationBeforeSlow.revision,
    "load command did not allocate a new Core navigation identity"
  );
  const loadingRevision = auxiliarySession.state.navigationRevision;
  const stopDelayMs = Services.prefs.getIntPref(
    "desktop.embedder.shell.stopDelayMs",
    200
  );
  requireContract(
    Number.isInteger(stopDelayMs) && stopDelayMs >= 0 && stopDelayMs <= 1000,
    "loading-stop test delay is outside its bounded range"
  );
  if (stopDelayMs > 0) {
    await new Promise(resolve => setTimeout(resolve, stopDelayMs));
  }
  requireContract(
    auxiliarySession.stop(),
    "EngineSession.stop() did not cancel an active load"
  );
  await waitForSessionState(
    auxiliarySession,
    state => !state.loading && state.url === slowURI,
    "EngineSession.stop() did not clear loading state"
  );
  requireContract(
    auxiliarySession.state.navigationRevision > loadingRevision,
    "stop command did not commit a Core navigation transition"
  );
  await new Promise(resolve => setTimeout(resolve, 250));
  requireContract(
    !auxiliarySession.state.loading && !auxiliarySession.stop(),
    "stopped Session resumed loading or accepted an idle stop"
  );
  emit("loading-stop-complete", {
    sessionId: auxiliarySession.id,
    url: auxiliarySession.state.url,
  });
  emit("view-lifecycle-transfer-complete", {
    sessionId: auxiliarySession.id,
    url: auxiliarySession.state.url,
  });

  runtime.activateSession(session);
  requireContract(
    session.view.visible && session.view.focus() && session.view.focused,
    "reactivated main View did not regain presentation and input ownership"
  );
  emitActiveContentProcess(session);

  const unloaded = new Promise(resolve => {
    auxiliaryWindow.addEventListener("unload", resolve, { once: true });
  });
  auxiliaryWindow.close();
  await unloaded;
  await new Promise(resolve => setTimeout(resolve, 0));
  requireContract(
    !runtime.closed &&
      runtime.windowCount === 1 &&
      runtime.sessionCount === 1 &&
      auxiliarySession.state.closed &&
      auxiliaryView.session === null &&
      mainTransferView.session === null &&
      !session.state.closed,
    "auxiliary window unload closed the process Runtime or the wrong Session"
  );
  multiWindowComplete = true;
  emit("multi-window-lifecycle-complete", {
    sessionId: session.id,
    windows: runtime.windowCount,
  });
  maybeQuit();
}

await verifyMultiWindowLifecycle();

window.addEventListener(
  "unload",
  () => {
    runtime.close();
  },
  { once: true }
);
