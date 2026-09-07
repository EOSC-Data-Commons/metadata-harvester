import httpx
import logging
from typing import Optional, Any
from harvester.settings import current_settings

logger = logging.getLogger(__name__)

# shared HTTP client, created on first use so that settings overrides are taken into account
_WAREHOUSE_CLIENT: Optional[httpx.Client] = None


def _client() -> httpx.Client:
    """Return the shared warehouse HTTP client, creating it if needed."""
    global _WAREHOUSE_CLIENT
    if _WAREHOUSE_CLIENT is None:
        _WAREHOUSE_CLIENT = httpx.Client(timeout=current_settings().WAREHOUSE_API_TIMEOUT)
    return _WAREHOUSE_CLIENT


def _warehouse_url(route: str) -> str:
    """Build a warehouse API route URL from the configured base URL."""
    return f"{current_settings().WAREHOUSE_API_URL}/{route}"


def start_harvest_run(harvest_url: str) -> Optional[dict[str, Any]]:
    """
    POST /harvest_run to create a new harvest run.

    :param harvest_url: endpoint for harvesting
    :return: JSON response (dict) containing 'harvest_run_id', optionally 'last_harvest_date', and endpoint config; returns None on error.
    """
    payload = {"harvest_url": harvest_url}
    try:
        response = _client().post(_warehouse_url("harvest_run"), json=payload)
        response.raise_for_status()
        run_info: dict[str, Any] = response.json()
        logger.info("Started harvest run id=%s.", run_info.get("id"))
        return run_info
    except httpx.RequestError as e:
        logger.error("Failed to start harvest run for %s: %s", harvest_url, e)
        return None

def get_open_run_id(harvest_url: str) -> Optional[str]:
    """
    GET /harvest_run to fetch an open harvest run ID if it exists.

    :param harvest_url: endpoint for harvesting
    :return: run ID if status is 'open', otherwise None
    """
    params = {"harvest_url": harvest_url}
    try:
        response = _client().get(_warehouse_url("harvest_run"), params=params)
        response.raise_for_status()

        response_json: dict[str, Any] = response.json()
        runs = response_json.get("harvest_runs", [])

        if not runs:
            return None

        run = runs[0]

        if run.get("status") == "open":
            run_id =  run.get("id")
            return str(run_id) if run_id is not None else None

        return None

    except httpx.HTTPStatusError as e:
        logger.error("HTTP error while checking open harvest run for %s: %s", harvest_url, e.response.text)
        return None

    except httpx.RequestError as e:
        logger.error("Network error while checking open harvest run for %s: %s", harvest_url, e)
        return None


def close_harvest_run(payload: dict[str, Any]) -> None:
    """
    PUT /harvest_run to close the harvest run.

    :param payload: payload for API post request to close the harvest run
    """
    run_id = payload.get("id")
    try:
        response = _client().put(_warehouse_url("harvest_run"), json=payload)
        response.raise_for_status()
        logger.info(
            "Closed harvest run %s — started %s, finished %s",
            run_id,
            payload.get("started_at"),
            payload.get("completed_at"),
        )
    except httpx.RequestError as e:
        logger.error("Failed to close harvest run %s: %s", run_id, e)


def send_harvest_event(event_payload: dict[str, Any]) -> bool:
    """
    Send event_payload to API.

    :param event_payload: dictionary containing event data for harvest_event route
    :return logical: True if the payload has been sent to API successfully
    """
    try:
        response = _client().post(_warehouse_url("harvest_event"), json=event_payload)
        response.raise_for_status()
        return True
    except httpx.HTTPStatusError as e:
        logger.error("Failed to send record %s to API: HTTP status error %s: %s", event_payload.get("record_identifier"), e, e.response.text)
        return False
    except httpx.RequestError as e:
        logger.error("Failed to send record %s to API: Request error %s", event_payload.get("record_identifier"), e)
        return False


def close_warehouse_client() -> None:
    global _WAREHOUSE_CLIENT
    if _WAREHOUSE_CLIENT is None:
        return
    try:
        _WAREHOUSE_CLIENT.close()
    except Exception:
        logger.warning("Failed to close warehouse client")
    finally:
        _WAREHOUSE_CLIENT = None