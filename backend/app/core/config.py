"""
================================================================================
FILE: app/core/config.py
MODULE: Module 1 - Core Foundation
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Type-safe configuration loader using Pydantic BaseSettings.
ZERO SECRETS ARE HARDCODED HERE. All credentials are read strictly from
the local .env file or environment variables at runtime.
================================================================================
"""
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    # LLM Settings (OpenRouter)
    OPENROUTER_API_KEY: str = Field(default="", description="OpenRouter API Key")
    LLM_BASE_URL: str = Field(default="https://openrouter.ai/api/v1", description="LLM Base URL")
    LLM_MODEL: str = Field(default="google/gemini-2.0-flash-exp:free", description="LLM Model")

    # Razorpay Credentials (Loaded strictly from .env)
    RAZORPAY_KEY_ID: str = Field(default="", description="Razorpay Key ID")
    RAZORPAY_KEY_SECRET: str = Field(default="", description="Razorpay Key Secret")
    RAZORPAY_WEBHOOK_SECRET: str = Field(default="", description="Razorpay Webhook Secret")

    # Cryptographic Proposal Signing Key
    HMAC_SECRET_KEY: str = Field(default="", description="HMAC Proposal Secret")

    # Server Networking
    ENVIRONMENT: str = Field(default="development", description="Runtime environment")
    PORT: int = Field(default=8000, description="Server port")
    HOST: str = Field(default="0.0.0.0", description="Server host")
    CORS_ORIGINS: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000,https://*.vercel.app",
        description="Allowed CORS origins"
    )

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
