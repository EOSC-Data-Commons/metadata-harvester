import asyncio
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, AsyncIterator, cast
from xml.dom import minidom

import httpx
from httpx_retries import Retry, RetryTransport

from .db_api_functions import send_harvest_event

logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_ENDPOINT_URL = "https://sparql.knowledgehub.nfdi4earth.de/"

PAGE_SIZE = 5000

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

retry_strategy = Retry(
    total = 8,
    backoff_factor = 0.5,
    status_forcelist = RETRYABLE_STATUS_CODES
)

_ASYNC_NFDI4EARTH_CLIENT = httpx.AsyncClient(
    transport = RetryTransport(retry = retry_strategy),
    timeout = httpx.Timeout(120),
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": "EOSC Data Commons harvester",
    },
)

# Bound detail-request concurrency. A single ID page can contain up to
# PAGE_SIZE records, so an unrestricted gather() could otherwise create
# thousands of simultaneous requests.
MAX_CONCURRENT_REQUESTS = 10

# How long to wait for a single send_harvest_event call (run in a worker
# thread) before giving up on it. send_harvest_event is synchronous, so it
# must never be awaited directly in this event loop - it would block every
# other concurrent task while it waits on I/O.
SEND_EVENT_TIMEOUT = 60


async def shutdown_async_client() -> None:
    """Close the shared async HTTP client."""
    try:
        await _ASYNC_NFDI4EARTH_CLIENT.aclose()
        logger.info("Async NFDI4Earth client closed successfully.")
    except Exception as e:
        logger.error("Error closing async NFDI4Earth client: %s", e)



ID_QUERY = """
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX dct: <http://purl.org/dc/terms/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT DISTINCT ?dataset ?issued
WHERE {
    ?dataset rdf:type dcat:Dataset .

    OPTIONAL {
        ?dataset dct:issued ?issued .
    }

    @AFTER_FILTER@
    @SINCE_FILTER@
}
ORDER BY ?dataset
"""



DETAIL_QUERY = """
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX schema: <http://schema.org/>
PREFIX dct: <http://purl.org/dc/terms/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?dataset ?title ?authors ?description ?landingpage ?download_urls
       ?startDate ?endDate ?publishers ?issued ?licenses ?keywords
WHERE {
  BIND(<@DATASET_IRI@> AS ?dataset)

  ?dataset dct:title ?title .

  OPTIONAL {
    ?dataset dct:issued ?issued .
  }

  OPTIONAL {
    ?dataset schema:description ?description .
  }

  OPTIONAL {
    ?dataset dcat:landingPage ?landingpage .
  }

  OPTIONAL {
    SELECT (GROUP_CONCAT(DISTINCT ?authorName; separator=", ") AS ?authors)
    WHERE {
      BIND(<@DATASET_IRI@> AS ?dataset)
      ?dataset dct:creator ?bnode_creator .
      ?bnode_creator schema:name ?authorName .
    }
  }

  OPTIONAL {
    SELECT (GROUP_CONCAT(DISTINCT ?download_url; separator=", ") AS ?download_urls)
    WHERE {
      BIND(<@DATASET_IRI@> AS ?dataset)
      ?dataset dcat:distribution ?bnode_distrib .
      ?bnode_distrib dcat:downloadURL ?download_url .
    }
  }

  OPTIONAL {
    SELECT (SAMPLE(?sd) AS ?startDate)
           (SAMPLE(?ed) AS ?endDate)
    WHERE {
      BIND(<@DATASET_IRI@> AS ?dataset)
      ?dataset dct:temporal ?bnode_temporal .
      ?bnode_temporal dcat:startDate ?sd ;
                       dcat:endDate ?ed .
    }
  }

  OPTIONAL {
    SELECT (GROUP_CONCAT(DISTINCT ?pubLabel; separator=", ") AS ?publishers)
    WHERE {
      BIND(<@DATASET_IRI@> AS ?dataset)
      ?dataset dct:publisher ?pub .

      OPTIONAL {
        ?pub foaf:name ?foafName .
      }

      OPTIONAL {
        ?pub schema:name ?schemaName .
      }

      BIND(
        IF(
          isBlank(?pub),
          COALESCE(?foafName, ?schemaName),
          STR(?pub)
        )
        AS ?pubLabel
      )
    }
  }

  OPTIONAL {
    SELECT (GROUP_CONCAT(DISTINCT ?lic; separator=", ") AS ?licenses)
    WHERE {
      BIND(<@DATASET_IRI@> AS ?dataset)
      ?dataset schema:license ?lic .
    }
  }

  OPTIONAL {
    SELECT (GROUP_CONCAT(DISTINCT ?kw; separator=", ") AS ?keywords)
    WHERE {
      BIND(<@DATASET_IRI@> AS ?dataset)
      ?dataset dcat:keyword ?kw .
    }
  }
}
"""



