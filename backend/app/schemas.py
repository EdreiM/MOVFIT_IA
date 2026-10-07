from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


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
    last_message_preview: str | None = None
    last_message_actor: str | None = None
    external_conversation_id: str | None = None

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
class CustomLink(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1)
    when: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, v: str) -> str:
        url = v.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("URL deve começar com http:// ou https://")
        return url


class AiConfigUpdate(BaseModel):
    ai_name: str | None = None
    tone: str | None = None
    use_emoji: bool | None = None
    system_prompt: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    transcription_api_key: str | None = None
    temperature: float | None = None
    operation_mode: str | None = None
    followup_enabled: bool | None = None
    followup_delay_minutes: int | None = None
    followup_max_attempts: int | None = None
    reply_debounce_seconds: float | None = Field(default=None, ge=1, le=60)
    custom_links: list[CustomLink] | None = None


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
    transcription_api_key_masked: str | None = None
    has_transcription_api_key: bool = False
    temperature: float
    operation_mode: str
    followup_enabled: bool
    followup_delay_minutes: int
    followup_max_attempts: int
    reply_debounce_seconds: float
    custom_links: list[CustomLink] = Field(default_factory=list)

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
    show_by_default: bool = True


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
    show_by_default: bool | None = None


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
    show_by_default: bool

    model_config = {"from_attributes": True}


PromotionAudience = Literal["all", "women", "men", "new_students"]


class PromotionCreate(BaseModel):
    title: str
    message: str
    image_url: str | None = None
    is_active: bool = True
    valid_from: date | None = None
    valid_until: date | None = None
    audience: PromotionAudience = "all"
    mention_on_plan_request: bool = True
    unit_ids: list[UUID] = Field(default_factory=list)
    requires_transfer: bool = True
    transfer_reason: str | None = None
    trigger_keywords: list[str] = Field(default_factory=list)
    sort_order: int = 0


class PromotionUpdate(BaseModel):
    title: str | None = None
    message: str | None = None
    image_url: str | None = None
    is_active: bool | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    audience: PromotionAudience | None = None
    mention_on_plan_request: bool | None = None
    unit_ids: list[UUID] | None = None
    requires_transfer: bool | None = None
    transfer_reason: str | None = None
    trigger_keywords: list[str] | None = None
    sort_order: int | None = None


class PromotionOut(BaseModel):
    id: UUID
    company_id: UUID
    title: str
    message: str
    image_url: str | None
    is_active: bool
    valid_from: date | None
    valid_until: date | None
    audience: PromotionAudience
    mention_on_plan_request: bool
    unit_ids: list[UUID]
    requires_transfer: bool
    transfer_reason: str | None
    trigger_keywords: list[str]
    sort_order: int

    model_config = {"from_attributes": True}

    @field_validator("unit_ids", mode="before")
    @classmethod
    def _coerce_unit_ids(cls, value):
        if not value:
            return []
        return [UUID(str(item)) for item in value]


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
    # O OpenAI só aceita nome de função com letras/números/_/- (até 64
    # caracteres). Sem essa trava, uma chave com acento ou espaço era aceita
    # aqui e depois fazia a API recusar TODA requisição da IA, deixando o
    # atendimento inteiro sem resposta (ver _OPENAI_FUNCTION_NAME_RE).
    tool_key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    description: str | None = None
    parameters: list[ToolParameter] = Field(default_factory=list)
    webhook_url: str
    # Em branco = ferramenta global (todas as integrações). Preenchido = só
    # usada em conversas vindas dessa integração específica.
    integration_id: UUID | None = None
    featured_in_metrics: bool = False


class ToolUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    parameters: list[ToolParameter] | None = None
    webhook_url: str | None = None
    is_active: bool | None = None
    integration_id: UUID | None = None
    featured_in_metrics: bool | None = None


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
    featured_in_metrics: bool
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
    config: dict | None = None
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
    is_student: bool
    was_transferred: bool
    wants_cancellation: bool
    physical_eval_scheduled: bool
    last_physical_eval_date: str | None
    last_physical_eval_time: str | None
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
    students_total: int
    transferred_total: int
    with_human_total: int
    cancellation_requests_total: int
    physical_evals_scheduled_total: int


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


class MetricsNarrativeReport(BaseModel):
    ai_name: str
    paragraphs: list[str]
    generated_at: datetime


class ShowcaseSnippet(BaseModel):
    actor: str
    text: str


class ShowcaseExample(BaseModel):
    modality: str
    title: str
    description: str
    occurred_at: datetime
    snippets: list[ShowcaseSnippet]


class OperationsSummary(BaseModel):
    conversations_total: int
    unique_contacts: int
    messages_inbound: int
    messages_outbound: int
    ai_resolved: int
    transferred: int
    with_human: int
    abandoned_by_client: int
    ai_resolution_rate: float | None
    plans_presented: int
    physical_evals_scheduled: int
    cancellation_requests: int
    avg_conversations_per_day: float


class OperationsDailyVolume(BaseModel):
    day: str
    conversations: int
    inbound_messages: int


class OperationsMotivation(BaseModel):
    label: str
    count: int
    pct: float | None


class OperationsUnitRow(BaseModel):
    unit: str
    conversations: int
    plans: int
    transfers: int
    cancellations: int


class OperationsTransferReason(BaseModel):
    reason: str
    count: int


class TransfersCategoryCount(BaseModel):
    category: str
    count: int


class TransfersSummary(BaseModel):
    period_days: int
    total_transfers: int
    by_category: list[TransfersCategoryCount]
    top_reasons: list[OperationsTransferReason]


class OperationsResponseTimes(BaseModel):
    median_seconds: float | None
    median_business_hours_seconds: float | None
    within_30min_pct: float | None
    over_4h_pct: float | None
    samples: int


class OperationsHourlyVolume(BaseModel):
    hour: int
    inbound_messages: int


class OperationsOutcome(BaseModel):
    label: str
    count: int
    pct: float | None


class OperationsComparisonRow(BaseModel):
    label: str
    current: float
    previous: float
    change_pct: float | None


class OperationsPeriodComparison(BaseModel):
    period_from: date
    period_to: date
    rows: list[OperationsComparisonRow]


class AiOperationsReport(BaseModel):
    period_from: date
    period_to: date
    generated_at: datetime
    ai_name: str
    summary: OperationsSummary
    daily_volume: list[OperationsDailyVolume]
    motivations: list[OperationsMotivation]
    units: list[OperationsUnitRow]
    transfer_reasons: list[OperationsTransferReason]
    response_times: OperationsResponseTimes
    hourly_inbound: list[OperationsHourlyVolume]
    outcomes: list[OperationsOutcome]
    period_comparison: OperationsPeriodComparison | None = None


# API keys (acesso externo, machine-to-machine)
class ApiKeyCreate(BaseModel):
    name: str


class ApiKeyOut(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyOut):
    # Só vem preenchida na resposta de criação — não é persistida em
    # nenhum lugar depois disso, então esse é o único momento que existe.
    key: str
