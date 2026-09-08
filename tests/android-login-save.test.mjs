import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";

const producer = await readFile(new URL("../gecko/mobile/shared/modules/navis/NavisAndroidGeckoProducer.sys.mjs", import.meta.url), "utf8");
const delegate = await readFile(new URL("../gecko/mobile/shared/components/geckoview/LoginStorageDelegate.sys.mjs", import.meta.url), "utf8");

function wire(privateMode = false, response = {accepted: true, index: 0, navisSaved: true}) {
  const requests = [];
  const sandbox = { ChromeUtils: {importESModule: () => ({ActorManagerParent: {}})} };
  vm.createContext(sandbox);
  vm.runInContext(producer.replaceAll("export ", "") + "\nglobalThis.factory=createNavisGeckoEventDispatcher", sandbox);
  const dispatcher = sandbox.factory({
    targetBridge: {prompt: async p => { requests.push(p); return response; }},
    rawDispatcher: {}, isLive: () => true,
    getBrowser: () => ({currentURI: {spec:"https://example.com/result"}, browsingContext: {originAttributes: {privateBrowsingId: privateMode ? 1 : 0}}}),
  });
  return {requests, dispatcher};
}

test("save transports private candidates but public metadata contains no secret", async () => {
  const f = wire();
  const result = await f.dispatcher.sendRequestForResult("GeckoView:Prompt", {prompt: {
    id: "save1", type: "Autocomplete:Save:Login", options: {password:"must-not-leak"},
    logins: [{origin:"https://example.com", username:"fixture", password:"only-private-candidate"}],
  }});
  assert.equal(f.requests[0].password, "");
  assert.doesNotMatch(f.requests[0].loginOptions, /password|only-private|must-not-leak/);
  assert.equal(f.requests[0].loginCandidates[0].password, "only-private-candidate");
  assert.equal(result.navisSaved, true);
  assert.equal(result.selection.value.password, "only-private-candidate");
});

test("private save/decline do not authorize storage and selection never mints acknowledgement", async () => {
  const prompt = {id:"s", type:"Autocomplete:Save:Login", logins:[{origin:"https://example.com",password:"secret"}]};
  const priv = wire(true);
  assert.equal(await priv.dispatcher.sendRequestForResult("GeckoView:Prompt", {prompt}), null);
  assert.equal(priv.requests.length, 0);
  const decline = wire(false, {accepted:false, type:"Dismiss", navisSaved:true});
  assert.equal(await decline.dispatcher.sendRequestForResult("GeckoView:Prompt", {prompt}), null);
  const select = wire();
  const result = await select.dispatcher.sendRequestForResult("GeckoView:Prompt", {prompt:{...prompt,type:"Autocomplete:Select:Login"}});
  assert.equal(result.navisSaved, false);
  assert.equal(select.requests[0].loginCandidates.length, 0);
});

function saveConsumer(navis = true, privateMode = false, geckoView = true) {
  let callback;
  const notifications = [], duplicateWrites = [];
  const sandbox = {
    GeckoViewUtils: {initLogging: () => ({})},
    Components: {ID: x => x},
    Services: {obs: {notifyObservers: (...args) => notifications.push(args)}},
    ChromeUtils: {generateQI: () => () => {}, defineESModuleGetters(lazy) {
      Object.assign(lazy, { AppConstants:{MOZ_NAVIS_CORE:navis,MOZ_GECKOVIEW:geckoView}, PrivateBrowsingUtils:{isBrowserPrivate:()=>privateMode},
        LoginEntry:{fromLoginInfo:x=>x,parse:x=>({toLoginInfo:()=>x})},
        GeckoViewAutocomplete:{onLoginSave:x=>duplicateWrites.push(x)},
        NavisAndroidLoginLifecycle:{current:()=>({token:"1".repeat(32)}), bind:()=>true, release(){}},
        GeckoViewPrompter: class {asyncShowPrompt(_msg, cb){callback=cb;} dismiss(){}},
      });
    }},
  };
  vm.createContext(sandbox);
  vm.runInContext(delegate.replace(/import \{ GeckoViewUtils \} from [^;]+;/, "").replace("export class", "class") + "\nglobalThis.instance=new LoginStorageDelegate", sandbox);
  return {instance:sandbox.instance, notifications, duplicateWrites, reply:value=>callback?.(value)};
}

test("Gecko save/update observers follow the native commit, never duplicate-write or acknowledge missing commit", () => {
  for (const update of [false,true]) {
    const c=saveConsumer();
    const value={origin:"https://example.com",username:"fixture",password:"secret"};
    if(update)c.instance.promptToChangePassword({},null,value); else c.instance.promptToSavePassword({},value);
    c.reply({selection:{value}});
    c.reply(null);
    assert.equal(c.notifications.length,0);
    c.reply({selection:{value},navisSaved:true});
    assert.equal(c.notifications.length,1);
    assert.equal(c.duplicateWrites.length,0);
  }
  const p=saveConsumer(true,true);
  p.instance.promptToSavePassword({},{});
  p.reply({selection:{value:{}},navisSaved:true});
  assert.equal(p.notifications.length,0);
  const upstream=saveConsumer(false);
  upstream.instance.promptToSavePassword({},{});
  upstream.reply({selection:{value:{}}});
  assert.equal(upstream.duplicateWrites.length,1);
  const desktop=saveConsumer(true,false,false);
  desktop.instance.promptToSavePassword({},{});
  desktop.reply({selection:{value:{}}});
  assert.equal(desktop.notifications.length,1);
  assert.equal(desktop.duplicateWrites.length,1);
});
