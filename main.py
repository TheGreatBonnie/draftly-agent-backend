import warnings

import uvicorn

from src.draftly.app.api.app import app
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
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        log_config=None,
    )


if __name__ == "__main__":
    main()
