/* AURORA for Zotero 7: a bootstrapped plugin. The work is in aurora.js. */
var Aurora;

function install() {}

async function startup({ id, version, rootURI }) {
  await Zotero.initializationPromise;
  Services.scriptloader.loadSubScript(rootURI + "aurora.js");
  Aurora.init({ id, version, rootURI });
  Zotero.PreferencePanes.register({ pluginID: id, src: rootURI + "prefs.xhtml", label: "AURORA", image: rootURI + "icons/20.png" });
  for (const win of Zotero.getMainWindows()) Aurora.addToWindow(win);
}

function onMainWindowLoad({ window }) { Aurora && Aurora.addToWindow(window); }
function onMainWindowUnload({ window }) { Aurora && Aurora.removeFromWindow(window); }

function shutdown() {
  if (Aurora) Aurora.shutdown();
  Aurora = undefined;
}

function uninstall() {}
