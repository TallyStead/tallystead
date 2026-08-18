from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator


class SetupStatusResponse(BaseModel):
    setup_required: bool


class ServerIdentityResponse(BaseModel):
    public_url: str
    api_version: str
    local_https_required: bool = True


class SetupRequest(BaseModel):
    household_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=256)
    device_name: str | None = Field(default=None, max_length=120)
    create_demo: bool = False
    demo_reference_date: date | None = None
    demo_volume: Literal["smoke", "realistic"] = "realistic"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    device_name: str | None = Field(default=None, max_length=120)


class ProxyLoginRequest(BaseModel):
    device_name: str | None = Field(default=None, max_length=120)


class ProxyAuthStatusResponse(BaseModel):
    available: bool
    email: EmailStr | None = None
    display_name: str | None = None


class ProxyLinkStatusResponse(BaseModel):
    linked: bool
    provider: str | None = None
    email_at_link: EmailStr | None = None
    created_at: str | None = None
    last_used_at: str | None = None


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetFinishRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    password: str = Field(min_length=12, max_length=256)


class SessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: UUID
    household_id: UUID
    role: str


class CurrentUserResponse(BaseModel):
    user_id: UUID
    email: EmailStr
    display_name: str
    household_id: UUID
    household_name: str
    role: str
    session_idle_minutes: int


class MemberCreateRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=256)
    role: str


class MemberRoleRequest(BaseModel):
    role: str


class MemberResponse(BaseModel):
    user_id: UUID
    membership_id: UUID
    email: EmailStr
    display_name: str
    role: str
    is_active: bool


class SessionTokenResponse(BaseModel):
    session_id: UUID
    user_id: UUID
    device_name: str | None
    created_at: str
    expires_at: str
    is_current: bool


class AdminPasswordResetRequest(BaseModel):
    password: str = Field(min_length=12, max_length=256)


class SystemStatusResponse(BaseModel):
    environment: str
    database_connected: bool
    object_store_configured: bool
    smtp_configured: bool
    passkeys_enabled: bool
    worker_healthy: bool
    worker_last_seen_at: str | None
    latest_backup_status: str | None
    latest_backup_at: str | None


class NetworkConfigurationResponse(BaseModel):
    canonical_url: str
    internal_url: str | None
    access_mode: str
    trusted_proxy_cidrs: list[str]
    forward_auth_enabled: bool
    certificate_mode: str


class CertificateStatusResponse(BaseModel):
    subject: str | None = None
    issuer: str | None = None
    names: list[str] = Field(default_factory=list)
    expires_at: str | None = None
    renewal_status: str


class NetworkStatusResponse(BaseModel):
    configuration: NetworkConfigurationResponse
    certificate: CertificateStatusResponse


class RequestHeaderDiagnostic(BaseModel):
    name: str
    value: str


class EffectiveRequestResponse(BaseModel):
    effective_url: str
    scheme: str
    host: str
    source_address: str | None
    transport_address: str | None
    connection_route: str
    forwarded_headers_trusted: bool
    headers: list[RequestHeaderDiagnostic]


class AccountStatusRequest(BaseModel):
    is_active: bool


