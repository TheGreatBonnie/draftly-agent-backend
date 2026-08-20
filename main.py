import logging
import warnings

import uvicorn  # ty: ignore[unresolved-import]

from src.draftly.app.api.app import app
from src.draftly.app.config import get_settings

warnings.filterwarnings(
    "ignore",
    message=".*type is unknown and inference may fail.*",
    category=UserWarning,
)

# Suppress Slack Bolt's "token will be ignored" warning — it's a logger.warning()
# call, not warnings.warn(), so warnings.filterwarnings can't catch it.
logging.getLogger("slack_bolt").setLevel(logging.ERROR)


def main() -> None:
    settings = get_settings()

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
