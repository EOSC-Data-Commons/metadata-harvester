# script for harvesting metadata based on oaipmh-scythe client
# run in terminal with the repository URL as argument: python harvester_scheduled.py {repo URL}

import os
import argparse
from datetime import datetime
from lxml import etree as ET
import json
from oaipmh_scythe import Scythe
from oaipmh_scythe.iterator import OAIResponseIterator

NS = {"oai": "http://www.openarchives.org/OAI/2.0/"}

# load json with config data for the repository
def load_repo_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# save new config data (i.e. update last harvest date)
def save_repo_config(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# take oai-pmh response and return only the records
def extract_records_from_response(xml_root):
    list_records = xml_root.find("oai:ListRecords", NS)
    return list_records.findall("oai:record", NS)

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
        with Scythe(repo_url, iterator=OAIResponseIterator) as client:
            if last_harvest:
                print(f"Incremental harvest since {last_harvest}")
                responses = client.list_records(
                    from_=last_harvest,
                    metadata_prefix=metadata_prefix
                )
                is_initial = False
            else:
                print("First harvest, fetching all records.")
                responses = client.list_records(metadata_prefix=metadata_prefix)
                is_initial = True

            output_path = os.path.join(
                harvests_folder,
                f"{'initial_harvest' if is_initial else 'harvest'}_{today}.xml"
            )

            harvested_any = False

            with open(output_path, "w", encoding="utf-8") as f:
                f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
                f.write('<records>\n')

                for oai_response in responses:
                    records = extract_records_from_response(oai_response.xml)
                    for record in records:
                        f.write(ET.tostring(record, pretty_print=True, encoding="unicode"))
                        f.write("\n")
                        harvested_any = True

                f.write('</records>')

            if harvested_any:
                config["last_harvest"] = today
                save_repo_config(config_path, config)
                print(f"Harvest successful. Saved to: {output_path}")
            else:
                print("No new records harvested. No file created.")
                os.remove(output_path)  

    except Exception as e:
        print(f"An error occurred during harvesting: {e}")

if __name__ == "__main__":
    main()
