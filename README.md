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
Run from the command line:
```sh
python harvester-oaipmh.py {repository URL}
```
Replace {repository URL} with the actual OAI-PMH endpoint of the repository you want to harvest from.

## Output
Harvested XML files are saved as initial_harvest_{date}.xml or harvest_{date}.xml in folders harvests_{repository}.

Last harvest date is saved in last_harvest_{repository}.txt for the purpose of incremental harvesting.

## License
This project uses the [oaipmh-scythe](https://github.com/afuetterer/oaipmh-scythe) Python client,  
which is distributed under the BSD license.

The BSD license is a permissive open source license that allows use, modification, and distribution.  
For full license details, see the [oaipmh-scythe license](https://github.com/afuetterer/oaipmh-scythe/blob/master/LICENSE).