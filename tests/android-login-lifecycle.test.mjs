import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";

const root = new URL("../", import.meta.url);
const source = await readFile(new URL("gecko/mobile/shared/modules/navis/NavisAndroidLoginLifecycle.sys.mjs", root), "utf8");
const autocomplete = await readFile(new URL("gecko/mobile/shared/modules/geckoview/GeckoViewAutocomplete.sys.mjs", root), "utf8");

function fixture(code = source) {
  let id = 0;
  const Ci = {
    nsIWebProgressListener: { LOCATION_CHANGE_SAME_DOCUMENT: 1, STATE_STOP: 2 },
    nsIWebProgress: { NOTIFY_LOCATION: 1, NOTIFY_STATE_NETWORK: 2 },
  };
  const sandbox = { Ci, Services: {uuid: {generateUUID: () => (++id).toString(16).padStart(32,"0")}},
    ChromeUtils: {generateQI: () => () => {}} };
  vm.createContext(sandbox);
  vm.runInContext(code.replace("export const", "const") + "\nglobalThis.api=NavisAndroidLoginLifecycle", sandbox);
  const api = sandbox.api;
  function page(privateMode = false) {
    const context = {isDiscarded:false, isInBFCache:false, ancestorsAreCurrent:true,
      originAttributes:{privateBrowsingId:privateMode?1:0}};
    context.top=context;
    const browser = {browsingContext:context, documentGlobal:{navisAndroidTarget:{}},
      navisNativePresentation:false, listeners:[], addProgressListener(l){this.listeners.push(l)},
      removeProgressListener(l){this.listeners.splice(this.listeners.indexOf(l),1)}};
    function document(origin="https://login.example", loadInfo=null) {
      if(context.currentWindowGlobal)context.currentWindowGlobal.isCurrentGlobal=false;
      const global={innerWindowId:++id,isCurrentGlobal:true,browsingContext:context,
        documentPrincipal:{originNoSuffix:origin}, documentChannel:loadInfo?{loadInfo}:null};
      context.currentWindowGlobal=global;
      return {manager:global,browsingContext:context};
    }
    const actor=document();
    api.attach(browser);
    const location=()=>browser.listeners.forEach(l=>l.onLocationChange(null,null,null,0));
    return {browser,context,actor,document,location};
  }
  return {api,page};
}

function prompt(api,capture) {
  const p={dismissals:0,dismiss(){this.dismissals++}};
  assert.equal(api.bind(capture,p,capture.origin),true);
  return p;
}

test("same-document fetch removal remains valid, unrelated navigation revokes without URL heuristics",()=>{
  const {api,page}=fixture();const f=page();
  const c=api.createSave(f.actor,f.browser,"form-removal-after-fetch");const p=prompt(api,c);
  assert.equal(api.authorizeSave(f.browser,c.token),true);
  // DOM removal does not replace the WindowGlobal.
  f.location();assert.equal(p.dismissals,0);
  f.document();f.location(); // Same URL/origin, different document: not authority.
  assert.equal(p.dismissals,1);assert.equal(api.authorizeSave(f.browser,c.token),false);
  f.context.currentWindowGlobal=c.global;c.global.isCurrentGlobal=true;
  assert.equal(api.authorizeSave(f.browser,c.token),false); // Cannot revive on back/BFCache.
});

test("real POST redirect chain survives actor destruction and cross-origin landing, but only once",()=>{
  const {api,page}=fixture();const f=page();
  const c=api.createSave(f.actor,f.browser,"form-submit-event");const p=prompt(api,c);
  const next=f.document("https://account.example",{triggeringWindowId:c.global.innerWindowId,isFormSubmission:true});
  f.location();assert.equal(api.authorizeSave(f.browser,c.token),true);assert.equal(p.dismissals,0);
  assert.equal(c.landing,next.manager);
  f.document("https://account.example",{triggeringWindowId:c.global.innerWindowId,isFormSubmission:true});
  f.location();assert.equal(p.dismissals,1);assert.equal(api.authorizeSave(f.browser,c.token),false);
});

