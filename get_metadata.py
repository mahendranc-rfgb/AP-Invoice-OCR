import app.sap_client as sc
import json
s, sid = sc.login_and_get_session()
res = s.get(sc.settings.base_url + '/$metadata', verify=False)
with open('metadata.xml', 'w', encoding='utf-8') as f:
    f.write(res.text)
sc.logout(s)
