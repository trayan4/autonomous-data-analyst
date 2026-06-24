"""
loads model/connection settings from .env
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

# The package can be installed anywhere; the user's .env lives at their project root,
# so discover it from the working directory rather than relative to this file.
load_dotenv(find_dotenv(usecwd=True))


def normalize_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    marker = "/openai/v1"
    if marker in url:
        url = url[: url.index(marker) + len(marker)]   # trim anything after /openai/v1
    else:
        url = url + "/openai/v1"                        # bare resource URL -> add it
    return url + "/"


def _require(name: str) -> str:
    v = os.getenv(name, "")
    if not v or v.startswith("<"):
        raise RuntimeError(f"missing env var {name} - set it in your .env or environment")
    return v


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    deployment: str # cheap / default tier
    deployment_strong: str # strong tier (falls back to cheap if unset)
    deployment_embed: str # embedding model for hybrid retrieval


def get_settings() -> Settings:
    cheap = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    return Settings(
        base_url=normalize_base_url(_require("AZURE_OPENAI_BASE_URL")),
        api_key=_require("AZURE_OPENAI_API_KEY"),
        deployment=cheap,
        deployment_strong=os.getenv("AZURE_OPENAI_DEPLOYMENT_STRONG", "") or cheap,
        deployment_embed=os.getenv("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-small"),
    )


if __name__ == "__main__":
    print("normalizer check:")
    for s in [
        "https://trayans-foundry.services.ai.azure.com/openai/v1/responses",
        "https://trayans-foundry.services.ai.azure.com/openai/v1/",
        "https://trayans-foundry.services.ai.azure.com",
    ]:
        print(f"  {s}\n    -> {normalize_base_url(s)}")

    cfg = get_settings()
    masked = cfg.api_key[:3] + "..." + cfg.api_key[-2:] if len(cfg.api_key) > 6 else "***"
    print("\nloaded settings:")
    print("  base_url  :", cfg.base_url)
    print("  deployment:", cfg.deployment, "(cheap/default)")
    print("  strong    :", cfg.deployment_strong, "(strong tier)")
    print("  api_key   :", masked)
    print("\nconfig OK")
