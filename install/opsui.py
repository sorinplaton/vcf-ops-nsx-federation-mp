"""VCF Operations product-UI session helper. Used only for what the public API lacks: uploading
and installing an unsigned management pack. The session (cookies + anti-forgery token) comes
from a logged-in headless Chrome: uitoken.mjs, then uisess.mjs. Needs OPS_HOST."""
import json, os, requests, urllib3
urllib3.disable_warnings()
BASE = f"https://{os.environ['OPS_HOST']}/vcf-operations/plug/ops/"

def session(path):
    d = json.load(open(path))
    s = requests.Session(); s.verify = False
    for c in d['cookies']:
        s.cookies.set(c['name'], c['value'], domain=c['domain'], path=c['path'])
    s.tok = d['token']
    return s

def action(s, name, main, **params):
    params.update(mainAction=main, secureToken=s.tok)
    return s.post(BASE + name + '.action', data=params)