def escape_sparql_string(value: str) -> str:
    """Escape a value for safe interpolation into a SPARQL string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')



async def execute_query(query: str, context: str) -> dict[str, Any]:
    """
    Execute a SPARQL query directly over HTTP using the shared async client.

    The RetryTransport handles transient connection/status failures. This
    function also validates the response and returns the parsed SPARQL JSON.
    """
    try:
        response = await _ASYNC_NFDI4EARTH_CLIENT.post(
            DEFAULT_ENDPOINT_URL,
            data={"query": query},
        )
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    except httpx.HTTPStatusError as e:
        logger.error(
            "HTTP %d while executing SPARQL query for %s",
            e.response.status_code,
            context,
        )
        raise
    except httpx.RequestError as e:
        logger.error("Network error while executing SPARQL query for %s: %s", context, e)
        raise



async def search_ids_page(after: str | None, since_filter: str = "") -> list[dict[str, Any]]:
    """
    Fetch one page of dataset IDs (and, where available, their `dct:issued`
    date) from the NFDI4Earth KnowledgeHub SPARQL endpoint, using keyset
    pagination.

    :param after: The dataset IRI of the last record seen on the previous
            page, or `None` to fetch the first page. Results are restricted
            to datasets that sort after this IRI.
    :param since_filter: A SPARQL `FILTER(...)` clause restricting results
            to datasets modified within a date range, or `""` for no filter.

    :return: The list of SPARQL result bindings (each with `dataset` and,
            optionally, `issued`) for that page, ordered by `?dataset`.
    """
    after_filter = (
        f'FILTER(STR(?dataset) > "{escape_sparql_string(after)}")' if after else ""
    )

    query = ID_QUERY.replace("@AFTER_FILTER@", after_filter).replace(
        "@SINCE_FILTER@", since_filter
    )
    query += f"\nLIMIT {PAGE_SIZE}"

    response = await execute_query(query, context=f"ids after={after}")

    return cast(
        list[dict[str, Any]],
        response["results"]["bindings"],
    )



async def fetch_dataset_detail(dataset_iri: str) -> dict[str, Any] | None:
    """
    Fetch full metadata for a single dataset from the NFDI4Earth
    KnowledgeHub SPARQL endpoint.

    :param dataset_iri: The IRI of the dataset to fetch metadata for.

    :return: A single SPARQL result binding with the dataset's metadata,
            or `None` if the dataset could not be found.
    """
    query = DETAIL_QUERY.replace("@DATASET_IRI@", dataset_iri)

    response = await execute_query(query, context=f"detail dataset={dataset_iri}")

    bindings = cast(
        list[dict[str, Any]],
        response["results"]["bindings"],
    )

    return bindings[0] if bindings else None



async def search_all_ids() -> AsyncIterator[list[dict[str, Any]]]:
    """
    Walk every page of dataset IDs from the NFDI4Earth KnowledgeHub SPARQL
    endpoint and yield each page's bindings as they're fetched.

    :return: An iterator over pages, where each page is a list of SPARQL
            result bindings (each with `dataset` and, optionally, `issued`).
    """
    after: str | None = None
    total_records = 0
    page_num = 1
    while True:
        page = await search_ids_page(after)

        if not page:
            logger.info("ID page %d empty, stopping", page_num)
            break

        total_records += len(page)
        logger.info(
            "ID page %d: %d records (%d total so far)",
            page_num, len(page), total_records,
        )
        yield page

        after = binding_value(page[-1], "dataset")

        if len(page) < PAGE_SIZE or not after:
            break

        page_num += 1
        await asyncio.sleep(0.5)



async def search_incremental_ids(from_date: str, until_date: str) -> AsyncIterator[list[dict[str, Any]]]:
    """
    Walk every page of dataset IDs from the NFDI4Earth KnowledgeHub SPARQL
    endpoint for datasets modified within a date range, and yield each
    page's bindings as they're fetched.

    :param from_date: The start of the update-date range (ISO 8601).
    :param until_date: The end of the update-date range (ISO 8601).
    :return: An iterator over pages, where each page is a list of SPARQL
            result bindings (each with `dataset` and, optionally, `issued`).
    """
    from_date = from_date[:10]
    until_date = until_date[:10]

    since_filter = (
        f'FILTER('
        f'?issued >= "{from_date}"^^<http://www.w3.org/2001/XMLSchema#date> && '
        f'?issued <= "{until_date}"^^<http://www.w3.org/2001/XMLSchema#date>'
        f')'
    )

    after: str | None = None
    total_records = 0
    page_num = 1
    while True:
        page = await search_ids_page(after, since_filter)

        if not page:
            logger.info("ID page %d empty, stopping", page_num)
            break

        total_records += len(page)
        logger.info(
            "ID page %d: %d records (%d total so far)",
            page_num, len(page), total_records,
        )
        yield page

        after = binding_value(page[-1], "dataset")

        if len(page) < PAGE_SIZE or not after:
            break

        page_num += 1
        await asyncio.sleep(0.5)



def binding_value(record: dict[str, Any], key: str) -> str | None:
    """Return the plain string value of a SPARQL binding, or None."""
    binding = record.get(key)
    return binding.get("value") if binding else None



def split_list(value: str | None, sep: str = ",") -> list[str]:
    """Split a GROUP_CONCAT-style string into a clean list of parts."""
    if not value:
        return []
    return [p.strip() for p in value.split(sep) if p.strip()]



def extract_doi(url: str | None) -> str | None:
    """Pull a bare DOI out of a URL, if present."""
    if not url:
        return None
    match = re.search(r"10\.\d{4,9}/\S+", url)
    return match.group(0) if match else None



DATASET_IRI_PREFIX = "https://cordra.knowledgehub.nfdi4earth.de/objects/"
async def nfdi4earth_data_to_datacite(record: dict[str, Any]) -> tuple[str, str | None]:
    """
    Convert an NFDI4Earth KnowledgeHub dataset record into a DataCite 4.6
    XML record wrapped in an OAI-PMH <record> element.

    :param record: A single SPARQL result binding, as returned by
            `fetch_dataset_detail`.

    :return: A tuple containing:
            - xml_pretty (str): Formatted DataCite XML record.
            - datestamp (str): Record update/creation date (YYYY-MM-DD).
    """
    title = binding_value(record, "title")
    authors_raw = binding_value(record, "authors")
    description = binding_value(record, "description")
    landingpage = binding_value(record, "landingpage")
    download_urls_raw = binding_value(record, "download_urls")
    start_date = binding_value(record, "startDate")
    end_date = binding_value(record, "endDate")
    publishers_raw = binding_value(record, "publishers")
    issued = binding_value(record, "issued")
    licenses_raw = binding_value(record, "licenses")
    keywords_raw = binding_value(record, "keywords")

    dataset_id = binding_value(record, "dataset")
    if not dataset_id:
        raise ValueError("SPARQL record is missing dataset IRI")

    doi = extract_doi(landingpage)
    record_identifier = dataset_id.removeprefix(DATASET_IRI_PREFIX)

    ET.register_namespace("", "http://www.openarchives.org/OAI/2.0/")
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")

    # OAI-PMH RECORD ROOT
    oai_record = ET.Element(
        "record", {
            "xmlns": "http://www.openarchives.org/OAI/2.0/",
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        })

    header = ET.SubElement(oai_record, "header")

    ET.SubElement(header, "identifier").text = record_identifier

    datestamp_text = issued[:10] if issued else None
    if datestamp_text:
        ET.SubElement(header, "datestamp").text = datestamp_text

    metadata = ET.SubElement(oai_record, "metadata")

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

    # IDENTIFIER (mandatory)
    if doi:
        ET.SubElement(resource, "identifier", identifierType="DOI").text = doi
    else:
        ET.SubElement(resource, "identifier", identifierType="URL").text = landingpage or ""

    # CREATORS
    author_names = split_list(authors_raw, sep=",")
    if author_names:
        creators = ET.SubElement(resource, "creators")
        for name in author_names:
            creator = ET.SubElement(creators, "creator")
            ET.SubElement(creator, "creatorName").text = name  # no nameType attribute at all

    # TITLES (mandatory)
    titles = ET.SubElement(resource, "titles")
    ET.SubElement(titles, "title").text = title

    # PUBLISHER (mandatory) - raw value from SPARQL, no external resolution
    publisher_list = split_list(publishers_raw, sep=",")
    publisher_el = ET.SubElement(resource, "publisher")
    publisher_el.text = publisher_list[0] if publisher_list else "unknown"

    # PUBLICATION YEAR (mandatory)
    if issued:
        year_match = re.search(r"(\d{4})", issued)
        if year_match:
            ET.SubElement(resource, "publicationYear").text = year_match.group(1)

    # RESOURCE TYPE (mandatory)
    ET.SubElement(resource, "resourceType", resourceTypeGeneral="Dataset").text = "Dataset"

    # DATES
    if issued or start_date or end_date:
        dates = ET.SubElement(resource, "dates")
        if issued:
            ET.SubElement(dates, "date", dateType="Issued").text = issued
        if start_date and end_date:
            ET.SubElement(dates, "date", dateType="Collected").text = f"{start_date}/{end_date}"
        elif start_date:
            ET.SubElement(dates, "date", dateType="Collected").text = start_date

    # RELATED IDENTIFIERS
    # DataCite has no dedicated "download URL" field; recording these as
    # relatedIdentifiers of type URL.
    download_urls = split_list(download_urls_raw, sep=",")
    if download_urls:
        related = ET.SubElement(resource, "relatedIdentifiers")
        for url in download_urls:
            ET.SubElement(related, "relatedIdentifier", relatedIdentifierType="URL", relationType="IsSourceOf").text = url

    # SUBJECTS
    keywords = split_list(keywords_raw, sep=",")[:10]
    if keywords:
        subjects_el = ET.SubElement(resource, "subjects")
        for word in keywords:
            ET.SubElement(subjects_el, "subject").text = word

    # RIGHTS
    licenses = split_list(licenses_raw, sep=",")
    if licenses:
        rights_list = ET.SubElement(resource, "rightsList")
        for lic in licenses:
            ET.SubElement(rights_list, "rights", rightsURI=lic)

    # DESCRIPTIONS
    if description:
        descriptions = ET.SubElement(resource, "descriptions")
        ET.SubElement(descriptions, "description", descriptionType="Abstract").text = description

    xml_str = ET.tostring(oai_record, encoding="unicode")
    xml_pretty = minidom.parseString(xml_str).toprettyxml(indent="  ")

    return xml_pretty, datestamp_text




async def process_dataset(
    dataset_iri: str,
    harvest_url: str | None,
    config: dict[str, Any],
    run_info: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> str:
    """
    Fetch and process one dataset using async HTTP.

    SPARQL HTTP requests are async. send_harvest_event remains a synchronous
    function (it matches the other harvester implementation), so it is run
    in a worker thread via asyncio.to_thread with a hard timeout - calling
    it directly would block this entire event loop (and every other
    concurrent task) for as long as it's stuck waiting on I/O.
    """
    try:
        async with semaphore:
            detail_record = await fetch_dataset_detail(dataset_iri)
    except Exception:
        logger.exception(
            "Failed to fetch detail for dataset %s; skipping it.",
            dataset_iri,
        )
        return "failed"

    if detail_record is None:
        logger.warning("No detail found for dataset %s, skipping", dataset_iri)
        return "skipped"

    try:
        xml_out, datestamp = await nfdi4earth_data_to_datacite(detail_record)
    except Exception:
        logger.exception("Failed to build metadata for dataset %s", dataset_iri)
        return "failed"

    record_identifier = (
        binding_value(detail_record, "dataset") or dataset_iri
    ).removeprefix(DATASET_IRI_PREFIX)

    download_url_list = split_list(binding_value(detail_record, "download_urls"))

    if datestamp is None:
        datestamp = datetime.now().isoformat()

    additional_metadata = (
        json.dumps({"download_urls": download_url_list}) if download_url_list else None
    )

    event_payload = {
        "record_identifier": record_identifier,
        "datestamp": datestamp,
        "raw_metadata": xml_out,
        "additional_metadata": additional_metadata,
        "harvest_url": harvest_url,
        "repo_code": config.get("code"),
        "harvest_run_id": run_info.get("id"),
        "is_deleted": False,
    }

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(send_harvest_event, event_payload),
            timeout=SEND_EVENT_TIMEOUT,
        )
        return "sent" if result else "failed"
    except asyncio.TimeoutError:
        logger.error(
            "send_harvest_event timed out after %ss for dataset %s",
            SEND_EVENT_TIMEOUT,
            dataset_iri,
        )
        return "failed"
    except Exception:
        logger.exception("Failed to send harvest event for dataset %s", dataset_iri)
        return "failed"



async def harvest_nfdi4earth(run_info: dict[str, Any]) -> bool:
    """
    Async NFDI4Earth harvester.

    ID pages are fetched sequentially because keyset pagination depends on
    the final IRI from the preceding page. Dataset detail records inside each
    page are fetched concurrently with asyncio.gather(), bounded by a
    semaphore to avoid overwhelming the public SPARQL endpoint.
    """
    record_count = 0
    harvest_events = 0
    failed_events = 0

    try:
        config = run_info.get("endpoint_config")
        if config is None:
            raise ValueError("config is missing")

        harvest_url = config.get("harvest_url")
        from_date = run_info.get("from_date")
        until_date = run_info.get("until_date")

        if until_date is None:
            raise ValueError("Missing until_date parameter")

        id_pages = (
            search_all_ids()
            if not from_date
            else search_incremental_ids(from_date, until_date)
        )

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

        async for id_page in id_pages:
            dataset_iris = [
                iri
                for id_record in id_page
                if (iri := binding_value(id_record, "dataset")) is not None
            ]

            skipped_in_page = len(id_page) - len(dataset_iris)
            if skipped_in_page:
                logger.warning(
                    "Skipping %d ID record(s) with no dataset IRI in this page",
                    skipped_in_page,
                )

            results = await asyncio.gather(
                *[
                    process_dataset(
                        iri,
                        harvest_url,
                        config,
                        run_info,
                        semaphore,
                    )
                    for iri in dataset_iris
                ],
                return_exceptions=True,
            )

            for iri, result in zip(dataset_iris, results):
                record_count += 1

                if isinstance(result, BaseException):
                    logger.error(
                        "Unexpected error processing dataset %s: %s",
                        iri,
                        result,
                    )
                    failed_events += 1
                elif result == "sent":
                    harvest_events += 1
                elif result == "failed":
                    failed_events += 1

        logger.info(
            "Harvest summary: processed %s records, successfully sent %s of them to the warehouse, "
            "failed to send %s records.",
            record_count,
            harvest_events,
            failed_events,
        )

        return failed_events == 0

    except Exception as e:
        logger.exception("Unexpected error in harvest_nfdi4earth: %s", e)
        logger.info(
            "Harvest summary: processed %s records, successfully sent %s of them to the warehouse, "
            "failed to send %s records.",
            record_count,
            harvest_events,
            failed_events,
        )
        return False



async def _harvest_and_shutdown(run_info: dict[str, Any]) -> bool:
    """Run the harvester and close the shared client on the same event loop."""
    try:
        return await harvest_nfdi4earth(run_info)
    finally:
        await shutdown_async_client()



def run_harvester_nfdi4earth(run_info: dict[str, Any]) -> bool:
    """Synchronous entry point used by main.py."""
    try:
        return asyncio.run(_harvest_and_shutdown(run_info))
    except Exception as e:
        logger.exception("NFDI4Earth harvester crashed: %s", e)
        return False