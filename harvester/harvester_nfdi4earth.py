import logging, os, time, json, re, threading, hashlib, xml.etree.ElementTree as ET, requests
from .db_api_functions import send_harvest_event
from xml.dom import minidom
from typing import Any, Callable, Iterator, cast
from urllib.error import HTTPError
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from SPARQLWrapper import SPARQLWrapper, JSON as SPARQL_JSON
from SPARQLWrapper.SPARQLExceptions import (
    EndPointNotFound,
    EndPointInternalError,
    URITooLong,
    QueryBadFormed,
    Unauthorized,
)

logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_ENDPOINT_URL = "https://sparql.knowledgehub.nfdi4earth.de/"

PAGE_SIZE = 5000
MAX_RETRIES = 3

# --------------------------------------------------------------------------
# SPARQLWrapper client is NOT thread-safe: setQuery()/queryAndConvert()
# mutate shared state on the instance. Rather than a single module-level
# client, give each worker thread its own instance via thread-local
# storage, so concurrent detail fetches (see run_harvester_nfdi4earth)
# can't race and cross-contaminate each other's queries/responses.
# --------------------------------------------------------------------------
_thread_local = threading.local()

def get_client() -> SPARQLWrapper:
    client = getattr(_thread_local, "client", None)
    if client is None:
        client = SPARQLWrapper(DEFAULT_ENDPOINT_URL)
        client.setReturnFormat(SPARQL_JSON)
        _thread_local.client = client
    return client

# How many datasets to fetch detail records for concurrently. This is
# I/O-bound (waiting on the SPARQL endpoint), so threads help a lot here
# despite the GIL. Keep this conservative-ish so we don't hammer a shared
# public endpoint; tune up/down based on observed throttling (429s).
MAX_WORKERS = 10



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
    SELECT (SAMPLE(?d) AS ?description)
    WHERE {
      BIND(<@DATASET_IRI@> AS ?dataset)
      ?dataset schema:description ?d .
    }
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



RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
RETRYABLE_SPARQL_EXCEPTIONS = (EndPointNotFound, EndPointInternalError, URITooLong)
NON_RETRYABLE_SPARQL_EXCEPTIONS = (QueryBadFormed, Unauthorized)



