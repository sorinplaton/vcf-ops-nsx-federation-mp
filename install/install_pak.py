"""Upload and install an unsigned management pack through the VCF Operations UI route.
usage: install_pak.py <session.json> <pak>   (needs 'unsigned PAK installation' enabled)"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opsui
s = opsui.session(sys.argv[1]); pak = sys.argv[2]
d = opsui.action(s, 'solution', 'getIntegrations').json()
print('unsigned allowed:', d.get('isUnsignedPakInstallationAllowed'))
print('prepare:', opsui.action(s, 'utility', 'prepareFileUpload').status_code)
uid = int(time.time() * 1000)
r = s.post(opsui.BASE + f'admin/services/solution/upload?uploadId={uid}&ignoreSignatureChecking=true&secureToken={s.tok}',
           data={'forceUpload': 'true', 'forceContent': 'true', 'secureToken': s.tok},
           files={'solution': (pak.split('/')[-1], open(pak, 'rb'), 'application/octet-stream')}, timeout=600)
print('upload:', r.status_code, r.text[:600])
res = r.json()
if res.get('errorMsg') or res.get('success') is False: raise SystemExit('upload failed')
print('eula:', opsui.action(s, 'solution', 'getLicenseAgreement').text[:120])
r = opsui.action(s, 'solution', 'install'); print('install:', r.status_code, r.text[:300])
try:
    answer = r.json()
except ValueError:
    answer = {}
if r.status_code >= 300 or answer.get('errorMsg') or answer.get('success') is False:
    raise SystemExit('install refused: ' + (answer.get('errorMsg') or r.text[:200]))
seen, errors = 0, 0
for _ in range(120):
    time.sleep(10)
    try:  # the UI may restart during an install: a poll without an answer is tried again
        st = opsui.action(s, 'solution', 'getInstallStatus').json()
        errors = 0
    except Exception as e:
        errors += 1
        if errors >= 12:
            raise SystemExit('no install status for two minutes: ' + repr(e)[:120])
        continue
    for x in st.get('statuses', [])[seen:]: print('  ', json.dumps(x)[:200])
    seen = len(st.get('statuses', []))
    if st.get('errorMsg'): raise SystemExit('install error: ' + st['errorMsg'])
    if st.get('completed'): print('completed'); break
else:
    raise SystemExit('install did not complete in 20 minutes')
