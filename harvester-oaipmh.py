# script for harvesting metadata based on oaipmh-scythe client
# run in terminal with the path to config file as argument: python harvester_scheduled.py {repos_config/repo.json}

import os
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


# save new config data (i.e. update last harvest date)
def save_repo_config(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# clean up OAI identifier for use in file names
def clean_identifier(oai_identifier):
    # replace problematic characters
    return oai_identifier.replace("/", "_").replace("\\", "_").replace(":", "_")

# save record if it's the latest version
def save_record(record, metadata_prefix, harvests_folder):
    identifier = record.header.identifier
    clean_id = clean_identifier(identifier)
    filename = f"{clean_id}.{metadata_prefix}.xml"
    filepath = os.path.join(harvests_folder, filename)

    # check if file already exists
    if os.path.exists(filepath):
        try:
            # parse existing file to get its datestamp
            existing_tree = ET.parse(filepath)
            existing_root = existing_tree.getroot()
            existing_datestamp = existing_root.findtext(".//oai:datestamp", namespaces=NS)

            if existing_datestamp and existing_datestamp >= record.header.datestamp:
                # existing file is newer - skip this record
                return False
        except Exception as e:
            print(f"Warning: could not compare with existing record '{filename}', overwriting file: {e}")

    # if the record is new(er), save it
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(ET.tostring(record.xml, pretty_print=True, encoding="unicode"))

    return True

# additional metadata: fetch and save dataverse json
def save_dataverse_json(doi, base_url, exporter, harvests_folder):
    params = {"exporter": exporter, "persistentId": doi}
    try:
        response = requests.get(base_url, params=params, timeout=30)
        if response.status_code == 200:
            clean_id = clean_identifier(doi)
            filename = f"{clean_id}.{exporter}.json"
            filepath = os.path.join(harvests_folder, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(response.json(), f, indent=2)
        else:
            print(f"Failed to fetch Dataverse JSON for {doi}: {response.status_code}")
    except Exception as e:
        print(f"Error fetching Dataverse JSON for {doi}: {e}")

# additional metadata: fetch and save additional schema
def save_additional_oai(record_id, repo_url, metadata_prefix, harvests_folder):
    try:
        with Scythe(repo_url) as client:
            record = client.get_record(identifier=record_id, metadata_prefix=metadata_prefix)
            clean_id = clean_identifier(record_id)
            filename = f"{clean_id}.{metadata_prefix}.xml"
            filepath = os.path.join(harvests_folder, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(ET.tostring(record.xml, pretty_print=True, encoding="unicode"))
    except Exception as e:
        print(f"Error fetching {metadata_prefix} metadata for {record_id}: {e}")

def main():
    parser = argparse.ArgumentParser(description="OAI-PMH Harvester (with the possibility of harvesting additional metadata)")
    parser.add_argument("harvest_run_id", help="Identifier of this harvest run")
    args = parser.parse_args()

    harvest_run_id = args.harvest_run_id
    config = load_repo_config(harvest_run_id)

    harvest_url = config["harvest_url"]
    suffix = config["suffix"]
    metadata_prefix = config["harvest_params"].get("metadata_prefix", "oai_dc")
    last_harvest = config.get("last_harvest_date")
    set = config["harvest_params"].get("set")
    additional = config.get("additional_metadata")
    additional_protocol = additional.get("protocol") if additional else None

    harvests_folder = f"harvests_{suffix}"
    additional_folder = f"harvests_{suffix}_additional"
    os.makedirs(harvests_folder, exist_ok=True)
    os.makedirs(additional_folder, exist_ok=True)

    try:
        with Scythe(harvest_url) as client:
            if last_harvest:
                print(f"Incremental harvest since {last_harvest}")
                records = client.list_records(
                    from_=last_harvest,
                    until="2025-08-21",
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

            for record in records:
                if save_record(record, metadata_prefix, harvests_folder):
                    record_count += 1  

                if additional_protocol == "dataverse_api":
                        doi = record.header.identifier  # OAI identifier == persistentId
                        save_dataverse_json(
                            doi,
                            additional["base_url"],
                            additional["exporter"],
                            additional_folder
                        )

                if additional_protocol == "OAI-PMH":
                        identifier = record.header.identifier
                        save_additional_oai(
                            record_id=identifier,
                            repo_url=additional["base_url"],
                            metadata_prefix=additional["schema"],
                            harvests_folder=additional_folder
                        )

            if record_count > 0:
                today = datetime.today().strftime("%Y-%m-%d")
                config["last_harvest_date"] = today
                save_repo_config(config_path, config)
                print(f"Harvested {record_count} records. Saved to: {harvests_folder}")
            else:
                print("No new records harvested.")

    except Exception as e:
        print(f"An error occurred during harvesting: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
