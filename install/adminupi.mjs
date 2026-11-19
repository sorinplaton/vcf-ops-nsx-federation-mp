// VCF Operations admin console through headless Chrome: read or switch "unsigned PAK installation".
// usage: OPS_HOST=.. OPS_PW=.. node adminupi.mjs getUPIStatus|enableUPI|disableUPI
const port = process.env.CDP_PORT || 9333, host = process.env.OPS_HOST, pw = process.env.OPS_PW, act = process.argv[2] || 'getUPIStatus';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const tab = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(tab.webSocketDebuggerUrl); await new Promise(r => ws.onopen = r);
let id = 0; const pending = new Map();
ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
const cdp = (method, params = {}) => new Promise(r => { pending.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async expr => (await cdp('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
const form = o => JSON.stringify(new URLSearchParams(o).toString());
const post = (url, o) => `fetch('${url}',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'},body:${form(o)}}).then(r=>r.text())`;
await cdp('Page.enable'); await cdp('Page.navigate', { url: `https://${host}/admin/login.action` }); await sleep(5000);
if (!(await ev('location.href')).includes('index.action')) {
  const res = await ev(post('login.action', { mainAction: 'login', userName: 'admin', password: pw, languageCode: 'us' }));
  if (res !== 'ok') { console.error('admin login: ' + String(res).slice(0, 100)); process.exit(1); }
  await cdp('Page.navigate', { url: `https://${host}/admin/index.action` });
}
let token; for (let i = 0; i < 40 && !token; i++) { await sleep(1000); token = await ev(`typeof secureToken==='undefined'?null:secureToken`); }
if (!token) { console.error('no token; at ' + await ev('location.href')); process.exit(2); }
console.log(act, '->', await ev(post('clusterManagement.action', { mainAction: act, secureToken: token })));
if (act !== 'getUPIStatus') { await sleep(3000); console.log('status ->', await ev(post('clusterManagement.action', { mainAction: 'getUPIStatus', secureToken: token }))); }
await fetch(`http://127.0.0.1:${port}/json/close/${tab.id}`); ws.close(); process.exit(0);
