import sys

DATACITE = 'http://datacite.org/schema/kernel-4'
XML = 'http://www.w3.org/XML/1998/namespace'

def get_identifier(entry: dict, identifier_type: str):
    if identifier := entry.get(f'{DATACITE}:identifier'):
        if id_type := identifier.get(f'@identifierType'):
            if id_type == identifier_type and '#text' in identifier:
                return identifier['#text']

    #print(f'No DOI given for {entry}')
    return None

def harmonize_creator(entry: dict):
    cr =  entry['creator']

    if isinstance(cr['creatorName'], str):
        return {
            'name': cr['creatorName']
        }
    else:
        return {
            **harmonize_props(cr, 'creatorName', {'@nameType': 'nameType'})
        }



def harmonize_props(entry: dict, field_name: str, value_map: dict):
    #print(type(entry), field_name, entry)

    if isinstance(entry[field_name], str):
        return entry
    elif isinstance(entry[field_name], dict):
        harmonized_entry =  {}

        if '#text' in entry[field_name]:
            harmonized_entry[field_name] = entry[field_name]['#text']

        for k, v in value_map.items():
            if entry[field_name].get(k) is not None:
                harmonized_entry[v] = entry[field_name][k]

        return harmonized_entry

    else:
        raise Exception('Neither string nor dict')


def make_object(subfield: list | dict, subfield_name: str):
    '''
    Given a subfield, turn it into a dict.

    :param subfield: subfield's value, could be a list of values or a single item.
    :param subfield_name: subfield's name, e.g., 'datacite:title' or 'datacite:subject'.
    :return: A dict for each subfield.
    '''
    if isinstance(subfield, list):
        res = list(map(lambda fi: {subfield_name: fi}, subfield))
        return res
    else:
        return [{subfield_name: subfield}]


def make_array(field: dict | list | None, subfield_name: str):
    '''
    Given a field value like 'datacite:titles' or 'datacite:subjects',
    returns an array of objects with the subfield name as an index.

    :param field: name of the field, e.g., 'datacite:titles' or 'datacite:subjects'.
    :param subfield_name: name og the subfield, e.g., 'datacite:title' or 'datacite:subject'.
    :return: a list of objects with the subfield name as an index.
    '''

    if field is None:
        return []

    if isinstance(field, dict):
        # field is a dict, thus the subfield is an object or a list
        res = list(map(lambda val: make_object(val, subfield_name), field.values()))
        return [x for sublist in res for x in sublist]
    elif isinstance(field, list):
        # field is a list
        return field
    else:
        raise Exception('Neither dict nor list')


def normalize_datacite_json(input: dict):
    # print(json.dumps(input))

    try:
        return {
            'doi': get_identifier(input, 'DOI'),
            'url': get_identifier(input, 'URL'),
            'titles': list(map(lambda el: harmonize_props(el, f'{DATACITE}:title', {f'@{XML}:lang': 'lang', '@titleType': 'titleType' }), make_array(input.get(f'{DATACITE}:titles'), f'{DATACITE}:title'))),
            'subjects': list(map(lambda el: harmonize_props(el, f'{DATACITE}:subject', {f'@{XML}:lang': 'lang'}), make_array(input.get(f'{DATACITE}:subjects'), f'{DATACITE}:subject'))),
            #'creators': list(map(lambda cr: harmonize_creator(cr), make_array(input.get('creators'), 'creator'))),
            'publicationYear': input.get('http://datacite.org/schema/kernel-4:publicationYear'),
            #'descriptions': list(map(lambda el: harmonize_props(el, 'description', {'@descriptionType': 'descriptionType', '@xml:lang': 'lang'}), make_array(input.get('descriptions'), 'description')))
        }

    except Exception as e:
        print(f'Error {str(e)} when processing {input}', file=sys.stderr)