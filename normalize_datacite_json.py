import json


def make_object(entry: list | object, field_name: str):
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
        'titles': make_array(input['titles'], 'title'),
        'subjects': make_array(input['subjects'], 'subject'),
        'creators': make_array(input['creators'], 'creator')
    }
