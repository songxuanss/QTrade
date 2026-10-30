"""Process startup policy and read-only verification of deployed schemas."""

import os

from qtrade_postgres.market_migrate import DIRECTORY
from qtrade_postgres.migrate import apply, verify
from qtrade_runtime.configuration import PROTECTED, load_secrets, validate_configuration


def validate_startup() -> None:
    load_secrets(os.environ)
    validate_configuration(os.environ)
    if os.environ.get("QTRADE_ENV") in PROTECTED:
        verify(os.environ["DATABASE_URL"])
        verify(os.environ["MARKET_DATABASE_URL"], schema="qtrade_market", directory=DIRECTORY)


if __name__ == "__main__":
    # Run as a separate deployment job before starting protected services.
    load_secrets(os.environ)
    validate_configuration(os.environ)
    apply(os.environ["DATABASE_URL"])
    apply(os.environ["MARKET_DATABASE_URL"], schema="qtrade_market", directory=DIRECTORY)
    print("Deployment migrations verified")
