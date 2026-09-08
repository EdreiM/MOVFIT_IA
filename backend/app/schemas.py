from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# Auth
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: UUID
    email: EmailStr
    full_name: str
    role: str
    status: str

    model_config = {"from_attributes": True}


# Companies
class CompanyCreate(BaseModel):
    name: str
    slug: str
    plan: str = "starter"


class CompanyUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    plan: str | None = None


class CompanyOut(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str
    plan: str
    created_at: datetime

    model_config = {"from_attributes": True}


# Users
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: str = "admin"
    company_ids: list[UUID] = Field(default_factory=list)


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    status: str | None = None
    password: str | None = None


# Numbers
class NumberCreate(BaseModel):
    label: str
    phone: str
    channel_type: str = "whatsapp_qr"
    webhook_mode: bool = False
    config: dict | None = None


class NumberUpdate(BaseModel):
    label: str | None = None
    phone: str | None = None
    channel_type: str | None = None
    is_active: bool | None = None
    webhook_mode: bool | None = None
    config: dict | None = None


class NumberOut(BaseModel):
    id: UUID
    company_id: UUID
    label: str
    phone: str
    channel_type: str
    is_active: bool
    webhook_mode: bool
    config: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


# Conversations
class ConversationOut(BaseModel):
    id: UUID
    company_id: UUID
    number_id: UUID | None
    integration_id: UUID | None
    external_conversation_id: str | None
    contact_phone: str
    contact_name: str | None
    status: str
    ai_enabled: bool
    channel: str | None
    last_message_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationUpdate(BaseModel):
    status: str | None = None
    contact_name: str | None = None


class AiStatusUpdate(BaseModel):
    ai_enabled: bool


class MessageCreate(BaseModel):
    text: str
    content_type: str = "text"


class MessageOut(BaseModel):
    id: UUID
    conversation_id: UUID
    direction: str
    actor: str
    content_type: str
    text: str | None
    raw_payload: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# AI Config
class AiConfigUpdate(BaseModel):
    ai_name: str | None = None
    tone: str | None = None
    use_emoji: bool | None = None
    system_prompt: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    temperature: float | None = None
    operation_mode: str | None = None
    followup_enabled: bool | None = None
    followup_delay_minutes: int | None = None
    followup_max_attempts: int | None = None


class AiConfigOut(BaseModel):
    id: UUID
    company_id: UUID
    integration_id: UUID | None
    ai_name: str
    tone: str | None
    use_emoji: bool
    system_prompt: str
    llm_provider: str
    llm_model: str
    llm_api_key_masked: str | None = None
    has_api_key: bool = False
    temperature: float
    operation_mode: str
    followup_enabled: bool
    followup_delay_minutes: int
    followup_max_attempts: int

    model_config = {"from_attributes": True}


class RagSourceCreate(BaseModel):
    name: str
    source_type: str = "knowledge"
    webhook_url: str


class RagSourceUpdate(BaseModel):
    name: str | None = None
    webhook_url: str | None = None
    is_active: bool | None = None


class RagSourceOut(BaseModel):
    id: UUID
    company_id: UUID
    name: str
    source_type: str
    webhook_url: str
    is_active: bool
    last_latency_ms: float | None
    last_success_at: datetime | None

    model_config = {"from_attributes": True}


# Catálogo: Unidades e Planos
class PlanCreate(BaseModel):
    name: str
    monthly_price: float
    enrollment_fee: float = 0
    fidelity_months: int | None = None
    payment_info: str | None = None
    benefits: list[str] = Field(default_factory=list)
    image_url: str | None = None
    signup_url: str | None = None


class PlanUpdate(BaseModel):
    name: str | None = None
    monthly_price: float | None = None
    enrollment_fee: float | None = None
    fidelity_months: int | None = None
    payment_info: str | None = None
    benefits: list[str] | None = None
    image_url: str | None = None
    signup_url: str | None = None
    is_active: bool | None = None


class PlanOut(BaseModel):
    id: UUID
    unit_id: UUID
    name: str
    monthly_price: float
    enrollment_fee: float
    fidelity_months: int | None
    payment_info: str | None
    benefits: list[str]
    image_url: str | None
    signup_url: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class UnitCreate(BaseModel):
    name: str
    city: str
    unit_type: str | None = None


class UnitUpdate(BaseModel):
    name: str | None = None
    city: str | None = None
    unit_type: str | None = None
    is_active: bool | None = None


class UnitOut(BaseModel):
    id: UUID
    company_id: UUID
    name: str
    city: str
    unit_type: str | None
    is_active: bool
    plans: list[PlanOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ToolParameter(BaseModel):
    name: str
    type: str = "string"
    description: str | None = None
    required: bool = False


class ToolCreate(BaseModel):
    name: str
    tool_key: str
    description: str | None = None
    parameters: list[ToolParameter] = Field(default_factory=list)
    webhook_url: str
    # Em branco = ferramenta global (todas as integrações). Preenchido = só
    # usada em conversas vindas dessa integração específica.
    integration_id: UUID | None = None


class ToolUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    parameters: list[ToolParameter] | None = None
    webhook_url: str | None = None
    is_active: bool | None = None
    integration_id: UUID | None = None


class ToolOut(BaseModel):
    id: UUID
    company_id: UUID
    integration_id: UUID | None
    name: str
    tool_key: str
    description: str | None
    parameters: list[ToolParameter]
    tool_type: str
    webhook_url: str | None
    is_active: bool
    last_executed_at: datetime | None

    model_config = {"from_attributes": True}


# Test chat (playground)
class TestChatRequest(BaseModel):
    text: str
    integration_id: UUID | None = None


class TestChatResponse(BaseModel):
    conversation_id: UUID
    reply: str | None


# Integrations
class IntegrationCreate(BaseModel):
    name: str
    integration_type: str = "webhook"
    adapter_key: str = "generic_mapping"
    outbound_url: str | None = None
    field_mapping: dict | None = None
    config: dict | None = None


class IntegrationUpdate(BaseModel):
    name: str | None = None
    outbound_url: str | None = None
    field_mapping: dict | None = None
    is_active: bool | None = None


class IntegrationOut(BaseModel):
    id: UUID
    company_id: UUID
    name: str
    integration_type: str
    adapter_key: str
    inbound_secret: str | None
    outbound_url: str | None
    field_mapping: dict | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# Leads (cadastro estruturado do cliente, separado da conversa)
class LeadOut(BaseModel):
    id: UUID
    company_id: UUID
    phone: str
    name: str | None
    cpf: str | None
    email: str | None
    birthdate: date | None
    unit: str | None
    stage: str
    custom_fields: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadUpdate(BaseModel):
    name: str | None = None
    cpf: str | None = None
    email: str | None = None
    birthdate: date | None = None
    unit: str | None = None
    stage: str | None = None
    custom_fields: dict | None = None


# Metrics
class MetricsOverview(BaseModel):
    conversations_total: int
    messages_inbound: int
    messages_outbound: int
    ai_resolved: int
    human_resolved: int
    avg_response_seconds: float | None
    ai_resolution_rate: float | None


class MetricsPoint(BaseModel):
    day: str
    conversations_total: int
    messages_inbound: int
    messages_outbound: int


class StageCount(BaseModel):
    stage: str
    count: int


class ToolStats(BaseModel):
    tool_key: str
    tool_name: str
    total_calls: int
    success_calls: int
    failed_calls: int
    distinct_conversations: int
    success_rate: float | None
