# script for harvesting metadata based on oaipmh-scythe client
# run in terminal with the repository URL as argument: python harvester_scheduled.py {repo URL}

import os
import argparse
from datetime import datetime
from lxml import etree as ET
import json
from oaipmh_scythe import Scythe
import requests

NS = {"oai": "http://www.openarchives.org/OAI/2.0/"}

# load json with config data for the repository
def load_repo_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

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


def main():
    parser = argparse.ArgumentParser(description="OAI-PMH Harvester")
    parser.add_argument("config_file", help="Path to repository config JSON file")
    args = parser.parse_args()

    config_path = args.config_file
    config = load_repo_config(config_path)

    repo_url = config["repository_url"]
    suffix = config["repository_suffix"]
    metadata_prefix = config.get("metadata_prefix", "oai_dc")
    last_harvest = config.get("last_harvest_date")
    set = config.get("set")
    additional = config.get("additional_metadata")
    additional_protocol = additional.get("protocol") if additional else None

    harvests_folder = f"harvests_{suffix}"
    os.makedirs(harvests_folder, exist_ok=True)

    try:
        with Scythe(repo_url) as client:
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

            for record in records:
                if save_record(record, metadata_prefix, harvests_folder):
                    record_count += 1  

                if additional_protocol == "dataverse_api":
                        doi = record.header.identifier  # OAI identifier == persistentId
                        save_dataverse_json(
                            doi,
                            additional["base_url"],
                            additional["exporter"],
                            harvests_folder
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

if __name__ == "__main__":
    main()
