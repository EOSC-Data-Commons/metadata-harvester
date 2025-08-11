import unittest
import json
from utils import normalize_datacite_json


class TestNormalizeDatacite(unittest.TestCase):

    def test_make_array_from_object(self):
        with open('tests/testdata/doi_10.17026_dans-2ab-dpmm.oai_datacite.xml.json') as f:
            data = json.load(f)['http://www.openarchives.org/OAI/2.0/:record'][
                'http://www.openarchives.org/OAI/2.0/:metadata']['http://datacite.org/schema/kernel-4:resource']
        res = normalize_datacite_json.make_array(data.get('http://datacite.org/schema/kernel-4:titles'), 'http://datacite.org/schema/kernel-4:title')

        self.assertEqual(len(res),1)

    def test_make_array_from_field_list(self):
        with open('tests/testdata/doi_10.17026_SS_78HHDK.oai_datacite.xml.json') as f:
            data = json.load(f)['http://www.openarchives.org/OAI/2.0/:record'][
                'http://www.openarchives.org/OAI/2.0/:metadata']['http://datacite.org/schema/kernel-4:resource']
        res = normalize_datacite_json.make_array(data.get('http://datacite.org/schema/kernel-4:titles'), 'http://datacite.org/schema/kernel-4:title')

        self.assertEqual(len(res), 2)