def execute_query(query: str, context: str) -> dict[str, Any]:
    """
    Run a SPARQL query against a thread-local client, retrying on
    transient errors - either an `HTTPError` with a retryable status code
    (429, 500, 502, 503, 504), or one of SPARQLWrapper's own transient
    exception types (`EndPointNotFound`, `EndPointInternalError`,
    `URITooLong`) - up to `MAX_RETRIES` times.

    :param query: The full SPARQL query text to execute.
    :param context: A short description used only for log messages
            (e.g. "ids after=<iri>" or "detail dataset=<iri>").

    :return: The parsed JSON response from the SPARQL endpoint.
    """
    client = get_client()
    client.setQuery(query)

    response: dict[str, Any] | None = None

    for attempt in range(MAX_RETRIES):
        is_last_attempt = attempt >= MAX_RETRIES - 1

        try:
            response = cast(
                dict[str, Any],
                client.queryAndConvert(),
            )
            break

        except HTTPError as e:
            if e.code not in RETRYABLE_STATUS_CODES:
                raise

            if is_last_attempt:
                logger.error(
                    "HTTP %d for %s after %d attempts; giving up.",
                    e.code,
                    context,
                    MAX_RETRIES,
                )
                raise

            wait = 5 * (attempt + 1)
            logger.warning(
                "HTTP %d for %s, retrying in %ds (attempt %d/%d)...",
                e.code,
                context,
                wait,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(wait)

        except NON_RETRYABLE_SPARQL_EXCEPTIONS:
            raise

        except RETRYABLE_SPARQL_EXCEPTIONS as e:
            if is_last_attempt:
                logger.error(
                    "%s for %s after %d attempts; giving up.",
                    type(e).__name__,
                    context,
                    MAX_RETRIES,
                )
                raise

            wait = 5 * (attempt + 1)
            logger.warning(
                "%s for %s, retrying in %ds (attempt %d/%d)...",
                type(e).__name__,
                context,
                wait,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(wait)

    if response is None:
        raise RuntimeError(
            f"_execute_query got no response after "
            f"{MAX_RETRIES} attempts ({context})"
        )

    return response



def search_ids_page(after: str | None, since_filter: str = "") -> list[dict[str, Any]]:
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

    response = execute_query(query, context=f"ids after={after}")

    return cast(
        list[dict[str, Any]],
        response["results"]["bindings"],
    )



def fetch_dataset_detail(dataset_iri: str) -> dict[str, Any] | None:
    """
    Fetch full metadata for a single dataset from the NFDI4Earth
    KnowledgeHub SPARQL endpoint.

    :param dataset_iri: The IRI of the dataset to fetch metadata for.

    :return: A single SPARQL result binding with the dataset's metadata,
            or `None` if the dataset could not be found.
    """
    query = DETAIL_QUERY.replace("@DATASET_IRI@", dataset_iri)

    response = execute_query(query, context=f"detail dataset={dataset_iri}")

    bindings = cast(
        list[dict[str, Any]],
        response["results"]["bindings"],
    )

    return bindings[0] if bindings else None



def search_all_ids() -> Iterator[list[dict[str, Any]]]:
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
        page = search_ids_page(after)

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
        time.sleep(0.5)



def search_incremental_ids(from_date: str, until_date: str) -> Iterator[list[dict[str, Any]]]:
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
        page = search_ids_page(after, since_filter)

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
        time.sleep(0.5)



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



ROR_CACHE: dict[str, dict[str, Any] | None] = {}
_ROR_CACHE_LOCK = threading.Lock()

def resolve_ror_publisher(publisher_url: str) -> dict[str, Any] | None:
    """Resolve a publisher value containing a ROR ID to name + ROR identifier."""

    match = re.search(r"(?:ror-|ror\.org/)([a-z0-9]+)", publisher_url, re.IGNORECASE)
    if not match:
        return None

    ror_id = match.group(1).lower()

    # Cache reads/writes come from multiple worker threads once detail
    # fetching is parallelized; guard with a lock. Worst case without it
    # is a handful of duplicate ROR API calls
    with _ROR_CACHE_LOCK:
        if ror_id in ROR_CACHE:
            return ROR_CACHE[ror_id]

    try:
        resp = requests.get(
            f"https://api.ror.org/organizations/{ror_id}",
            timeout=10,
        )
        resp.raise_for_status()

        data: dict[str, Any] = resp.json()
        names: list[dict[str, Any]] = data.get("names", [])

        name = next(
            (
                n["value"]
                for n in names
                if "ror_display" in n.get("types", [])
                and n.get("value")
            ),
            None,
        )

        if not name:
            name = next(
                (
                    n["value"]
                    for n in names
                    if n.get("lang") == "en"
                    and "acronym" not in n.get("types", [])
                    and n.get("value")
                ),
                None,
            )

        if not name:
            name = next(
                (
                    n["value"]
                    for n in names
                    if "acronym" not in n.get("types", [])
                    and n.get("value")
                ),
                None,
            )

        if not name:
            logger.warning("No usable name found for ROR %s", ror_id)
            return None

        result = {
            "name": str(name),
            "ror_id": ror_id,
        }

        with _ROR_CACHE_LOCK:
            ROR_CACHE[ror_id] = result
        return result

    except requests.RequestException as e:
        logger.warning("ROR lookup failed for %s: %s", ror_id, e)
        return None



def nfdi4earth_data_to_datacite(record: dict[str, Any]) -> tuple[str, str]:
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
    record_identifier = dataset_id or ""

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

    if issued:
        datestamp_text = issued[:10]
    else:
        datestamp_text = datetime.now().date().isoformat()

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

    # PUBLISHER (mandatory)
    publisher_list = split_list(publishers_raw, sep=",")
    publisher_el = ET.SubElement(resource, "publisher")
    if publisher_list:
        resolved = resolve_ror_publisher(publisher_list[0])
        if resolved:
            publisher_el.text = resolved["name"]
            publisher_el.set("publisherIdentifier", f"https://ror.org/{resolved['ror_id']}")
            publisher_el.set("publisherIdentifierScheme", "ROR")
            publisher_el.set("schemeURI", "https://ror.org")
        else:
            publisher_el.text = publisher_list[0]  # fall back to raw URL if resolution fails
    else:
        publisher_el.text = "unknown"

    # PUBLICATION YEAR (mandatory)
    if issued:
        pub_year_source = issued
    else:
        pub_year_source = datestamp_text

    year_match = re.search(r"(\d{4})", pub_year_source)
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
    keywords = split_list(keywords_raw, sep=",")
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



def process_dataset(
    dataset_iri: str,
    harvest_url: str | None,
    config: dict[str, Any],
    run_info: dict[str, Any],
) -> str:
    """
    Fetch detail for one dataset, convert it to DataCite XML, and send it
    as a harvest event. Designed to be run inside a thread pool worker -
    all state it touches (SPARQL client, ROR cache) is either thread-local
    or lock-protected.

    :return: One of "sent", "failed", or "skipped".
    """
    try:
        detail_record = fetch_dataset_detail(dataset_iri)
    except Exception:
        logger.exception(
            "Failed to fetch detail for dataset %s after retries; skipping it.",
            dataset_iri,
        )
        return "failed"

    if detail_record is None:
        logger.warning("No detail found for dataset %s, skipping", dataset_iri)
        return "skipped"

    xml_out, datestamp = nfdi4earth_data_to_datacite(detail_record)

    record_identifier = binding_value(detail_record, "dataset") or dataset_iri
    
    event_payload = {
        "record_identifier": record_identifier,
        "datestamp": datestamp,
        "raw_metadata": xml_out,
        "additional_metadata": json.dumps({}),
        "harvest_url": harvest_url,
        "repo_code": config.get("code"),
        "harvest_run_id": run_info.get("id"),
        "is_deleted": False,
    }

    return "sent" if send_harvest_event(event_payload) else "failed"



def run_harvester_nfdi4earth(run_info: dict[str, Any]) -> bool:
    """
    Run a full (or incremental) NFDI4Earth KnowledgeHub harvest and push
    each entry to the data warehouse as a harvest event.

    Two-step approach: first page through cheap "ID only" queries to get
    the list of dataset IRIs, then fetch full metadata for datasets in
    each page concurrently (via a thread pool) through
    `fetch_dataset_detail`

    :param run_info: Dictionary describing the harvest run.

    :return: `True` if every harvest event was sent successfully (and no
            unexpected exception occurred), `False` if any event failed to
            send or an exception was raised during the run.
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

        id_pages = search_all_ids() if not from_date else search_incremental_ids(from_date, until_date)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for id_page in id_pages:
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

                futures = {
                    executor.submit(
                        process_dataset, iri, harvest_url, config, run_info
                    ): iri
                    for iri in dataset_iris
                }

                for future in as_completed(futures):
                    record_count += 1
                    try:
                        result = future.result()
                    except Exception:
                        # Should be rare - process_dataset already catches
                        # its own exceptions - but guard against anything
                        # unexpected escaping so one bad record can't kill
                        # the whole harvest.
                        logger.exception(
                            "Unexpected error processing dataset %s",
                            futures[future],
                        )
                        failed_events += 1
                        continue

                    if result == "sent":
                        harvest_events += 1
                    elif result == "failed":
                        failed_events += 1

        logger.info(
            "Harvest summary: processed %s records, successfully sent %s of them to the warehouse, "
            "failed to send %s records.",
            record_count,
            harvest_events,
            failed_events
        )

        return failed_events == 0

    except Exception as e:
        logger.exception("Unexpected error in run_harvester_nfdi4earth: %s", e)
        logger.info(
            "Harvest summary: processed %s records, successfully sent %s of them to the warehouse, "
            "failed to send %s records.",
            record_count,
            harvest_events,
            failed_events        )
        return False