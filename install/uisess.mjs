// Reads the UI session of the already logged-in headless Chrome: the anti-forgery token lives
// in the /vcf-operations/plug/ops iframe. Prints JSON {token, cookies:[{name,value,path}]}.
const tabs = await (await fetch(`http://127.0.0.1:${process.env.CDP_PORT || 9333}/json`)).json();
const ws = new WebSocket(tabs.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.onopen = r);
let id = 0; const pending = new Map(); const ctxs = [];
ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } if (d.method === 'Runtime.executionContextCreated') ctxs.push(d.params.context); };
const cdp = (method, params = {}) => new Promise(r => { pending.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
await cdp('Runtime.enable'); await new Promise(r => setTimeout(r, 800));
let token = null;
for (const c of ctxs) {
  const r = await cdp('Runtime.evaluate', { contextId: c.id, returnByValue: true, expression: `typeof secureToken==='undefined'?null:secureToken` });
  token = token || r.result?.result?.value;
}
const cookies = (await cdp('Network.getAllCookies')).result.cookies.map(c => ({ name: c.name, value: c.value, path: c.path, domain: c.domain }));
console.log(JSON.stringify({ token, cookies })); ws.close(); process.exit(token ? 0 : 2);
