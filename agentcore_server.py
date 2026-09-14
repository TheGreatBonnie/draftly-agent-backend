import warnings

import uvicorn
from dotenv import load_dotenv

from src.draftly.app.config import get_settings
from src.draftly.observability.logging import configure_logging

warnings.filterwarnings(
    "ignore",
    message=".*type is unknown and inference may fail.*",
    category=UserWarning,
)


def main() -> None:
    load_dotenv()

    settings = get_settings()

    configure_logging(settings=settings)

    uvicorn.run(
        "src.draftly.app.agentcore.app:create_agentcore_app",
        factory=True,
        host=settings.host,
        port=settings.agentcore_port,
        log_level=settings.log_level.lower(),
        log_config=None,
    )


if __name__ == "__main__":
    main()