test("a COOP top-context replacement requires the actual submission channel, not its URL",()=>{
  for(const submitted of [true,false]) {
    const {api,page}=fixture();const f=page();
    const c=api.createSave(f.actor,f.browser,"form-submit-event");const p=prompt(api,c);
    const next=page();next.actor.manager.documentChannel={loadInfo:{
      triggeringWindowId:submitted?c.global.innerWindowId:999,isFormSubmission:true}};
    f.actor.manager.isCurrentGlobal=false;f.context.isDiscarded=true;
    f.browser.browsingContext=next.context;f.location();
    assert.equal(api.authorizeSave(f.browser,c.token),submitted);assert.equal(p.dismissals,submitted?0:1);
  }
});

test("native page presentation revokes hidden web prompts without reloading the page",()=>{
  const {api,page}=fixture();const f=page();
  const c=api.createSave(f.actor,f.browser,"form-removal-after-fetch");const p=prompt(api,c);
  f.browser.navisNativePresentation=true;api.reconcile(f.browser);
  assert.equal(p.dismissals,1);assert.equal(api.authorizeSave(f.browser,c.token),false);
  f.browser.navisNativePresentation=false;assert.equal(api.authorizeSave(f.browser,c.token),false);
});

test("wrong channel, non-form navigation, changed frame/ancestor, private saves fail closed",()=>{
  for(const info of [null,{triggeringWindowId:999,isFormSubmission:true}, {isFormSubmission:true},
      {triggeringWindowId:2,isFormSubmission:false}]) {
    const {api,page}=fixture();const f=page();const c=api.createSave(f.actor,f.browser,"form-submit-event");
    const p=prompt(api,c);f.document("https://login.example",info);f.location();
    assert.equal(api.authorizeSave(f.browser,c.token),false);assert.equal(p.dismissals,1);
  }
  for(const property of ["isDiscarded","isInBFCache"]) {
    const {api,page}=fixture();const f=page();const c=api.createSave(f.actor,f.browser);
    f.context[property]=true;assert.equal(api.authorizeSave(f.browser,c.token),false);
  }
  const {api,page}=fixture();const priv=page(true);
  assert.equal(api.createSave(priv.actor,priv.browser,"form-submit-event"),null);
  // Explicit selection of existing entries is not permission to persist a private login.
  const selection=api.createSelection(priv.actor,priv.browser);assert.ok(selection);
  assert.equal(api.authorizeSave(priv.browser,selection.token),false);
});

test("late old actors and callbacks cannot dismiss or replace newer prompt; tokens are browser-bound",()=>{
  const {api,page}=fixture();const f=page();const c=api.createSave(f.actor,f.browser,"form-submit-event");
  const p=prompt(api,c);
  const next=f.document("https://landing.example",{triggeringWindowId:c.global.innerWindowId,isFormSubmission:true});
  const newer=api.createSave(next,f.browser,"edit");const q=prompt(api,newer);
  assert.equal(p.dismissals,1);
  assert.equal(api.createSave(f.actor,f.browser,"form-submit-event"),null);
  api.release(c,true);assert.equal(q.dismissals,0);
  assert.equal(api.authorizeSave(f.browser,newer.token),true);
  assert.equal(api.authorizeSave(page().browser,newer.token),false);
  assert.equal(api.authorizeSave(f.browser,"arbitrary"),false);
  api.detach(f.browser);assert.equal(q.dismissals,1);assert.equal(f.browser.listeners.length,0);
});

test("a realm string cannot bypass form authority; trusted auth scope preserves original challenge behaviour",()=>{
  const {api,page}=fixture();const f=page();let called=0;
  const actual={promptToSavePassword(browser,login){called++;const c=api.current(browser);
    assert.ok(c);assert.equal(api.bind(c,{dismiss(){}},login.origin,login.httpRealm),true);return c;}};
  const login={origin:"https://login.example",httpRealm:"restricted",formActionOrigin:null};
  api.wrapPrompter(actual,f.browser,null).promptToSavePassword(f.browser,login);
  assert.equal(called,0);
  const form=api.createSave(f.actor,f.browser,"edit");
  assert.equal(api.bind(form,{dismiss(){}},login.origin,"restricted"),false);
  const auth=api.authPrompter(actual,f.browser,login).promptToSavePassword(f.browser,login);
  assert.equal(called,1);assert.equal(api.authorizeSave(f.browser,auth.token),true);
  assert.equal(api.current(f.browser),null); // No ambient authority after the call.
  const priv=page(true);api.authPrompter(actual,priv.browser,login).promptToSavePassword(priv.browser,login);
  assert.equal(called,1);
});

