# script for harvesting metadata based on oaipmh-scythe client
# run in terminal with the path to config file as argument: python harvester_scheduled.py {repos_config/repo.json}

import os
import sys
import argparse
from datetime import datetime
from lxml import etree as ET
import json
from oaipmh_scythe import Scythe
import requests
import traceback

NS = {"oai": "http://www.openarchives.org/OAI/2.0/"}
API_BASE_URL = ""

def load_repo_config(harvest_run_id: str):
    """
    Fetch repository configuration from API.

    :param harvest_run_id: unique ID for that harvest run
    :return: json file with config data
    """
    url = f"{API_BASE_URL}/config/{harvest_run_id}"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        config = response.json()
        return config
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch repository configuration from API: {e}")
        raise


# save new config data (i.e. update last harvest date) ??

# clean up OAI identifier for use in file names ??

def send_harvest_event(
    api_base_url,
    repo_code,
    harvest_url,
    record_identifier,
    datestamp,
    is_deleted,
    raw_metadata,
    additional_metadata
):
    """
    Create an API payload and send it.

    :return logical: True if the payload has been sent to API successfully 
    """
    url = f"{api_base_url}/harvest_event"
    payload = {
        "record_identifier": record_identifier,
        "datestamp": datestamp,
        "is_deleted": is_deleted,
        "raw_metadata": raw_metadata,
        "additional_metadata": additional_metadata,
        "harvest_url": harvest_url,
        "repo_code": repo_code
    }

    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Failed to send record {record_identifier} to API: {e}")
        return False


def fetch_dataverse_json(doi, base_url, exporter):
    """
    Fetch additional metadata: dataverse json

    :param doi: record identifier
    :param base_url: dataverse API endpoint
    :param exporter: metadata format

    :return: json with additional metadata
    """
    params = {"exporter": exporter, "persistentId": doi}
    try:
        response = requests.get(base_url, params=params, timeout=30)
        if response.status_code == 200:
            return json.dumps(response.json(), indent=2)
        else:
            print(f"Failed to fetch Dataverse JSON for {doi}: {response.status_code}")
    except Exception as e:
        print(f"Error fetching Dataverse JSON for {doi}: {e}")

# additional metadata: fetch and save additional schema
def fetch_additional_oai(record_id, base_url, metadata_prefix):
    """
    Fetch additional metadata: OAI-PMH

    :param record_id: OAI-PMH record identifier
    :param base_url: OAI-PMH endpoint
    :param metadata_prefix: metadata format

    :return: stringified xml with additional metadata
    """
    try:
        with Scythe(base_url) as client:
            record = client.get_record(identifier=record_id, metadata_prefix=metadata_prefix)
            return ET.tostring(record.xml, pretty_print=True, encoding="unicode")
    except Exception as e:
        print(f"Error fetching {metadata_prefix} metadata for {record_id}: {e}")


def main():
    parser = argparse.ArgumentParser(description="OAI-PMH Harvester (with the possibility of harvesting additional metadata)")
    parser.add_argument("harvest_run_id", help="Identifier of this harvest run")
    args = parser.parse_args()

    harvest_run_id = args.harvest_run_id
    config = load_repo_config(harvest_run_id)

    # this is an OAI-PMH harvester, exit if it's triggered by a repo with a different primary harvesting protocol
    if config.get("protocol") != "OAI-PMH":
        msg = (
            f"Repository '{config["name"]}' skipped: protocol '{config.get("protocol")}' is not supported by this harvester."
        )
        print(msg)
        sys.exit(0)

    harvest_url = config["harvest_url"]
    suffix = config["suffix"]
    metadata_prefix = config["harvest_params"].get("metadata_prefix", "oai_dc")
    last_harvest = config.get("last_harvest_date")
    set = config["harvest_params"].get("set")
    additional = config.get("additional_metadata")
    additional_protocol = additional.get("protocol") if additional else None


    try:
        with Scythe(harvest_url) as client:
            if last_harvest:
                print(f"Incremental harvest since {last_harvest}")
                records = client.list_records(
                    from_=last_harvest,
                    metadata_prefix=metadata_prefix,
                    set_=set
                )
            else:
                print("First harvest, fetching all records.")
                records = client.list_records(
                    metadata_prefix=metadata_prefix,
                    set_=set,
                    ignore_deleted=True
                )

            record_count = 0
            harvest_events = 0

            for record in records:
                record_count += 1
                identifier = record.header.identifier
                datestamp = record.header.datestamp
                is_deleted = getattr(record.header, "status", None) == "deleted"
                raw_metadata = ET.tostring(record.xml, pretty_print=True, encoding="unicode")

                additional_metadata = None

                if additional_protocol == "dataverse_api":
                    additional_metadata = fetch_dataverse_json(
                        doi=identifier,
                        base_url=additional["endpoint"],
                        exporter=additional["method"]
                    )

                elif additional_protocol == "OAI-PMH":
                    additional_metadata = fetch_additional_oai(
                        record_id=identifier,
                        base_url=additional["endpoint"],
                        metadata_prefix=additional["method"]
                    )

                if send_harvest_event(
                    api_base_url=API_BASE_URL,
                    repo_code=suffix,
                    harvest_url=harvest_url,
                    record_identifier=identifier,
                    datestamp=datestamp,
                    is_deleted=is_deleted,
                    raw_metadata=raw_metadata,
                    additional_metadata=additional_metadata,
                ):
                    harvest_events += 1

            if record_count > 0:
                today = datetime.today().strftime("%Y-%m-%d")
                print(f"Harvested {record_count} records. Successfully sent {harvest_events} of them to the warehouse.")
            else:
                print("No new records harvested.")

    except Exception as e:
        print(f"An error occurred during harvesting: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
