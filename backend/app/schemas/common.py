from pydantic import BaseModel, Field


class Envelope[T](BaseModel):
    data: T
    request_id: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict] = Field(default_factory=list)


class ErrorEnvelope(BaseModel):
    error: ErrorBody
    request_id: str
