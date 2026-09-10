# Repository for metadata crawlers

This repository contains a Python package for metadata crawling and ingestion.
Currently, it supports harvesting metadata via the OAI-PMH protocol, using the oaipmh-scythe Python client library, and a specialized harvester for FinBIF repository.
The package is containerized with Docker and can be run locally or deployed as part of a larger metadata ingestion system.
This is work in progress and will be extended with additional harvesting methods in the future.

## Overview

The crawler is designed as a modular Python package.
It performs the following tasks:
1. Starts a new harvest run in the database via an API call to /harvest_run.
2. Retrieves configuration info for the given repository endpoint.
3. Determines the harvesting protocol (currently only OAI-PMH and FinBIF API are supported) and executes the appropriate harvesting module.
4. Sends harvested metadata to the database via an API call to /harvest_event.
5. Closes the harvest run with a success or failure status.
6. Logs all stages of the workflow to both console and rotating log files.

## Architecture
The package consists of the following components:
- main.py - entry point that namages harvest runs, fetches config info and runs the appropriate harvesting module
- db_api_functions.py - contains functions used to communicate with the database API
- harvester_oaipmh.py - harvesting module for repositories that expose metadata via OAI-PMH
- harvester_finbif.py - harvesting module for FinBIF repository
- dc_to_datacite.xsl - XSLT stylesheet that transforms metadata format from Dublin Core to DataCite format
- ddi_to_datacite.xsl - XSLT stylesheet that transforms metadata format from DDI to DataCite format
- logging.py - logging config function
- settings.py - handles settings for different environments

## Requirements
- [Python](https://www.python.org/downloads/) >= 3.12
- Install `uv`, see [docs](https://docs.astral.sh/uv/getting-started/installation/)
- Dependencies are listed in `pyproject.toml`,
  install with `uv sync --locked --all-extras --group dev` for local development

## Environment configuration
The harvester uses Pydantic settings for all configuration.
All configuration values come from:

1. Docker environment variables (if running inside Docker)
2. A local .env file (if running on your machine)
3. Internal defaults in settings.py

You must create a .env file before running the harvester locally.

To do this:
copy the example file ```cp .env.example .env```
and fill in the required values:
```
ENVIRONMENT=local
WAREHOUSE_API_URL=http://localhost:8000
```
- ```ENVIRONMENT``` chooses a configuration profile (local, dev, staging, production)
- ```WAREHOUSE_API_URL``` must point to the Warehouse API instance the harvester will send results to.
For Docker execution, the ```WAREHOUSE_API_URL``` can be set in ```docker-compose.yml```.

## Install as a Python package

The harvester can be installed straight from GitHub and used as a library:

```sh
pip install git+https://github.com/EOSC-Data-Commons/metadata-crawlers.git
# or with uv
uv add git+https://github.com/EOSC-Data-Commons/metadata-crawlers.git
```

Then invoke the harvesting process from Python with `run_harvest()`, passing a `HarvesterSettings` object instead of relying on a `.env` file:

```python
from harvester import HarvesterSettings, run_harvest

success = run_harvest(
    "https://example.org/oai",  # repository harvesting endpoint
    settings=HarvesterSettings(
        WAREHOUSE_API_URL="http://localhost:8000",
        WAREHOUSE_API_TIMEOUT=30,  # optional, defaults to 30
        LOG_DIR="./logs",          # optional, defaults to ./logs
        LOG_LEVEL="INFO",          # optional, defaults to INFO
    ),
    setup_logs=True,  # optional, set False to keep your own logging config
)
```

Omitting `settings` falls back to the environment profile: the `ENVIRONMENT` variable, the `.env` file, then the defaults in `settings.py`. Unrelated keys in your own `.env` are ignored, so the package is safe to import into an existing project.

Installing the package also provides a `metadata-harvester {repository URL}` command.

## Running Locally

To run the harvester directly on your machine:
1. Ensure ```.env``` exists in the project root and the required values have been filled
2. Run the following:
```
uv run python -m harvester {repository URL}
```
Replace ```{repository URL}``` with the actual base URL of the repository you want to harvest.

## Docker Usage
The repository includes a ```Dockerfile``` and a ```docker-compose.yml```.
These can be used to build and run the harvester in an isolated environment.

Build the image:
```
docker compose build
```
Run the harvester once:
```
docker compose run --rm harvester {repository URL}
```

By default, ```.env``` is loaded in ```docker-compose.yml```, but you can replace that with appropriate values for ```ENVIRONMENT``` and ```WAREHOUSE_API_URL```.

## Logs and Output
The harvester does not write harvested metadata to disk.
Instead, all records and harvest run status updates are sent directly to the database API.

Logs include:
- Informational messages about harvesting progress
- Warnings about potentially problematic responses
- Errors indicating failed requests, invalid responses, or failed harvest runs

Logs are saved to:
``` logs/harvester.log ```

When running in Docker, this log directory can be mounted as a volume to persist logs outside the container.

## Future Extensions
Future versions of this package will support additional crawling protocols.

## License
This project is licensed under the Apache License 2.0.
See the LICENSE file for details.

This project uses the [oaipmh-scythe](https://github.com/afuetterer/oaipmh-scythe) Python client,
which is distributed under the BSD license.
The BSD license is a permissive open source license that allows use, modification, and distribution.
For full license details, see the [oaipmh-scythe license](https://github.com/afuetterer/oaipmh-scythe/blob/master/LICENSE).
