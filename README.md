# Repository for metadata harvesters

For PoC we are only using repositories that expose their metadata via OAI-PMH, so there is currently only one script for harvesting metadata, and it is based on oaipmh-scythe Python client. 
This is work in progress.

## harvester-oaipmh
- Supports initial and incremental harvesting
- Creates a 'harvests' folder for each repository and saves results of each harvest in a separate XML file
- Stores last harvest date for each repository in a txt file (temporary, we plan to save all of config data for repositories, including last harvest date, in a json file)
- Runs from command line with URL of repository as the only parameter

## Requirements
- [Python](https://www.python.org/downloads/) >= 3.10
- see requirements.txt 
```sh
pip install -r requirements.txt
```

## Usage

### Harvesting

Run from the command line:
```sh
python harvester-oaipmh.py repos_config/{config_file.json}
```

### Transforming Harvested Data

Harvested data can be transformed to JSON. 
Currently, only `oai_datacite` metadata prefix is supported.  

```sh
python transform.py -i harvests_{repo_suffix} -o {repo_suffix}_json [-n]
```

If the -n flag is provided, the JSON data will also be normalized and validated against `schema.json`. 


## Output
Harvested XML files are saved as initial_harvest_{date}.xml or harvest_{date}.xml in folders harvests_{repository}.

Last harvest date is saved in last_harvest_{repository}.txt for the purpose of incremental harvesting.

## License

This projects has the following dependencies:

- [jsonschema](https://github.com/python-jsonschema/jsonschema): [MIT](https://github.com/python-jsonschema/jsonschema/blob/main/COPYING)
- [lxml](https://github.com/lxml/lxml): [BSD](https://github.com/lxml/lxml/blob/master/LICENSE.txt)
- [oaipmh-scythe](https://github.com/afuetterer/oaipmh-scythe): [BSD](https://github.com/afuetterer/oaipmh-scythe/blob/master/LICENSEBSD)
- [xmltodict](https://github.com/martinblech/xmltodict): [MIT](https://github.com/martinblech/xmltodict/blob/master/LICENSE)

### Development Dependencies
- [mypy](https://github.com/python/mypy): [MIT](https://github.com/python/mypy/blob/master/LICENSE)
- [lxml-stubs](https://github.com/lxml/lxml-stubs): [Apache](https://github.com/lxml/lxml-stubs/blob/master/LICENSE)
- [pytest](https://github.com/pytest-dev/pytest): [MIT](https://github.com/pytest-dev/pytest/blob/main/LICENSE)
- [typeshed](https://github.com/python/typeshed): [Apache](https://github.com/python/typeshed/blob/main/LICENSE)