function method(text,marker) {
  const start=text.indexOf(marker);assert.ok(start>=0,marker);
  let open=text.indexOf("{",start),end=open+1,depth=1;
  while(depth) {depth+=(text[end]==="{")-(text[end]==="}");end++;}
  return text.slice(start,end);
}

test("actual selection callback rejects lost document and old actor dismissal cannot close a new picker",async()=>{
  const {api,page}=fixture();const f=page();let callback,fillReads=0;
  const lazy={AppConstants:{MOZ_GECKOVIEW:true}, NavisAndroidLoginLifecycle:api, GeckoViewPrompter:class {
    dismiss(){callback(null)} asyncShowPrompt(_payload,cb){callback=cb}
  }};
  const env={lazy,debug:()=>{},LoginEntry:{parse(value){fillReads++;return value}},SelectOption:class{constructor(x){Object.assign(this,x)}}};
  vm.createContext(env);
  const snippet=method(autocomplete,"onLoginSelect(aBrowser,")+","+method(autocomplete,"delegateDismiss(sourceActor");
  vm.runInContext("globalThis.actual={"+snippet+"}",env);
  const capture=api.createSelection(f.actor,f.browser);
  const pending=env.actual.onLoginSelect(f.browser,[{}],capture,f.actor);
  let dismissed=0;env.actual._prompt={dismiss(){dismissed++}};
  env.actual.delegateDismiss({});assert.equal(dismissed,0);
  env.actual.delegateDismiss(f.actor);assert.equal(dismissed,1);
  f.document();callback({selection:{value:{password:"synthetic"}}});
  await assert.rejects(pending);assert.equal(fillReads,0);
});

test("negative mutations remove real identity, form, private and newer-owner guards",()=>{
  const mutations=[
    ["info.triggeringWindowId !== global.innerWindowId","false"],
    ['capture.reason === "form-submit-event" && !info.isFormSubmission',"false"],
    ['(kind !== "select" && top.originAttributes.privateBrowsingId > 0)',"false"],
    ['kind === "save" && capture.landing && state.save && valid(state.save)',"false"],
  ];
  for(const [from,to] of mutations) {
    assert.ok(source.includes(from));const {api,page}=fixture(source.replace(from,to));const f=page();
    if(from.includes("privateBrowsingId")){const p=page(true);assert.ok(api.createSave(p.actor,p.browser));continue;}
    const c=api.createSave(f.actor,f.browser,"form-submit-event");
    if(from.includes("state.save")) {
      const next=f.document("https://landing.example",{triggeringWindowId:c.global.innerWindowId,isFormSubmission:true});
      api.createSave(next,f.browser);assert.ok(api.createSave(f.actor,f.browser,"form-submit-event"));continue;
    }
    f.document("https://other.example",{triggeringWindowId:from.includes("triggeringWindowId")?999:c.global.innerWindowId,
      isFormSubmission:!from.includes("isFormSubmission")});
    assert.equal(api.authorizeSave(f.browser,c.token),true);
  }
});

