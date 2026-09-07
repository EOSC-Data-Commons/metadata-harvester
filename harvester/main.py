import argparse
import logging
from datetime import datetime, timezone

from .additional_metadata_functions import close_dataverse_client
from .harvester_oaipmh import run_harvester_oaipmh
from harvester.harvester_finbif import run_harvester_finbif
from harvester.harvester_mdposit import run_harvester_mdposit
from harvester.harvester_empiar import run_harvester_empiar
from .db_api_functions import start_harvest_run, close_harvest_run, get_open_run_id, close_warehouse_client
from .logging import setup_logging
from .settings import HarvesterSettings, set_settings


logger = logging.getLogger(__name__)


def run_harvest(
    harvest_url: str,
    settings: HarvesterSettings | None = None,
    setup_logs: bool = True,
) -> bool:
    """
    Run a complete harvest for one repository endpoint. This is the entry point
    to use when driving the harvester from Python.

    :param harvest_url: base URL of the repository harvesting endpoint
    :param settings: settings for this harvest, e.g. HarvesterSettings(WAREHOUSE_API_URL=...).
                     Defaults to the environment profile (ENVIRONMENT / .env / defaults)
    :param setup_logs: set to False to keep the logging config of the calling application
    :return: True if the harvest completed successfully
    """
    set_settings(settings)
    if setup_logs:
        setup_logging()

    # start a new harvest run
    start_time = datetime.now(timezone.utc).isoformat(timespec='seconds')
    harvest_run_id = None
    harvest_success = False

    try:
        run_info = start_harvest_run(harvest_url)
        # if there is no response, try to find an open harvest run and close it
        if run_info is None:
            logger.error("Failed to start harvest run, checking for existing open run...")
            open_run_id = get_open_run_id(harvest_url)
            if open_run_id:
                # if there is an open run for that endpoint, close it as failed and start a new one
                logger.warning("Closing existing open harvest run %s as failed", open_run_id)
                end_time = datetime.now(timezone.utc).isoformat(timespec='seconds')
                close_harvest_run_payload = {
                    "id": open_run_id,
                    "success": False,
                    "started_at": start_time,  # this will overwrite the started_at date that is already in the DB, but API requires this field
                    "completed_at": end_time
                    }
                close_harvest_run(close_harvest_run_payload)
                logger.info("Retry to start a new harvest run...")
                run_info = start_harvest_run(harvest_url)
                if run_info is None:
                    logger.error("Cannot start a new harvest run. Quitting harvester.")
                    return False
            else:
                logger.error("No open run found. Quitting harvester.")
                return False

        harvest_run_id = run_info["id"]
        config = run_info.get("endpoint_config")
        if not config:
            raise ValueError("Missing endpoint_config in API response")

        harvesting_protocol = config.get("protocol")

        if harvesting_protocol == "OAI-PMH":
            harvest_success = run_harvester_oaipmh(run_info)
        elif harvesting_protocol == "FINBIF_API":
            harvest_success = run_harvester_finbif(run_info)
        elif harvesting_protocol == "MDPOSIT_API":
            harvest_success = run_harvester_mdposit(run_info)
        elif harvesting_protocol == "EMPIAR_API":
            harvest_success = run_harvester_empiar(run_info)
        else:
            raise ValueError(f"Unsupported protocol: {harvesting_protocol}")


    except Exception as e:
        logger.exception("Harvest encountered an error: %s", e)
        harvest_success = False


    finally:
        if harvest_run_id:
            end_time = datetime.now(timezone.utc).isoformat(timespec='seconds')
            close_harvest_run_payload = {
                "id": harvest_run_id,
                "success": harvest_success,
                "started_at": start_time,
                "completed_at": end_time,
            }
            close_harvest_run(close_harvest_run_payload)
        close_dataverse_client()
        close_warehouse_client()

    return harvest_success


def main() -> int:
    """CLI entry point: harvest the endpoint given as argument."""
    parser = argparse.ArgumentParser(description="Metadata Harvester")
    parser.add_argument("harvest_url", help="Repository harvesting endpoint")
    args = parser.parse_args()

    return 0 if run_harvest(args.harvest_url) else 1