"""Realtime process probe; provider streams are added with market-data tasks."""

from fastapi import FastAPI

from qtrade_composition.startup import validate_startup
from qtrade_runtime.health import create_health_app

validate_startup()
app: FastAPI = create_health_app("qtrade-realtime")
