# script for harvesting metadata based on oaipmh-scythe client
# run in terminal with the repository URL as argument: python harvester_scheduled.py {repo URL}

import os
import argparse
from datetime import datetime
from lxml import etree as ET
import json
from oaipmh_scythe import Scythe

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
    return oai_identifier.replace("/", "_").replace(":", "_")

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

    harvests_folder = f"harvests_{suffix}"
    os.makedirs(harvests_folder, exist_ok=True)

    today = datetime.today().strftime("%Y-%m-%d")

    try:
        with Scythe(repo_url) as client:
            if last_harvest:
                print(f"Incremental harvest since {last_harvest}")
                records = client.list_records(
                    from_=last_harvest,
                    metadata_prefix=metadata_prefix
                )
            else:
                print("First harvest, fetching all records.")
                records = client.list_records(metadata_prefix=metadata_prefix)

            harvested_any = False

            for record in records:
                identifier = record.header.identifier
                clean_id = clean_identifier(identifier)
                filename = f"{clean_id}.{metadata_prefix}.xml"
                filepath = os.path.join(harvests_folder, filename)

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(ET.tostring(record.xml, pretty_print=True, encoding="unicode"))
                    harvested_any = True

            if harvested_any:
                config["last_harvest_date"] = today
                save_repo_config(config_path, config)
                print(f"Harvest successful. Saved to: {harvests_folder}")
            else:
                print("No new records harvested.")

    except Exception as e:
        print(f"An error occurred during harvesting: {e}")

if __name__ == "__main__":
    main()