test("actual selection delegates filling only to its captured actor and balances synchronous failures",async()=>{
  const body=autocomplete.slice(autocomplete.indexOf("  async delegateSelection({"),autocomplete.indexOf("\n  delegateDismiss("));
  for(const privateMode of [false,true]) {
    const {api,page}=fixture();const f=page(privateMode);f.context.embedderElement=f.browser;
    let filled=0,writes=0;
    f.actor.manager.getActor=()=>({async fillForm(){filled++}});
    class SelectOption {constructor(x){Object.assign(this,x)}static Hint={NONE:0,GENERATED:1,FIREFOX_RELAY:2};}
    class LoginEntry {constructor(value){Object.assign(this,value)}toLoginInfo(){return this}}
    const sandbox={lazy:{AppConstants:{MOZ_GECKOVIEW:true},NavisAndroidLoginLifecycle:api},debug:()=>{},SelectOption,LoginEntry};
    vm.createContext(sandbox);vm.runInContext("globalThis.actual={_numActiveSelections:0,"+body+"}",sandbox);
    const actual=sandbox.actual;
    actual.onLoginSave=()=>writes++;
    actual.onLoginSelect=async(_browser,options)=>options[0];
    const request={browsingContext:f.context,sourceActor:f.actor,
      options:[{style:"generatedPassword",comment:JSON.stringify({generatedPassword:"synthetic"})}],
      inputElementIdentifier:"original-input",formOrigin:"https://login.example"};
    await actual.delegateSelection(request);
    assert.equal(filled,1);assert.equal(writes,privateMode?0:1);assert.equal(actual._numActiveSelections,0);
    actual.onLoginSelect=()=>{throw Error("transport failed")};
    await assert.rejects(actual.delegateSelection(request));
    assert.equal(actual._numActiveSelections,0);assert.equal(actual._promptOwner,null);
    actual.onLoginSelect=async(_browser,options)=>{f.document();return options[0]};
    await actual.delegateSelection(request);assert.equal(filled,1);assert.equal(actual._numActiveSelections,0);
  }
});

test("actual LoginManager child retains submission reason and parent captures before async store lookup",async()=>{
  const child=await readFile(new URL("gecko/toolkit/components/passwordmgr/LoginManagerChild.sys.mjs",root),"utf8");
  const sandbox={lazy:{log(){},FORM_SUBMISSION_REASON:{FORM_REMOVAL_AFTER_FETCH:"form-removal-after-fetch"}}};
  vm.createContext(sandbox);vm.runInContext("globalThis.actual={"+method(child,"_onFormSubmit(form, reason)")+"}",sandbox);
  for(const reason of ["form-submit-event","form-removal-after-fetch","page-navigation"]) {
    let sent;sandbox.actual._maybeSendFormInteractionMessage=(...args)=>sent=args;
    sandbox.actual._onFormSubmit({},reason);
    assert.equal(sent[1],"PasswordManager:ShowDoorhanger");assert.equal(sent[2].submissionReason,reason);
    assert.equal(sent[2].ignoreConnect,reason==="form-removal-after-fetch");
  }
  const parent=await readFile(new URL("gecko/toolkit/components/passwordmgr/LoginManagerParent.sys.mjs",root),"utf8");
  const start=parent.indexOf("  async showDoorhanger(");const end=parent.indexOf("\n  async _onPasswordEditedOrGenerated",start);
  const part=parent.slice(start,end);
  // Execute the actual entry up to its first async lookup, not a copy of the
  // policy. Candidate capture must happen before any yielded credential work.
  const prefix=part.slice(0,part.indexOf("    async function recordLoginUse("));
  const {api,page}=fixture();const f=page();let seen=0;
  const env={lazy:{AppConstants:{MOZ_GECKOVIEW:true},NavisAndroidLoginLifecycle:{createSave(...args){seen++;return api.createSave(...args)}}}};
  vm.createContext(env);vm.runInContext("globalThis.actual={"+prefix+"return navisCapture;}}",env);
  Object.assign(env.actual,f.actor);
  const result=env.actual.showDoorhanger(f.browser,"https://login.example",{submissionReason:"form-submit-event"});
  assert.equal(seen,1);const capture=await result;assert.equal(capture.global,f.actor.manager);
  assert.equal(capture.reason,"form-submit-event");
  env.lazy.AppConstants.MOZ_GECKOVIEW=false;
  assert.equal(await env.actual.showDoorhanger(f.browser,"https://login.example",{}),null);
  assert.equal(seen,1); // A desktop build never enters the Android authority path.
  // Both async relay layers must retain the reason; prevent regression to the
  // old isSubmission-only interface before reaching the parent.
  assert.match(child,/\.\.\.fields,\s*isSubmission,\s*submissionReason,/);
  assert.match(child,/let detail = \{[\s\S]*?submissionReason,/);
});
