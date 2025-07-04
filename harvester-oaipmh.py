# script for harvesting metadata based on oaipmh-scythe client
# run in terminal with the repository URL as argument: python harvester_scheduled.py {repo URL}

import os
import argparse
from datetime import datetime
from lxml import etree as ET
from oaipmh_scythe import Scythe
from oaipmh_scythe.iterator import OAIResponseIterator

# list of repositories together with suffixes to be added to harvests folders
REPOSITORIES = {
    "https://archaeology.datastations.nl/oai": "DANS_arch",
    "https://ssh.datastations.nl/oai": "DANS_soc",
    "https://lifesciences.datastations.nl/oai": "DANS_life",
    "https://phys-techsciences.datastations.nl/oai": "DANS_phystech",
    "https://dataverse.nl/oai": "DANS_gen",
    "https://dabar.srce.hr/oai": "DABAR",
    "https://www.swissubase.ch/oai-pmh/v1/oai": "SWISS",
    "https://api.archives-ouvertes.fr/oai/hal": "HAL"
}
METADATA_PREFIX = "oai_dc"
NS = {"oai": "http://www.openarchives.org/OAI/2.0/"}

# suffix to be added to the folder with harvests and to the file with last harvest info 
def get_repo_suffix(repo_url):
    return REPOSITORIES.get(repo_url, "UNKNOWN")

# (create and) get path for the folder with harvests, and for the file with last harvest info
def get_paths(repo_url):
    suffix = get_repo_suffix(repo_url)
    harvests_folder = f"harvests_{suffix}"
    os.makedirs(harvests_folder, exist_ok=True)
    last_harvest_info = f"last_harvest_{suffix}.txt"
    return harvests_folder, last_harvest_info

# get last harvest date from the file, or None if this is an initial harvest
def load_last_harvest_date(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read().strip()
    return None

# (create last harvest file and) save the date of the last harvest 
def save_last_harvest_date(path, date):
    with open(path, "w") as f:
        f.write(date)

# take oai-pmh response and return only the records
def extract_records_from_response(xml_root):
    list_records = xml_root.find("oai:ListRecords", NS)
    return list_records.findall("oai:record", NS)

def main():
    parser = argparse.ArgumentParser(description="OAI-PMH Harvester")
    parser.add_argument("repository", help="URL of the repository's OAI-PMH endpoint (e.g. https://ssh.datastations.nl/oai)")
    args = parser.parse_args()

    repository = args.repository
    harvests_folder, last_harvest_info = get_paths(repository)

    last_harvest = load_last_harvest_date(last_harvest_info)
    today = datetime.today().strftime("%Y-%m-%d")

    try:
        with Scythe(repository, iterator=OAIResponseIterator) as client:
            if last_harvest:
                print(f"Incremental harvest since {last_harvest}")
                responses = client.list_records(
                    from_=last_harvest,
                    metadata_prefix=METADATA_PREFIX
                )
                is_initial = False
            else:
                print("First harvest, fetching all records.")
                responses = client.list_records(metadata_prefix=METADATA_PREFIX)
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
                save_last_harvest_date(last_harvest_info, today)
                print(f"Harvest successful. Saved to: {output_path}")
            else:
                print("No new records harvested. No file created.")
                os.remove(output_path)  

    except Exception as e:
        print(f"An error occurred during harvesting: {e}")

if __name__ == "__main__":
    main()
