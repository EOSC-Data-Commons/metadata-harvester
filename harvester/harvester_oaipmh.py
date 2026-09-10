import logging, os, time

from datetime import datetime
from lxml import etree as ET
from oaipmh_scythe import Scythe, OAIResponse
from collections.abc import Iterable, Iterator
from typing import Any

from oaipmh_scythe.models import Record
from oaipmh_scythe.exceptions import NoRecordsMatch

from .additional_metadata_functions import fetch_dataverse_json, fetch_additional_oai, fetch_additional_metadata_hal, \
    fetch_additional_metadata_zenodo
from .db_api_functions import send_harvest_event
from .zenodo_functions import resolve_zenodo_dois

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def apply_xslt_transform(xml: str, transform: ET.XSLT) -> str | None:
    """
    Apply a precompiled XSLT transform to a XML string.

    :param xml: XML as string
    :param transform: Compiled lxml.etree.XSLT object
    :return: Transformed XML as string, or None on failure
    """
    try:
        doc = ET.fromstring(xml.encode("utf-8"))
        result_tree = transform(doc)
        return ET.tostring(result_tree, pretty_print=True, encoding="UTF-8").decode("utf-8")
    except Exception as e:
        logger.warning("Transformation failed: %s", e)
        return None
    


def transformation_and_additional_metadata(raw_metadata: str | None, 
                                           metadata_prefix: str, 
                                           identifier: str, 
                                           additional_protocol: str | None, 
                                           additional_endpoint: str, 
                                           additional_format: str) -> tuple[str | None, str | None]:
    """
    Transform metadata into DataCite format and optionally fetch additional metadata.

    This function performs two main tasks:
    1. If the original format is not DataCite already and a transformation has to be applied, 
    the original XML is stored as additional metadata, like in the case of DABAR
    2. Depending on configuration, it may fetch additional metadata from external services (e.g., Dataverse API, OAI-PMH, HAL API).

    :param raw_metadata (str): The original metadata record (typically XML).
    :param metadata_prefix (str): The metadata format identifier (e.g., "oai_dc", "oai_ddi25", "datacite").
    :param identifier (str): Unique identifier of the record (e.g., DOI or OAI identifier).
    :param additional_protocol (str | None): Name of protocol that is used for additional metadata (OAI-PMH, DATAVERSE_API, HAL_API...)
    :param additional_endpoint (str): Base endpoint URL for additional metadata
    :param additional_format (str): Additional parameter that is needed for some endpoints

    :return: tuple of (raw_metadata, additional_metadata), where raw_metadata is the
            transformed (or original) metadata, and additional_metadata is
            either the original metadata, externally fetched metadata, or None.
            Returns (None, None) on failure.
    """
    # if schema is not DataCite, we will need to transform the XML
    additional_metadata = None

    try:
        if metadata_prefix not in ["oai_datacite", "oai_datacite4", "datacite"]: # if metadata_prefix is not in datacite format
            transform = None
            if metadata_prefix == "oai_ddi25":
                XSLT_PATH = os.path.join(BASE_DIR, "ddi_to_datacite.xsl")
                xslt_doc = ET.parse(XSLT_PATH)
                transform = ET.XSLT(xslt_doc)

            if metadata_prefix == "oai_dc":
                XSLT_PATH = os.path.join(BASE_DIR, "dc_to_datacite.xsl")
                xslt_doc = ET.parse(XSLT_PATH)
                transform = ET.XSLT(xslt_doc)

            if transform is not None:
                if raw_metadata is None:
                    logger.warning("Skipping record %s: no metadata to transform.", identifier)
                    return None, None
                additional_metadata = raw_metadata
                raw_metadata = apply_xslt_transform(raw_metadata, transform)
                if raw_metadata is None:
                    logger.warning("Skipping record %s: transformation to DataCite failed.", identifier)
                    return None, None

        elif additional_protocol == "DATAVERSE_API": # DANS
            additional_metadata = fetch_dataverse_json(
                doi = identifier,
                base_url = additional_endpoint,
                exporter = additional_format
            )

        elif additional_protocol == "OAI-PMH": # DABAR
            additional_metadata = fetch_additional_oai(
                record_id = identifier,
                base_url = additional_endpoint,
                metadata_prefix = additional_format
            )

        elif additional_protocol == "HAL_API": # HAL
            identifier_for_additional_metadata = identifier.split(":")[-1]
            if identifier_for_additional_metadata is None:
                raise ValueError("Incorrect identifier for HAL additional metadata")
            additional_metadata = fetch_additional_metadata_hal(
                record_id = identifier_for_additional_metadata,
                base_url = additional_endpoint
            )

        elif additional_protocol == "ZENODO_API": # ZENODO
            additional_metadata = fetch_additional_metadata_zenodo(
                record_id = identifier,
                base_url = additional_endpoint
            )

    except Exception as e:
        logger.error("Error when fetching additional metadata: %s", e)
        return None, None
    
    return (raw_metadata, additional_metadata)


