"""Validate artifact identity without rewriting versioned metadata or dates."""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator


class ArtifactMetadata(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    model_name: str
    model_version: str
    model_config_id: str
    feature_set_name: str

    @field_validator("model_name", "model_version", "model_config_id", "feature_set_name")
    @classmethod
    def locked_identity(cls, value, info: ValidationInfo):
        if info.context and value != info.context[info.field_name]:
            raise ValueError("incompatible with the locked production model")
        return value


class ArtifactFeatureSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    feature_set_name: str
    raw_training_columns: Any

    @field_validator("feature_set_name")
    @classmethod
    def locked_feature_set(cls, value, info: ValidationInfo):
        if info.context and value != info.context["feature_set_name"]:
            raise ValueError("incompatible feature set")
        return value

    @field_validator("raw_training_columns")
    @classmethod
    def neighborhood_first(cls, value):
        if not value or value[0] != "neighborhood":
            raise ValueError("raw_training_columns must begin with neighborhood")
        return value
