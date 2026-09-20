import math
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    embedding_base_url: str = 'https://api.siliconflow.cn/v1'
    embedding_model: Literal['BAAI/bge-m3'] = 'BAAI/bge-m3'
    embedding_api_key: SecretStr | None = None
    milvus_uri: str = 'http://127.0.0.1:19530'
    knowledge_top_k: int = Field(default=5, ge=1, le=20)
    knowledge_score_threshold: float | None = Field(default=None, ge=-1, le=1)
    knowledge_tool_timeout: float = Field(default=20, gt=0, le=60)
    knowledge_worker_enabled: bool = False
    knowledge_worker_timezone: str = 'Asia/Shanghai'

    def ensure_knowledge_ready(self, *, search: bool = True) -> None:
        if not self.embedding_api_key or not self.embedding_api_key.get_secret_value().strip():
            raise ValueError('embedding_api_key is required')
        if self.embedding_base_url.rstrip('/') != 'https://api.siliconflow.cn/v1':
            raise ValueError('embedding endpoint must be SiliconFlow')
        if search and self.knowledge_score_threshold is None:
            raise ValueError('knowledge score threshold requires calibration')

    database_url: SecretStr | None = None
    tool_timeout: float = Field(default=5.0, gt=0, le=30)

    openai_base_url: str = Field(min_length=1)
    openai_model: str = Field(min_length=1)
    openai_api_key: SecretStr
    context_window_tokens: int = Field(default=8192, gt=0)
    max_output_tokens: int = Field(default=512, gt=0)
    context_margin_tokens: int = Field(default=256, ge=0)
    request_timeout: float = Field(default=60.0, gt=0)
    structured_output_method: Literal["json_mode", "json_schema"] = "json_mode"

    @field_validator("openai_base_url", "openai_model")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("openai_api_key")
    @classmethod
    def reject_blank_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("request_timeout")
    @classmethod
    def require_finite_timeout(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("must be finite")
        return value

    @model_validator(mode="after")
    def require_positive_input_budget(self) -> "Settings":
        if self.context_window_tokens <= self.max_output_tokens + self.context_margin_tokens:
            raise ValueError("context window must exceed output tokens plus margin")
        return self

    @property
    def input_budget(self) -> int:
        return self.context_window_tokens - self.max_output_tokens - self.context_margin_tokens