class IntegrationSettingsRequest(BaseModel):
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, max_length=320)
    smtp_password: str | None = Field(default=None, max_length=1024)
    smtp_from_address: EmailStr | None = None
    smtp_security: str | None = Field(default=None, pattern="^(starttls|tls|none)$")
    imap_host: str | None = Field(default=None, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_username: str | None = Field(default=None, max_length=320)
    imap_password: str | None = Field(default=None, max_length=1024)
    imap_archive_processed: bool | None = None
    smtp_notifications_enabled: bool | None = None
    ai_provider: str | None = Field(default=None, pattern="^(ollama|lm_studio)$")
    ai_base_url: str | None = Field(default=None, max_length=500)
    ai_model: str | None = Field(default=None, max_length=255)
    ai_enabled: bool | None = None
    ai_extract_enabled: bool | None = None
    ai_suggestions_enabled: bool | None = None
    ai_resource_limit: str | None = Field(default=None, pattern="^(low|medium|high)$")


class IntegrationStatusResponse(BaseModel):
    smtp_configured: bool
    imap_configured: bool
    smtp_host: str | None
    smtp_port: int | None
    smtp_username: str | None
    smtp_from_address: str | None
    smtp_security: str | None
    imap_host: str | None
    imap_port: int | None
    imap_username: str | None
    ai_configured: bool
    ai_provider: str | None
    ai_base_url: str | None
    ai_model: str | None
    updated_at: str | None
    imap_archive_processed: bool
    smtp_notifications_enabled: bool
    ai_enabled: bool
    ai_extract_enabled: bool
    ai_suggestions_enabled: bool
    ai_resource_limit: str


class IntegrationTestResponse(BaseModel):
    integration: str
    reachable: bool
    detail: str


class VisionModelTestResponse(BaseModel):
    success: bool
    provider: str
    model: str
    duration_ms: int
    checks: dict[str, bool]
    detail: str


class PasskeyOptionsResponse(BaseModel):
    ceremony_id: UUID
    public_key: dict[str, Any]


class PasskeyRegistrationFinishRequest(BaseModel):
    ceremony_id: UUID
    credential: dict[str, Any]


class PasskeyLoginOptionsRequest(BaseModel):
    email: EmailStr


class PasskeyLoginFinishRequest(BaseModel):
    ceremony_id: UUID
    credential: dict[str, Any]
    device_name: str | None = Field(default=None, max_length=120)


class PasskeyResponse(BaseModel):
    passkey_id: UUID
    created_at: str
    last_used_at: str | None


class FinancialAccountCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    account_type: str = Field(pattern="^(checking|savings|cash|money_market|credit_card|loan|mortgage|line_of_credit|brokerage|investment|401k|403b|traditional_ira|roth_ira|pension|hsa|fsa|property|vehicle|business_checking|business_savings|business_credit_card|business_loan|other)$")
    currency_code: str = Field(pattern="^(USD|CAD|MXN)$")
    opening_balance_minor: int = 0
    include_in_planner: bool | None = None
    include_in_net_worth: bool = True
    ownership_scope: str | None = Field(default=None, pattern="^(household|business)$")
    balance_nature: str | None = Field(default=None, pattern="^(asset|liability)$")
    liquidity: str | None = Field(default=None, pattern="^(spendable|restricted|invested|non_liquid|liability)$")
    tax_treatment: str | None = Field(default=None, pattern="^(none|taxable|tax_deferred|tax_free|health_advantaged)$")
    institution: str | None = Field(default=None, max_length=200)
    masked_identifier: str | None = Field(default=None, max_length=24)


class FinancialAccountResponse(BaseModel):
    account_id: UUID
    name: str
    account_type: str
    currency_code: str
    opening_balance_minor: int
    balance_minor: int
    include_in_planner: bool
    include_in_net_worth: bool
    ownership_scope: str
    balance_nature: str
    liquidity: str
    tax_treatment: str
    institution: str | None
    masked_identifier: str | None
    current_value_minor: int
    valuation_as_of: date | None
    is_archived: bool


class FinancialAccountUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    include_in_planner: bool | None = None
    include_in_net_worth: bool | None = None
    ownership_scope: str | None = Field(default=None, pattern="^(household|business)$")
    balance_nature: str | None = Field(default=None, pattern="^(asset|liability)$")
    liquidity: str | None = Field(default=None, pattern="^(spendable|restricted|invested|non_liquid|liability)$")
    tax_treatment: str | None = Field(default=None, pattern="^(none|taxable|tax_deferred|tax_free|health_advantaged)$")
    institution: str | None = Field(default=None, max_length=200)
    masked_identifier: str | None = Field(default=None, max_length=24)
    is_archived: bool | None = None


class AccountValuationCreateRequest(BaseModel):
    valuation_date: date
    value_minor: int = Field(ge=0)
    currency_code: str = Field(pattern="^(USD|CAD|MXN)$")
    source_type: str = Field(default="manual", pattern="^(manual|imported)$")
    note: str | None = Field(default=None, max_length=500)


class AccountValuationResponse(AccountValuationCreateRequest):
    valuation_id: UUID
    account_id: UUID


class NetWorthAccountResponse(BaseModel):
    account_id: UUID
    name: str
    account_type: str
    ownership_scope: str
    balance_nature: str
    liquidity: str
    value_minor: int
    currency_code: str
    valuation_as_of: date | None


class NetWorthResponse(BaseModel):
    as_of: date
    currency_code: str
    asset_total_minor: int
    liability_total_minor: int
    net_worth_minor: int
    household_net_worth_minor: int
    business_net_worth_minor: int
    accounts: list[NetWorthAccountResponse]


class CategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category_type: str = Field(pattern="^(income|expense)$")


class CategoryResponse(BaseModel):
    category_id: UUID
    name: str
    category_type: str
    is_system_default: bool
    is_archived: bool


class CategoryUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_archived: bool | None = None


class MerchantCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class MerchantUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_archived: bool | None = None


class MerchantResponse(BaseModel):
    merchant_id: UUID
    name: str
    aliases: list[str]
    is_archived: bool


class MerchantProfileSummaryResponse(BaseModel):
    profile_id: UUID
    merchant_id: UUID | None
    name: str
    aliases: list[str]
    is_normalized: bool
    is_archived: bool
    transaction_count: int


class TransactionSplitRequest(BaseModel):
    category_id: UUID
    amount_minor: int
    memo: str | None = Field(default=None, max_length=255)


class TransactionSplitResponse(BaseModel):
    split_id: UUID
    category_id: UUID
    category_name: str
    amount_minor: int
    memo: str | None


class LedgerTransactionCreateRequest(BaseModel):
    account_id: UUID
    transaction_date: date
    amount_minor: int
    currency_code: str = Field(pattern="^(USD|CAD|MXN)$")
    status: str = Field(default="posted", pattern="^(pending|posted)$")
    payee: str | None = Field(default=None, max_length=200)
    merchant_id: UUID | None = None
    memo: str | None = Field(default=None, max_length=2000)
    activity_type: str = Field(default="regular", pattern="^(regular|contribution|employer_match|purchase|sale|dividend|interest|fee|withdrawal|market_adjustment)$")
    splits: list[TransactionSplitRequest] = Field(default_factory=list, max_length=100)


class LedgerTransactionResponse(BaseModel):
    transaction_id: UUID
    account_id: UUID
    account_name: str
    transaction_date: date
    amount_minor: int
    currency_code: str
    status: str
    payee: str | None
    raw_payee: str | None
    merchant_id: UUID | None
    merchant_name: str | None
    memo: str | None
    source_type: str
    source_reference: str | None
    activity_type: str
    transfer_id: UUID | None
    reversal_of_transaction_id: UUID | None
    corrected_from_transaction_id: UUID | None
    reconciled_at: str | None
    splits: list[TransactionSplitResponse]


class LedgerTransactionPageResponse(BaseModel):
    items: list[LedgerTransactionResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class TransactionUpdateRequest(BaseModel):
    payee: str | None = Field(default=None, max_length=200)
    merchant_id: UUID | None = None
    memo: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, pattern="^(pending|posted|voided)$")
    splits: list[TransactionSplitRequest] | None = Field(default=None, max_length=100)
    reason: str = Field(min_length=3, max_length=500)


class TransactionReverseRequest(BaseModel):
    transaction_date: date
    reason: str = Field(min_length=3, max_length=500)


class TransactionCorrectionRequest(LedgerTransactionCreateRequest):
    reason: str = Field(min_length=3, max_length=500)


class TransactionReconcileRequest(BaseModel):
    reconciled: bool


class TransactionRevisionResponse(BaseModel):
    revision_id: UUID
    reason: str
    before_snapshot: dict[str, Any]
    created_at: str


class TransactionDetailResponse(BaseModel):
    transaction: LedgerTransactionResponse
    revisions: list[TransactionRevisionResponse]


class BalanceExplanationResponse(BaseModel):
    account_id: UUID
    account_name: str
    currency_code: str
    as_of: date
    include_pending: bool
    opening_balance_minor: int
    activity_minor: int
    balance_minor: int
    included_transaction_ids: list[UUID]


class TransferCreateRequest(BaseModel):
    from_account_id: UUID
    to_account_id: UUID
    transaction_date: date
    amount_minor: int = Field(gt=0)
    currency_code: str = Field(pattern="^(USD|CAD|MXN)$")
    status: str = Field(default="posted", pattern="^(pending|posted)$")
    memo: str | None = Field(default=None, max_length=2000)


class TransferResponse(BaseModel):
    transfer_id: UUID
    from_transaction: LedgerTransactionResponse
    to_transaction: LedgerTransactionResponse


class BillProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    payee: str | None = Field(default=None, max_length=200)
    cadence: str = Field(pattern="^(weekly|biweekly|monthly|quarterly|yearly|irregular)$")
    next_due_date: date
    expected_amount_minor: int = Field(gt=0)
    minimum_amount_minor: int | None = Field(default=None, ge=0)
    maximum_amount_minor: int | None = Field(default=None, ge=0)
    currency_code: str = Field(pattern="^(USD|CAD|MXN)$")
    is_essential: bool = False
    priority: int = Field(default=3, ge=1, le=5)


class BillProfileResponse(BillProfileCreateRequest):
    bill_profile_id: UUID
    next_due_date: date | None
    due_day: int | None
    is_active: bool


class BillProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    payee: str | None = Field(default=None, max_length=200)
    cadence: str | None = Field(default=None, pattern="^(weekly|biweekly|monthly|quarterly|yearly|irregular)$")
    next_due_date: date | None = None
    expected_amount_minor: int | None = Field(default=None, gt=0)
    minimum_amount_minor: int | None = Field(default=None, ge=0)
    maximum_amount_minor: int | None = Field(default=None, ge=0)
    is_essential: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    is_active: bool | None = None


class DebtCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    lender: str | None = Field(default=None, max_length=200)
    account_id: UUID | None = None
    balance_minor: int = Field(ge=0)
    balance_as_of_date: date | None = None
    apr_basis_points: int = Field(default=0, ge=0, le=100000)
    minimum_payment_minor: int = Field(gt=0)
    due_day: int = Field(ge=1, le=31)
    next_due_date: date
    currency_code: str = Field(pattern="^(USD|CAD|MXN)$")


class DebtUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    lender: str | None = Field(default=None, max_length=200)
    account_id: UUID | None = None
    balance_minor: int | None = Field(default=None, ge=0)
    balance_as_of_date: date | None = None
    apr_basis_points: int | None = Field(default=None, ge=0, le=100000)
    minimum_payment_minor: int | None = Field(default=None, gt=0)
    due_day: int | None = Field(default=None, ge=1, le=31)
    next_due_date: date | None = None
    currency_code: str | None = Field(default=None, pattern="^(USD|CAD|MXN)$")
    is_active: bool | None = None


class DebtResponse(DebtCreateRequest):
    debt_id: UUID
    balance_anchor_minor: int
    is_active: bool


class PaymentLinkRequest(BaseModel):
    transaction_id: UUID
    amount_minor: int = Field(gt=0)
    principal_amount_minor: int | None = Field(default=None, ge=0)


class PaymentLinkResponse(BaseModel):
    payment_link_id: UUID
    transaction_id: UUID
    amount_minor: int


class BillInstanceUpdateRequest(BaseModel):
    expected_amount_minor: int | None = Field(default=None, gt=0)
    status: str | None = Field(default=None, pattern="^(upcoming|changed|skipped)$")
    note: str | None = Field(default=None, max_length=2000)


class BillInstanceResponse(BaseModel):
    bill_instance_id: UUID
    bill_profile_id: UUID | None
    debt_id: UUID | None
    name: str
    due_date: date
    expected_amount_minor: int
    minimum_amount_minor: int | None
    maximum_amount_minor: int | None
    paid_amount_minor: int
    currency_code: str
    is_essential: bool
    priority: int
    status: str
    note: str | None
    payment_links: list[PaymentLinkResponse]


class IncomeSourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    payer: str | None = Field(default=None, max_length=200)
    cadence: str = Field(pattern="^(weekly|biweekly|monthly|quarterly|yearly|irregular)$")
    next_expected_date: date
    expected_amount_minor: int = Field(gt=0)
    minimum_amount_minor: int | None = Field(default=None, ge=0)
    maximum_amount_minor: int | None = Field(default=None, ge=0)
    currency_code: str = Field(pattern="^(USD|CAD|MXN)$")
    confidence_percent: int = Field(default=100, ge=0, le=100)


class IncomeSourceResponse(IncomeSourceCreateRequest):
    income_source_id: UUID
    next_expected_date: date | None
    is_active: bool


class IncomeSourceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    payer: str | None = Field(default=None, max_length=200)
    cadence: str | None = Field(default=None, pattern="^(weekly|biweekly|monthly|quarterly|yearly|irregular)$")
    next_expected_date: date | None = None
    expected_amount_minor: int | None = Field(default=None, gt=0)
    minimum_amount_minor: int | None = Field(default=None, ge=0)
    maximum_amount_minor: int | None = Field(default=None, ge=0)
    confidence_percent: int | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None


class IncomeEventCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    expected_date: date
    expected_amount_minor: int = Field(gt=0)
    minimum_amount_minor: int | None = Field(default=None, ge=0)
    maximum_amount_minor: int | None = Field(default=None, ge=0)
    currency_code: str = Field(pattern="^(USD|CAD|MXN)$")
    confidence_percent: int = Field(default=50, ge=0, le=100)
    note: str | None = Field(default=None, max_length=2000)


class IncomeEventReceiveRequest(BaseModel):
    transaction_id: UUID


class IncomeEventResponse(IncomeEventCreateRequest):
    income_event_id: UUID
    income_source_id: UUID | None
    received_transaction_id: UUID | None
    status: str


class CalendarItemResponse(BaseModel):
    item_type: str
    item_id: UUID
    name: str
    event_date: date
    amount_minor: int
    currency_code: str
    status: str
    priority: int | None


class GenerationResponse(BaseModel):
    bill_instances_created: int
    income_events_created: int
    debt_instances_created: int


class PlannerRequest(BaseModel):
    as_of_date: date
    horizon_days: int = Field(default=30, ge=1, le=365)
    currency_code: str = Field(default="USD", pattern="^(USD|CAD|MXN)$")
    cash_buffer_minor: int = Field(default=0, ge=0)
    include_pending: bool = True


class PlannerAccountResponse(BaseModel):
    account_id: UUID
    name: str
    balance_minor: int


class PlannerTimelineItemResponse(BaseModel):
    item_type: str
    item_id: UUID | None
    name: str
    event_date: date
    amount_minor: int
    projected_balance_minor: int
    confidence_percent: int | None = None
    explanation: str


class PlannerReserveResponse(BaseModel):
    bill_instance_id: UUID
    name: str
    due_date: date
    required_minor: int
    funded_minor: int
    shortfall_minor: int
    status: str
    explanation: str


class PlannerShortfallResponse(BaseModel):
    event_date: date
    amount_minor: int
    obligation_name: str
    explanation: str


class PlannerForecastResponse(BaseModel):
    snapshot_id: UUID | None = None
    rule_version: str
    input_hash: str
    as_of_date: date
    horizon_date: date
    currency_code: str
    include_pending: bool
    cash_buffer_minor: int
    planning_balance_minor: int
    available_to_plan_minor: int
    safe_to_spend_minor: int
    reserved_now_minor: int
    expected_income_minor: int
    required_outflow_minor: int
    ending_balance_minor: int
    accounts: list[PlannerAccountResponse]
    excluded_accounts: list[str]
    timeline: list[PlannerTimelineItemResponse]
    reserves: list[PlannerReserveResponse]
    shortfalls: list[PlannerShortfallResponse]
    warnings: list[str]
    assumptions: list[str]


class ImportSourceRequest(BaseModel):
    account_id: UUID
    name: str = Field(min_length=1, max_length=160)
    institution: str | None = Field(default=None, max_length=200)
    date_column: str = Field(default="date", min_length=1, max_length=80)
    payee_column: str = Field(default="description", min_length=1, max_length=80)
    original_payee_column: str | None = Field(default=None, max_length=80)
    amount_column: str | None = Field(default="amount", max_length=80)
    debit_column: str | None = Field(default=None, max_length=80)
    credit_column: str | None = Field(default=None, max_length=80)
    status_column: str | None = Field(default=None, max_length=80)
    category_column: str | None = Field(default=None, max_length=80)
    memo_column: str | None = Field(default=None, max_length=80)
    amount_sign: Literal["positive_in", "positive_out"] = "positive_in"
    date_format: str = Field(default="%Y-%m-%d", min_length=1, max_length=40)
    export_method: str | None = Field(default=None, max_length=200)
    export_instructions: str | None = Field(default=None, max_length=5000)
    notes: str | None = Field(default=None, max_length=2000)
    reminder_interval_days: int | None = Field(default=None, ge=1, le=3650)
    next_reminder_date: date | None = None
    reminders_enabled: bool = False

    @model_validator(mode="after")
    def validate_amount_mapping(self):
        fields = (self.amount_column, self.debit_column, self.credit_column)
        if not any(value and value.strip() for value in fields):
            raise ValueError("Map a signed amount column or at least one debit/credit column")
        return self


class ImportSourceResponse(ImportSourceRequest):
    source_id: UUID
    account_name: str
    format_type: str
    last_imported_at: str | None
    reminder_status: str
    is_active: bool


class CsvImportRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    csv_text: str = Field(min_length=1, max_length=5_000_000)


class CsvInspectionResponse(BaseModel):
    headers: list[str]
    row_count: int
    mappings: dict[str, str | None]
    confidence: dict[str, str]
    date_format: str
    preview_rows: list[dict[str, str]]


class ImportBatchResponse(BaseModel):
    batch_id: UUID
    source_id: UUID
    filename: str
    file_checksum: str
    parser_version: str
    status: str
    row_count: int
    candidate_count: int
    duplicate_count: int
    invalid_count: int
    ready_count: int = 0
    transfer_count: int = 0
    recurring_count: int = 0
    review_count: int = 0
    mapping_version_id: UUID | None = None
    ingestion_channel: str = "csv_upload"
    upstream_reference: str | None = None
    created_at: str


class MatchCandidateResponse(BaseModel):
    match_id: UUID
    transaction_id: UUID
    transaction_date: date
    payee: str | None
    amount_minor: int
    confidence_percent: int
    evidence: str
    status: str


class ReviewItemResponse(BaseModel):
    row_id: UUID
    batch_id: UUID
    source_name: str
    source_account_id: UUID
    row_number: int
    raw_values: dict[str, Any]
    transaction_date: date | None
    amount_minor: int | None
    currency_code: str
    raw_payee: str | None
    normalized_payee: str | None
    status: str
    exception_type: str | None
    validation_error: str | None
    automation_kind: str | None = None
    proposed_category_id: UUID | None = None
    proposed_category_name: str | None = None
    automation_confidence: int | None = None
    automation_evidence: str | None = None
    candidates: list[MatchCandidateResponse]
    created_transaction_id: UUID | None = None


class ReviewQueuePageResponse(BaseModel):
    items: list[ReviewItemResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class MatchDecisionRequest(BaseModel):
    action: str = Field(pattern="^(confirm|reject|defer)$")
    note: str | None = Field(default=None, max_length=2000)


class CreateImportedTransactionRequest(BaseModel):
    category_id: UUID | None = None
    remember_rule: bool = False


class BulkCreateImportedTransactionsRequest(BaseModel):
    row_ids: list[UUID] = Field(min_length=1, max_length=100)
    category_id: UUID


class ImportCategorySuggestionRequest(BaseModel):
    row_ids: list[UUID] = Field(min_length=1, max_length=25)


class ReminderUpdateRequest(BaseModel):
    reminders_enabled: bool | None = None
    next_reminder_date: date | None = None
    reminder_interval_days: int | None = Field(default=None, ge=1, le=3650)


class ReconciliationExceptionResponse(BaseModel):
    exception_id: UUID
    exception_type: str
    related_type: str | None
    related_id: str | None
    event_date: date | None
    amount_minor: int | None
    currency_code: str | None
    detail: str
    status: str


class DocumentCreateRequest(BaseModel):
    kind: str = Field(pattern="^(receipt|invoice|statement|general)$")
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=120)
    data_base64: str = Field(min_length=1, max_length=20_500_000)
    account_id: UUID | None = None
    document_date: date | None = None
    amount_minor: int | None = Field(default=None, ge=0)
    currency_code: str | None = Field(default=None, pattern="^(USD|CAD|MXN)$")
    payee: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class DocumentUpdateRequest(BaseModel):
    kind: str | None = Field(default=None, pattern="^(receipt|invoice|statement|general)$")
    account_id: UUID | None = None
    document_date: date | None = None
    amount_minor: int | None = Field(default=None, ge=0)
    currency_code: str | None = Field(default=None, pattern="^(USD|CAD|MXN)$")
    payee: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class DocumentResponse(BaseModel):
    document_id: UUID
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    status: str
    account_id: UUID | None
    account_name: str | None
    document_date: date | None
    amount_minor: int | None
    currency_code: str | None
    payee: str | None
    notes: str | None
    has_thumbnail: bool
    linked_transaction_id: UUID | None
    created_at: str
    updated_at: str


class DocumentMatchResponse(BaseModel):
    match_id: UUID
    transaction_id: UUID
    transaction_date: date
    account_name: str
    payee: str | None
    amount_minor: int
    currency_code: str
    method: str
    confidence_percent: int
    evidence: str
    status: str
    reviewed_at: str | None


class DocumentExtractionResponse(BaseModel):
    extraction_id: UUID
    provider: str
    model_version: str
    status: str
    suggestions: dict[str, Any] | None
    confidence_percent: int | None
    failure_detail: str | None
    user_disposition: str
    created_at: str
    completed_at: str | None


class DocumentDetailResponse(BaseModel):
    document: DocumentResponse
    matches: list[DocumentMatchResponse]
    extractions: list[DocumentExtractionResponse]


class DocumentMatchCreateRequest(BaseModel):
    transaction_id: UUID
    note: str | None = Field(default=None, max_length=1000)


class DocumentMatchDecisionRequest(BaseModel):
    action: Literal["confirm", "reject", "defer"]
    note: str | None = Field(default=None, max_length=1000)


class ExtractionDecisionRequest(BaseModel):
    action: Literal["accept", "reject"]


class ReportTotalsResponse(BaseModel):
    spending_minor: int
    income_minor: int
    refunds_minor: int
    debt_payments_minor: int
    investment_activity_minor: int
    net_cash_flow_minor: int


class ReportCountsResponse(BaseModel):
    included: int
    pending: int
    uncategorized: int
    transfers_excluded: int
    reversals_excluded: int


class ReportBreakdownResponse(BaseModel):
    id: str
    name: str
    amount_minor: int


class ReportTransactionResponse(BaseModel):
    transaction_id: UUID
    transaction_date: date
    account_id: UUID
    account_name: str
    ownership_scope: str
    payee: str | None
    merchant_id: UUID | None
    merchant_name: str | None
    amount_minor: int
    report_amount_minor: int
    currency_code: str
    status: str
    activity_type: str
    classification: str
    categories: list[dict[str, Any]]


class MerchantProfileResponse(BaseModel):
    merchant: MerchantProfileSummaryResponse
    rule_version: str
    date_from: date
    date_to: date
    currency_code: str
    totals: ReportTotalsResponse
    transaction_count: int
    purchase_count: int
    refund_count: int
    average_purchase_minor: int
    first_transaction_date: date | None
    last_transaction_date: date | None
    monthly_spending: list[dict[str, Any]]
    by_category: list[ReportBreakdownResponse]
    by_account: list[ReportBreakdownResponse]
    transactions: list[ReportTransactionResponse]
    warnings: list[str]


class SpendingReportResponse(BaseModel):
    rule_version: str
    filters: dict[str, Any]
    prior_period: dict[str, Any]
    totals: ReportTotalsResponse
    counts: ReportCountsResponse
    transactions: list[ReportTransactionResponse]
    by_category: list[ReportBreakdownResponse]
    by_merchant: list[ReportBreakdownResponse]
    by_account: list[ReportBreakdownResponse]
    monthly_spending: list[dict[str, Any]]
    signals: dict[str, Any]
    semantics: list[str]


class ReportPresetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    report_type: str = Field(default="spending", pattern="^(spending|category|merchant|account|cash_flow|debt|net_worth)$")
    filters: dict[str, Any]


class ReportPresetResponse(BaseModel):
    preset_id: UUID
    name: str
    report_type: str
    filters: dict[str, Any]
    created_at: str
    updated_at: str
