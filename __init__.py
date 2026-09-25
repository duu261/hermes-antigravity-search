"""Hermes plugin entry point."""

from .provider import AntigravityWebSearchProvider


def register(ctx) -> None:
    ctx.register_web_search_provider(AntigravityWebSearchProvider())
