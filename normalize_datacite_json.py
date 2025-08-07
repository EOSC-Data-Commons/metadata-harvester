

def harmonize_creator(entry: dict):
    cr =  entry['creator']

    if isinstance(cr['creatorName'], str):
        return {
            'creatorName': cr['creatorName']
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
        harmonized_entry =  {
            field_name: entry[field_name]['#text'],
        }

        for k, v in value_map.items():
            if entry[field_name].get(k) is not None:
                harmonized_entry[v] = entry[field_name][k]

        return harmonized_entry

    else:
        raise Exception('Neither string nor dict')


def make_object(entry: list | dict, field_name: str):
    #print(entry)
    if isinstance(entry, list):
        res = list(map(lambda fi: {field_name: fi}, entry))
        return res
    else:
        return [{field_name: entry}]


def make_array(field: dict | list, field_name: str):
    #print(field.items())

    if isinstance(field, dict):
        # print('dict')
        #print(field.values())
        res = list(map(lambda val: make_object(val, field_name), field.values()))
        return [x for sublist in res for x in sublist]
    elif isinstance(field, list):
        # print('list')
        return field
    else:
        raise Exception('Neither dict nor list')


def normalize_datacite_json(input: dict):
    # print(json.dumps(input))

    return {
        'titles': list(map(lambda el: harmonize_props(el, 'title', {'@xml:lang': 'lang', '@titleType': 'titleType' }), make_array(input['titles'], 'title'))),
        'subjects': list(map(lambda el: harmonize_props(el, 'subject', {'@xml:lang': 'lang'}), make_array(input['subjects'], 'subject'))),
        'creators': list(map(lambda cr: harmonize_creator(cr), make_array(input['creators'], 'creator'))),
        'publicationYear': input.get('publicationYear')
    }
