// Run the actual ping-request builders with mocked HTTP (no live listings).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.join(__dirname, "../static");
const posts = [
  {id:"waiting", addr:"192.0.2.1:10800", net_status:3, giuroll:true, autopunch:true},
  {id:"playing", addr:"192.0.2.2:10800", net_status:4, guest_connected:true, giuroll:true},
  {id:"vanilla", addr:"192.0.2.3:10800", net_status:3, giuroll:false},
];
const sent = [];
const context = vm.createContext({
  window:{}, console, AbortController, setTimeout, clearTimeout,
  loggedIn:true, pingInFlight:false, clientPingAvailable:true,
  posts:new Map(posts.map(p => [p.id,p])), pingByPostId:new Map(),
  render:()=>{}, checkClientPingApi:async()=>true,
  shouldReportHighPingForPost:()=>false, LOCAL_API_BASE:"http://127.0.0.1:49152",
  fetch:async (url, options) => {
    if (url.endsWith("/lobby/ping")) sent.push(JSON.parse(options.body).targets);
    return {ok:true, json:async()=>({ok:true,results:[]})};
  },
});
vm.runInContext(fs.readFileSync(path.join(root,"favicon-notify.js"),"utf8"), context);
context.parsePostAddr = context.window.AsobbyNotify.parsePostAddr;
const html = fs.readFileSync(path.join(root,"index.html"),"utf8");
vm.runInContext(html.slice(html.indexOf("async function refreshHostPings()"),
  html.indexOf("function schedulePingRefresh()")), context);
(async () => {
  await context.refreshHostPings();
  await context.window.AsobbyNotify.refreshPostPings(posts, new Map());
  for (const targets of sent) {
    assert.deepEqual(targets.map(p=>p.id),["waiting","playing","vanilla"]);
    assert.deepEqual(targets.map(p=>p.giuroll),[true,true,false]);
    assert.deepEqual(targets.map(p=>p.autopunch),[true,false,false]);
  }
  assert.equal(sent.length,2);
  console.log("Lobby and background ping builders: capability flags preserved");
})().catch(error => {console.error(error); process.exitCode=1;});
