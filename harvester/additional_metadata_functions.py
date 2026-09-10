import httpx
from typing import Optional
import json
import logging
from typing import Any
from oaipmh_scythe import Scythe
from lxml import etree as ET

_DATAVERSE_CLIENT: Optional[httpx.Client] = None

logger = logging.getLogger(__name__)


def _dataverse_client() -> httpx.Client:
    """Return the shared Dataverse HTTP client, creating it if needed."""
    global _DATAVERSE_CLIENT
    if _DATAVERSE_CLIENT is None:
        _DATAVERSE_CLIENT = httpx.Client(timeout = 30)
    return _DATAVERSE_CLIENT


def close_dataverse_client() -> None:
    global _DATAVERSE_CLIENT
    if _DATAVERSE_CLIENT is None:
        return
    try:
        _DATAVERSE_CLIENT.close()
    except Exception:
        logger.warning("Failed to close Dataverse client")
    finally:
        _DATAVERSE_CLIENT = None


def fetch_dataverse_json(doi: str, base_url: str, exporter: str | None) -> Optional[str]:
    """
    Fetch additional metadata: dataverse json

    :param doi: record identifier
    :param base_url: dataverse API endpoint
    :param exporter: exporter type
    :return: stringified JSON with additional metadata; returns None on error
    """
    params = {"exporter": exporter, "persistentId": doi}
    try:
        response = _dataverse_client().get(base_url, params=params)
        response.raise_for_status()
        return json.dumps(response.json(), indent=2)
    except httpx.HTTPStatusError as e:
        logger.warning(
            "Failed to fetch Dataverse JSON for %s: HTTP %s",
            doi,
            e.response.status_code,
        )
        return None
    except httpx.RequestError as e:
        logger.error("Network error fetching Dataverse JSON for %s: %s", doi, e)
        return None


def fetch_additional_metadata_hal(record_id: str, base_url: str) -> Optional[str]:
    """
    Fetch file metadata from the HAL Search API for a given HAL record.

    :param record_id: HAL record identifier
    :param base_url: HAL Search API endpoint
    :return: stringified JSON response with additional metadata;
            returns None if the request fails or the record is not found
    """
    # Remove version suffix from the ID because query doesn't accept version suffix
    hal_id_without_version = record_id.split("v")[0]
    params = {
        "q": f"halId_s:{hal_id_without_version}",
        "wt": "json",
        # Request only the fields needed to locate and describe attached files
        "fl": ",".join([
            "halId_s",  # document identifier
            "fileMain_s",  # URL of the primary attached file
            "files_s",  # URLs of all attached files
            "fileType_s",  # file type (e.g. PDF)
            "modifiedDate_tdate",  # last modification date
            "producedDate_tdate",  # production/publication date
            "version_i",  # version number
        ]),
    }

    try:
        response = _dataverse_client().get(base_url, params=params)
        response.raise_for_status()
        data = response.json()
        if not data.get("response", {}).get("docs"):
            logger.warning("No HAL records found for %s", record_id)
            return None
        return json.dumps(data, indent=2)

    except httpx.HTTPStatusError as e:
        logger.warning(
            "Failed to fetch HAL JSON for %s: HTTP %s",
            record_id,
            e.response.status_code,
        )
        return None

    except httpx.RequestError as e:
        logger.error(
            "Network error fetching HAL JSON for %s: %s",
            record_id,
            e,
        )
        return None


def fetch_additional_oai(record_id: str, base_url: str, metadata_prefix: str) -> Optional[str]:
    """
    Fetch additional metadata: OAI-PMH

    :param record_id: OAI-PMH record identifier
    :param base_url: OAI-PMH endpoint
    :param metadata_prefix: metadata format
    :return: stringified XML with additional metadata; returns None on error
    """
    try:
        with Scythe(base_url) as client:
            record = client.get_record(identifier=record_id, metadata_prefix=metadata_prefix)
            return ET.tostring(record.xml, pretty_print=True, encoding="unicode")
    except Exception as e:
        logger.warning("Error fetching %s metadata for %s: %s", metadata_prefix, record_id, e)
        return None


def fetch_additional_metadata_zenodo(record_id: str, base_url: str) -> Optional[str]:
    """
    Fetch additional metadata (files) from a Zenodo record via its API.

    :param record_id: Zenodo record identifier is like "oai:zenodo.org:8435696".
    :param base_url: Base URL of Zenodo additional API for file metadata.
    :return: JSON string of the record's file metadata or None if an error occurs.
    """

    # Zenodo OAI IDs come in the form "oai:zenodo.org:8435696".
    # We need only the numeric part at the end for API calls.
    # split(':') -> ['oai', 'zenodo.org', '8435696']
    # [-1] -> '8435696'
    record_id = record_id.split(":")[-1]

    # Construct the full URL to fetch files for this specific record
    # Example: "https://zenodo.org/api/records/8435696/files"
    url = f"{base_url}/{record_id}/files"

    try:
        with httpx.Client() as client:
            response = client.get(url)
            response.raise_for_status()
            return json.dumps(response.json(), indent=2)

    except httpx.HTTPStatusError as e:
        logger.warning(
            "Failed to fetch Zenodo data for %s: HTTP %s",
            record_id,
            e.response.status_code,
        )
        return None

    except httpx.RequestError as e:
        logger.error(
            "Network error fetching Zenodo data for %s: %s",
            record_id,
            str(e),
        )
        return None

