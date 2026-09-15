import logging
import warnings

from dotenv import load_dotenv

load_dotenv()

# Suppress slack_bolt warnings before app import triggers them
logging.getLogger("slack_bolt").setLevel(logging.ERROR)

import uvicorn

from src.draftly.app.config import get_settings
from src.draftly.observability.logging import configure_logging

warnings.filterwarnings(
    "ignore",
    message=".*type is unknown and inference may fail.*",
    category=UserWarning,
)


def main() -> None:
    settings = get_settings()

    configure_logging(settings=settings)

    uvicorn.run(
        "src.draftly.app.api.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        log_config=None,
        reload=settings.environment == "development",
    )


if __name__ == "__main__":
    main()
