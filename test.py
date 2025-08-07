from normalize_datacite_json import normalize_datacite_json
import json

with open('DANS_json/doi_10.17026_DANS-22C-9KMH.oai_datacite.xml.json') as f:
    one = normalize_datacite_json(json.load(f)['record']['metadata']['resource'])


with open('DABAR_json/oai_dabar.srce.hr_agr_3356.oai_datacite.xml.json') as f:
    two = normalize_datacite_json(json.load(f)['record']['metadata']['resource'])

with open('DABAR_json/oai_dabar.srce.hr_agr_1630.oai_datacite.xml.json') as f:
    three = normalize_datacite_json(json.load(f)['record']['metadata']['resource'])


with open('res.json', 'w') as f:
    f.write(json.dumps([one, two, three]))
