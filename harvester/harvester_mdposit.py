import logging, os, httpx, xml.etree.ElementTree as ET, time, mimetypes, json
from datetime import datetime
from .db_api_functions import send_harvest_event
from xml.dom import minidom
from typing import Any, cast

logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_MDPOSIT_CLIENT = httpx.Client(
    timeout=httpx.Timeout(120),
)

def guess_format(filename: str) -> str:
    """
    Determine the MIME type of a file based on its filename extension.

    Uses Python's built-in mimetype detection and provides custom
    fallbacks for scientific file formats that are not recognized
    by the standard library.

    Args:
        filename: Name of the file whose MIME type should be determined.

    Returns:
        A MIME type string (e.g. "application/json",
        "chemical/x-pdb", "application/octet-stream").

    Notes:
        Unknown file types default to "application/octet-stream".
    """
    mime, _ = mimetypes.guess_type(filename)

    if mime is None:
        ext = filename.lower().split(".")[-1]
        custom_map = {
            "pdb": "chemical/x-pdb",
            "prmtop": "application/octet-stream",
            "xtc": "application/octet-stream",
            "bin": "application/octet-stream",
            "topology": "application/octet-stream",
        }
        return custom_map.get(ext, "application/octet-stream")
    return mime



def fetch_projects_summary(base_api_url: str, headers: dict[str, str]) -> dict[str, Any]:
    """
    Retrieve project summary statistics from the MDposit API.

    This endpoint is used to obtain metadata about the repository,
    including the total number of available projects.

    Args:
        base_api_url: Base URL of the MDposit API.
        headers: HTTP headers to include in the request.

    Returns:
        Dictionary containing summary information returned by
        the API.
    """
    url = f"{base_api_url}/projects/summary"
    response = _MDPOSIT_CLIENT.get(url, headers = headers, timeout = 30)
    response.raise_for_status()
    summary = response.json() 
    return cast(dict[str, Any], summary)



