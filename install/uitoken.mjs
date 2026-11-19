// Gets a VCF Operations UI session (cookie + secureToken) through headless Chrome.
// The UI hands out its anti-forgery token only to a real browser; the public API has no
// way to upload an unsigned management pack. Prints JSON {cookie, token}.
const port = process.env.CDP_PORT || 9333, host = process.env.OPS_HOST, pw = process.env.OPS_PW;
const sleep = ms => new Promise(r => setTimeout(r, ms));
let tabs; for (let i = 0; i < 40; i++) { try { tabs = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); break; } catch { await sleep(250); } }
const ws = new WebSocket(tabs.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.onopen = r);
let id = 0; const pending = new Map();
ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
const cdp = (method, params = {}) => new Promise(r => { pending.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async expr => (await cdp('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
await cdp('Page.enable');
await cdp('Page.navigate', { url: `https://${host}/ui/login.action` });
await sleep(6000);
const body = new URLSearchParams({ mainAction: 'login', userName: 'admin', password: pw, authSourceId: 'localItem',
  authSourceName: 'Local Users', authSourceType: '', forceLogin: 'true', timezone: '0', languageCode: 'us' }).toString();
const res = await ev(`fetch('login.action',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'},body:${JSON.stringify(body)}}).then(r=>r.text())`);
if (res !== 'ok') { console.error('login: ' + String(res).slice(0, 100)); process.exit(1); }
await cdp('Page.navigate', { url: `https://${host}/ui/index.action` });
let token; for (let i = 0; i < 60 && !token; i++) { await sleep(1000); token = await ev(`typeof secureToken==='undefined'?null:secureToken`); }
const cookies = (await cdp('Network.getCookies', { urls: [`https://${host}/ui/`] })).result.cookies;
console.log(JSON.stringify({ token, cookie: cookies.find(c => c.name === 'JSESSIONID')?.value }));
ws.close(); process.exit(token ? 0 : 2);
