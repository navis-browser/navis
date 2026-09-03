/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

if (
  Services.prefs.getBoolPref(
    "desktop.embedder.shell.traceMissingServices",
    false
  )
) {
  const { XPCOMUtils } = ChromeUtils.importESModule(
    "resource://gre/modules/XPCOMUtils.sys.mjs"
  );
  XPCOMUtils.defineLazyServiceGetter = (
    target,
    name,
    contract,
    interfaceType
  ) => {
    ChromeUtils.defineLazyGetter(target, name, () => {
      const component = Cc[contract];
      if (!component) {
        dump(
          `DESKTOP_EMBEDDER_SHELL_EVENT ${JSON.stringify({
            type: "missing-service",
            name,
            contract,
          })}\n`
        );
      }
      return component.getService(interfaceType);
    });
  };
}

import("./shell.mjs").catch(error => {
  const message = String(error?.stack || error);
  console.error("Desktop Gecko Embedder test shell failed", error);
  dump(`DESKTOP_EMBEDDER_SHELL_FAILURE ${message}\n`);
  document.getElementById("status").value = `Startup failed: ${error}`;
  setTimeout(() => window.close(), 100);
});