def fetch_all_projects_data(base_api_url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    """
    Retrieve all projects from the MDposit API.

    Projects are fetched in batches using paginated requests until
    all available records have been collected.

    Args:
        base_api_url: Base URL of the MDposit API.
        headers: HTTP headers to include in API requests.

    Returns:
        List of project dictionaries returned by the API.

    Notes:
        A one-second delay is applied between requests to avoid
        overwhelming the API.
    """
    project_summary = fetch_projects_summary(base_api_url, headers)
    total = project_summary["projectsCount"]
    print(f"Total projects: {total}")

    projects : list[dict[str, Any]] = []
    page = 1

    while len(projects) < total:
        response = _MDPOSIT_CLIENT.get(
            f"{base_api_url}/projects",
            headers = headers,
            params = {"limit": 100, "page": page},
            timeout = 30
        )
        response.raise_for_status()
        data = response.json()
        projects.extend(data["projects"])
        print(f"Fetched page {page}, total so far: {len(projects)}")
        page += 1
        time.sleep(1)

    return projects



def fetch_incremental_projects_data(base_api_url: str, from_date: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    """
    Retrieve projects updated after a specified date.

    Performs an incremental harvest by requesting only projects
    whose update date is greater than the supplied timestamp.

    The method first determines the total number of matching
        projects and then retrieves them using paginated requests.

    Args:
        base_api_url: Base URL of the MDposit API.
        from_date: ISO-formatted date used as the lower bound for project updates.
        headers: HTTP headers to include in API requests.

    Returns:
        List of project dictionaries updated after the specified date.

    Notes:
        A one-second delay is applied between requests to avoid
        overwhelming the API.
    """
    
    query = {"updateDate": {"$gt": from_date}}
    query_str = json.dumps(query)
    params : dict[str, str | int] = {"limit": 0, "page": 1, "query": query_str}
    
    # inital request just to see how many filtered projects there are
    response = _MDPOSIT_CLIENT.get(
        f"{base_api_url}/projects",
        headers = headers,
        params = params,
        timeout = 30
    )

    response.raise_for_status()
    data = response.json()
    number_of_filtered_objects = data["filteredCount"]

    # fetching all filtered projects 
    projects : list[dict[str, Any]] = []
    page = 1
    while len(projects) < number_of_filtered_objects:
        params_filtered : dict[str, str | int] = {"limit": 100, "page": page, "query": query_str}
        response = _MDPOSIT_CLIENT.get(
            f"{base_api_url}/projects",
            headers = headers,
            params = params_filtered,
            timeout = 30
        )
        response.raise_for_status()
        data = response.json()
        projects.extend(data["projects"])
        print(f"Fetched page {page}, total so far: {len(projects)}")
        page += 1
        time.sleep(1)
    return projects



def build_description(project: dict[str, Any]) -> str:
    """
    Generate a human-readable dataset description from project metadata.

    Constructs descriptive sentences using selected metadata fields
    such as simulation method, system components, domains, and
    available analyses.

    Args:
        project: Project dictionary returned by the MDposit API.

    Returns:
        A textual description suitable for inclusion in metadata
        records and discovery systems.
    """
    meta = project["metadata"]
    sentences = []

    # METHOD
    method = meta.get("METHOD")
    if method:
        sentences.append(f"The dataset was generated using {method}.")

    # SYSKEYS
    syskeys = meta.get("SYSKEYS") or []
    if syskeys:
        sentences.append(f"The system is composed of the following components: {', '.join(syskeys)}.")

    # DOMAINS
    domains = meta.get("DOMAINS") or []
    if domains:
        clean_domains = ", ".join(domains)
        sentences.append(
            f"The system relates to the following domains: {clean_domains}."
        )

    # ANALYSES
    analyses = project.get("analyses") or []
    if analyses:
        pretty = ", ".join(analyses)
        sentences.append(
            f"The dataset includes the following analyses: {pretty}."
        )

    return " ".join(sentences)



def mdposit_data_to_datacite(project: dict[str, Any]) -> tuple[str, str, str]:
    """
    Convert an MDposit project record into a DataCite XML record.

    Args:
        project: Project dictionary obtained from the MDposit API.

    Returns:
        A tuple containing:
            - xml_pretty (str): Formatted DataCite XML record.
            - identifier (str): Dataset URL identifier.
            - datestamp (str): Record update or creation date
              in YYYY-MM-DD format.
    """

    meta = project["metadata"]

    # NAMESPACES
    ET.register_namespace("", "http://www.openarchives.org/OAI/2.0/")
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    ET.register_namespace("dc", "http://datacite.org/schema/kernel-4")

    # DATES
    creation_date = project.get("creationDate", "")
    update_date = project.get("updateDate", "")

    # OAI-PMH RECORD ROOT
    record = ET.Element(
        "record",
        {
            "xmlns": "http://www.openarchives.org/OAI/2.0/",
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        },
    )

    # HEADER
    header = ET.SubElement(record, "header")

    header_identifier = ET.SubElement(header, "identifier")
    header_identifier.text = project['accession']

    datestamp = ET.SubElement(header, "datestamp")
    datestamp.text = update_date[:10] if update_date else creation_date[:10]

    # METADATA BLOCK
    metadata = ET.SubElement(record, "metadata")

    # DATACITE RESOURCE
    resource = ET.SubElement(
        metadata,
        "resource",
        {
            "xmlns": "http://datacite.org/schema/kernel-4",
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:schemaLocation": (
                "http://datacite.org/schema/kernel-4 "
                "https://schema.datacite.org/meta/kernel-4.6/metadata.xsd"
            ),
        },
    )

    # IDENTIFIER
    identifier = ET.SubElement(resource, "identifier", identifierType = "URL")
    identifier.text = f"https://mdposit.mddbr.eu/#/browse/{project['accession']}"

    # CREATORS
    creators = ET.SubElement(resource, "creators")
    for author in meta.get("AUTHORS") or []:
        creator = ET.SubElement(creators, "creator")
        creator_name = ET.SubElement(creator, "creatorName", nameType = "Personal")
        creator_name.text = author

    # TITLES
    titles = ET.SubElement(resource, "titles")
    ET.SubElement(titles, "title").text = meta.get("NAME", "")

    # RESOURCE TYPE
    ET.SubElement(resource, "resourceType", resourceTypeGeneral="Dataset").text = "Molecular Dynamics Simulations"

    # CONTRIBUTORS
    if meta.get("CONTACT"):
        contributors = ET.SubElement(resource, "contributors")
        contributor = ET.SubElement(contributors, "contributor", contributorType = "ContactPerson")
        ET.SubElement(contributor, "contributorName").text = meta["CONTACT"]

    # DATES
    dates = ET.SubElement(resource, "dates")
    if creation_date:
        ET.SubElement(dates, "date", dateType="Created").text = creation_date[:10]

    if update_date:
        ET.SubElement(dates, "date", dateType="Updated").text = update_date[:10]

    # RELATED IDENTIFIERS
    related = ET.SubElement(resource, "relatedIdentifiers")
    # PDB
    for pdbid in meta.get("PDBIDS") or []:
        ET.SubElement(related, "relatedIdentifier", relatedIdentifierType = "URL", relationType = "IsDerivedFrom").text = f"https://www.rcsb.org/structure/{pdbid}"

    # UniProt
    for ref in meta.get("REFERENCES") or []:
        ET.SubElement(related, "relatedIdentifier", relatedIdentifierType = "URL", relationType = "References").text = f"https://www.uniprot.org/uniprot/{ref}"

    # DOI
    citation = meta.get("CITATION") or ""
    if citation:
        if "https://doi.org/" in citation:
            doi = citation.split("https://doi.org/")[-1].strip()
            ET.SubElement(related, "relatedIdentifier", relatedIdentifierType="DOI", relationType="IsDocumentedBy").text = doi
        elif "DOI:" in citation:
            doi = citation.replace("DOI:", "").strip()
            ET.SubElement(related, "relatedIdentifier", relatedIdentifierType="DOI", relationType="IsDocumentedBy").text = doi

    # FORMATS
    files = project.get("files") or []
    formats_set = {guess_format(f) for f in files if "." in f}
    formats_el = ET.SubElement(resource, "formats")
    for fmt in sorted(formats_set):
        ET.SubElement(formats_el, "format").text = fmt

    # RIGHTS
    rights_list = ET.SubElement(resource, "rightsList")
    if meta.get("LINKCENSE") and meta.get("LICENSE"):
        ET.SubElement(rights_list, "rights", rightsURI = meta.get("LINKCENSE"), rightsIdentifier = "CC-BY-4.0").text = meta.get("LICENSE")

    # DESCRIPTIONS
    descriptions = ET.SubElement(resource, "descriptions")
    if meta.get("DESCRIPTION"):
        description_text = meta["DESCRIPTION"].strip()
        description_text = build_description(project) + " " + description_text
    else:
        description_text = build_description(project)
    ET.SubElement(descriptions, "description", descriptionType="Abstract").text = description_text

    xml_str = ET.tostring(record, encoding="unicode")
    xml_pretty = minidom.parseString(xml_str).toprettyxml(indent="  ")
    return xml_pretty, identifier.text, (update_date or creation_date)[:10]



def run_harvester_mdposit(run_info: dict[str, Any]) -> bool:
    """
    Depending on the supplied configuration, performs either a full
    harvest or an incremental harvest, converts each project into
    DataCite XML, and publishes harvest events to the data warehouse.

    Args:
        run_info: Harvest execution configuration containing:
            - endpoint_config.harvest_url
            - from_date (optional)
            - id (harvest run identifier)

    Returns:
        True if all harvest events were successfully sent;
        False if any failures occurred or an unexpected error
        was encountered.

    Raises:
        No exceptions are propagated. Unexpected errors are logged
        and result in a False return value.
    """

    record_count = 0
    harvest_events = 0
    failed_events = 0
    try:

        config = run_info.get("endpoint_config")
        if config is None:
            raise ValueError("config is missing")
        harvest_url = config.get("harvest_url")
        from_date: str | None = run_info.get("from_date")
        headers = {"Accept": "application/json"}

        if not from_date:
            mdposit_data_projects = fetch_all_projects_data(harvest_url, headers)
        else:
            mdposit_data_projects = fetch_incremental_projects_data(harvest_url, from_date, headers)
        for project in mdposit_data_projects:
            mdposit_xml, identifier, datestamp = mdposit_data_to_datacite(project)
            additional_file_metadata = ", ".join(project.get("files", []))

            event_payload = {
                "record_identifier": identifier,
                "datestamp": datestamp,
                "raw_metadata": mdposit_xml,
                "additional_metadata": additional_file_metadata,
                "harvest_url": harvest_url,
                "repo_code": config.get("code"),
                "harvest_run_id": run_info.get("id"),
                "is_deleted": False
            }

            if send_harvest_event(event_payload):
                harvest_events += 1
            else:
                failed_events += 1

        logger.info(
            "Harvest summary: processed %s records, successfully sent %s of them to the warehouse, failed to send %s records.",
            record_count,
            harvest_events,
            failed_events
        )

        if failed_events == 0:
            return True
        else:
            return False
            
    except Exception as e:
        logger.exception("Unexpected error in run_harvester_oaipmh: %s", e)
        logger.info(
            "Harvest summary: processed %s records, successfully sent %s of them to the warehouse, failed to send %s records.",
            record_count,
            harvest_events,
            failed_events
        )
        return False