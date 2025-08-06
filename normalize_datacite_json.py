import json

def make_object(entry: list | object):
    #print(entry)
    if isinstance(entry, list):
        res =  list(map(lambda fi: {'title': fi}, entry))
        return res
    else:
        return entry

def make_array(field: dict | list):
    #print(json.dumps(field))

    if isinstance(field, dict):
        print('dict')
        res =  list(map(make_object, field.values()))
        return [x for sublist in res for x in sublist]
    elif isinstance(field, list):
        print('list')
        return field

def normalize_datacite_json(input: dict):
    #print(json.dumps(input))

    return make_array(input['titles'])