def process_record(record: Record, config: dict[str, Any], metadata_prefix: str, harvest_url: str,
                    code: str, harvest_run_id: str, repository_name: str) -> str:
    """
    Process a single OAI-PMH record: extract and transform its metadata,
    then send the resulting event to the warehouse.

    :param record: OAI-PMH record object (as returned by Scythe)
    :param config: harvest endpoint configuration dict
    :param metadata_prefix: OAI-PMH metadata prefix the record was harvested with
                             (e.g. "oai_dc", "datacite")
    :param harvest_url: base URL of the OAI-PMH endpoint the record came from
    :param code: repository code
    :param harvest_run_id: identifier of the current harvest run
    :param repository_name: repository name
    :return str: "skipped" if the record was excluded (e.g. ALBA empty sets)
                 "failed" if metadata transformation failed or the event could not be sent to the warehouse
                 "sent" if the event was successfully sent
    """
    try:
        identifier = record.header.identifier
        if identifier is None:
            logger.error("Record has no identifier, skipping")
            return "failed"
        datestamp = record.header.datestamp
        is_deleted = getattr(record.header, "status", None) == "deleted"
        raw_metadata: str | None = ET.tostring(record.xml, pretty_print=True, encoding="unicode")

        # special case where we skip some records for PaNOSC ALBA repository because those records have poor metadata
        if repository_name == "ALBA":
            setSpecs = record.header.setSpecs
            if setSpecs == []:
                return "skipped"

        additional_protocol = None
        additional_endpoint = ""
        additional_format = ""
        harvest_params = config.get("harvest_params")
        if harvest_params:
            additional = harvest_params.get("additional_metadata_params")
            if additional:
                additional_protocol = additional.get("protocol")
                additional_endpoint = additional["endpoint"]
                additional_format = additional["format"]

        # Identifier for additional metadata without namespace (everything after last ":")
        identifier_for_additional_metadata = identifier.split(":")[-1]
        additional_metadata = None

        if not is_deleted:
            raw_metadata, additional_metadata = transformation_and_additional_metadata(
                raw_metadata,
                metadata_prefix,
                identifier,
                additional_protocol,
                additional_endpoint,
                additional_format
            )
            if raw_metadata is None:
                return "failed"

        # metadata and record info to be sent to the warehouse
        event_payload = {
            "record_identifier": identifier_for_additional_metadata,
            "datestamp": datestamp,
            "raw_metadata": raw_metadata,
            "additional_metadata": additional_metadata,
            "harvest_url": harvest_url,
            "repo_code": code,
            "harvest_run_id": harvest_run_id,
            "is_deleted": is_deleted
        }

        return "sent" if send_harvest_event(event_payload) else "failed"
    except Exception as e:
        logger.error("Processing of record failed: %s", e)
        return "failed"



def fetch_records_by_id(client: Scythe, record_ids: set[str], metadata_prefix: str, repository_name: str) -> Iterator[OAIResponse | Record]:
    """
    Fetch records for a list of IDs

    :param client: an active Scythe OAI-PMH client used to fetch individual records
    :param ids: iterable of record identifiers to fetch
    :param metadata_prefix: OAI-PMH metadata prefix to request (e.g. "oai_dc", "datacite")
    :param repository_name: repository_name
    :return: list of successfully fetched record objects
    """
    logger.info("Fetching records for %d identifiers", len(record_ids))
    for id in record_ids:
        try:
            yield client.get_record(identifier = id, metadata_prefix = metadata_prefix)
            if repository_name == "Zenodo":
                time.sleep(2.1)  # zenodo rate limit is 60 requests per minute so 2.1 * 30 = 63 > 60
        except Exception as e:
            logger.error("Record %s failed: %s", id, e)



def fetch_records_by_sets(client: Scythe, sets: Iterable[str | None], from_: str | None,
                          from_date: str | None, until_: str | None, metadata_prefix: str) -> Iterator[OAIResponse | Record]:
    """
    Yield OAI-PMH records across all configured sets, either incrementally (from_ to until_) or as a full harvest.

    :param client: an active Scythe OAI-PMH client used to list records
    :param sets: iterable of OAI-PMH set names to harvest
    :param from_: lower bound datestamp
    :param from_date: original, unformatted "from" date used only for logging
    :param until_: upper bound datestamp
    :param metadata_prefix: OAI-PMH metadata prefix to request (e.g. "oai_dc", "datacite")
    :return: generator yielding record objects across all given sets
    """
    sets = sets or [None]

    for set_name in sets:
        if from_:
            logger.info("Incremental harvest since %s", from_date)
            records = client.list_records(
                from_ = from_, 
                until = until_, 
                metadata_prefix = metadata_prefix, 
                set_ = set_name
            )
        else:
            logger.info("First harvest, fetching all records.")
            records = client.list_records(
                metadata_prefix = metadata_prefix, 
                set_ = set_name, 
                ignore_deleted = True
            )

        try:
            yield from records
        except NoRecordsMatch:
            logger.info(
                "No new records for set %s (from=%s, until=%s) — nothing to harvest.",
                set_name, from_, until_
            )
            continue



