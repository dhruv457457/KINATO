"""
================================================================================
FILE: app/core/config.py
MODULE: Module 1 - Core Foundation
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Manages all environment configuration settings using Pydantic BaseSettings.
Loads secrets securely from .env and provides type-safe access to:
  1. OpenRouter Free LLM API settings (Base URL, default free model)
  2. Razorpay Test-Mode credentials (Key ID, Key Secret, Webhook Secret)
  3. HMAC Secret Key for cryptographic proposal signing
  4. Server host, port, and CORS origins for Next.js frontend communication

KEY ATTRIBUTES & FUNCTIONS:
  - Settings: Pydantic v2 BaseSettings model with validation.
  - cors_origins_list: Helper property parsing comma-separated CORS strings.
  - settings: Singleton instance exported for use across all backend services.
================================================================================
"""
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """
    Application Settings schema with environment variable fallback.
    """
    # --------------------------------------------------
    # 1. LLM Provider Configuration (OpenRouter)
    # --------------------------------------------------
    OPENROUTER_API_KEY: str = Field(
        default="",
        description="OpenRouter API Key for free LLM access (e.g. Gemini 2.0 Flash / Llama 3.3)"
    )
    LLM_BASE_URL: str = Field(
        default="https://openrouter.ai/api/v1",
        description="Base URL for OpenRouter / OpenAI-compatible API"
    )
    LLM_MODEL: str = Field(
        default="google/gemini-2.0-flash-exp:free",
        description="Target model identifier on OpenRouter"
    )

    # --------------------------------------------------
    # 2. Razorpay Test Rails Configuration
    # --------------------------------------------------
    RAZORPAY_KEY_ID: str = Field(
        default="rzp_test_TSk4KG18ZnfUX7",
        description="Razorpay Test Mode Public Key ID"
    )
    RAZORPAY_KEY_SECRET: str = Field(
        default="9kBkSTUFTM3sKmZ9y1fsTIuD",
        description="Razorpay Test Mode Private Key Secret (Server-side ONLY)"
    )
    RAZORPAY_WEBHOOK_SECRET: str = Field(
        default="nexusops_webhook_secret_2026",
        description="Secret key used to verify X-Razorpay-Signature on incoming webhooks"
    )

    # --------------------------------------------------
    # 3. Security & Cryptographic Proposal Signing
    # --------------------------------------------------
    HMAC_SECRET_KEY: str = Field(
        default="kinato-production-secret-key-2026",
        description="Secret key used to compute HMAC-SHA256 digest on agreed A2A proposals"
    )

    # --------------------------------------------------
    # 4. Server & Networking
    # --------------------------------------------------
    ENVIRONMENT: str = Field(
        default="development",
        description="Runtime environment: development | production | test"
    )
    PORT: int = Field(default=8000, description="FastAPI server port")
    HOST: str = Field(default="0.0.0.0", description="FastAPI server host")
    CORS_ORIGINS: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000,https://*.vercel.app",
        description="Comma-separated list of allowed frontend origins"
    )

    @property
    def cors_origins_list(self) -> List[str]:
        """Returns parsed list of allowed CORS origins."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


# Exported singleton instance
settings = Settings()
