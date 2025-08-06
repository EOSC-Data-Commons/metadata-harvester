from normalize_datacite_json import normalize_datacite_json
import json

with open('DANS_json/doi_10.17026_DANS-22C-9KMH.oai_datacite.xml.json') as f:
    print(normalize_datacite_json(json.load(f)['record']['metadata']['resource']))


with open('DABAR_json/oai_dabar.srce.hr_agr_3356.oai_datacite.xml.json') as f:
    print(normalize_datacite_json(json.load(f)['record']['metadata']['resource']))