def run_harvest_loop(record_iter: Iterator[OAIResponse | Record], need_timeout: bool, config: dict[str, Any], metadata_prefix: str,
                     harvest_url: str, code: str, harvest_run_id: str, repository_name: str
                     ) -> tuple[int, int, int]:
    """
    Consume an iterator of records, processing and counting each one.

    :param record_iter: iterable of record objects to process
    :param need_timeout: if True, sleep 2 seconds after every 10th record
                          to throttle requests to rate-limited repositories
    :param config: harvest endpoint configuration dict
    :param metadata_prefix: OAI-PMH metadata prefix used for this harvest
    :param harvest_url: base URL of the OAI-PMH endpoint
    :param code: repository code
    :param harvest_run_id: identifier of the current harvest run
    :param repository_name: repository name
    :return: tuple of (record_count, harvest_events, failed_events) where
             record_count is the total number of items consumed from
             record_iter, harvest_events is the number successfully sent
             to the warehouse, and failed_events is the number that were
             None, raised an exception, or returned "failed"
    """
    record_count = 0
    harvest_events = 0
    failed_events = 0
    for record in record_iter:
        record_count += 1
        if record is None:
            failed_events += 1
            continue
        if not isinstance(record, Record):
            logger.warning("Skipping unexpected OAIResponse (not a Record)")
            failed_events += 1
            continue
        if need_timeout and record_count % 10 == 0:
            time.sleep(2.1)

        status = process_record(record, config, metadata_prefix, harvest_url, code, harvest_run_id, repository_name)

        if status == "sent":
            harvest_events += 1
        elif status == "failed":
            failed_events += 1
        else:
            continue

    return record_count, harvest_events, failed_events



def run_harvester_oaipmh(run_info: dict[str, Any]) -> bool:
    """
    Run an OAI-PMH harvest.

    :param run_info (dict): info about the harvest run
    :return bool: True if harvest succeeded, False otherwise
    """
    record_count = 0
    harvest_events = 0
    failed_events = 0

    try:
        # extract run info and harvest params
        harvest_run_id = run_info.get("id")
        if harvest_run_id is None:
            raise ValueError("Missing harvest run ID")

        # extract dates
        from_date = run_info.get("from_date")
        from_ = datetime.strptime(from_date, '%Y-%m-%dT%H:%M:%S.%f%z').strftime('%Y-%m-%dT%H:%M:%SZ') if from_date else None
        until_date = run_info.get("until_date")
        if until_date is None:
            raise ValueError("Missing until date")
        until_ = datetime.strptime(until_date, '%Y-%m-%dT%H:%M:%S.%f%z').strftime('%Y-%m-%dT%H:%M:%SZ')

        config = run_info.get("endpoint_config")
        if config is None:
            raise ValueError("Missing config")
        
        # extract parameters from config
        harvest_url = config.get("harvest_url")
        if harvest_url is None:
            raise ValueError("Missing harvest url")

        harvest_params = config.get("harvest_params")
        if harvest_params is None:
            raise ValueError("Missing harvest parameters")

        code = config.get("code")
        if code is None:
            raise ValueError("Missing code of repository")

        # extract additional parameters from harvest_params
        metadata_prefix = harvest_params.get("metadata_prefix", "oai_dc")
        sets = harvest_params.get("set")

        # if master_set_indentifiers is defined then we do harvest by individual IDs
        individual_ids = run_info.get("master_set_identifiers")

        # here we define for which repositories we need to add timeout in order get back all the records from them
        repository_name = config.get("name")
        if repository_name is None:
            raise ValueError("Missing name of repository")
    
        need_timeout = False
        if repository_name in ["ALBA", "Riga Stradins University", "CLARIN-IV", "Zenodo"]:
            need_timeout = True

        # harvesting
        with Scythe(harvest_url, timeout = 180, max_retries = 3, default_retry_after = 60) as client:

            if individual_ids: # currently only used for HAL-Zenodo
                resolved_ids = resolve_zenodo_dois(individual_ids)
                record_iter = fetch_records_by_id(client, resolved_ids, metadata_prefix, repository_name)
            else:
                record_iter = fetch_records_by_sets(client, sets, from_, from_date, until_, metadata_prefix)

            record_count, harvest_events, failed_events = run_harvest_loop(
                record_iter, need_timeout, config, metadata_prefix, harvest_url, code, harvest_run_id, repository_name
            )

        logger.info(
            "Harvest summary: processed %s records, successfully sent %s of them to the warehouse, failed to send %s records",
            record_count,
            harvest_events,
            failed_events
        )

        return failed_events == 0

    except Exception:
        logger.exception("Unexpected error in run_harvester_oaipmh")
        logger.info(
            "Harvest summary: processed %s records, successfully sent %s of them to the warehouse, failed to send %s records",
            record_count,
            harvest_events,
            failed_events
        )
        return False