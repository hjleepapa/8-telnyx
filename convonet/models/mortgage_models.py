"""
Mortgage Application Models for Convonet
Database models for pre-approved mortgage application process
"""

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Text, Enum, Numeric, Integer, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone
from enum import Enum as PyEnum
import uuid

from convonet.models.base import Base


class ApplicationStatus(str, PyEnum):
    """Mortgage application status - inherits from str for SQLAlchemy compatibility"""
    DRAFT = "draft"
    FINANCIAL_REVIEW = "financial_review"
    DOCUMENT_COLLECTION = "document_collection"
    DOCUMENT_VERIFICATION = "document_verification"
    UNDER_REVIEW = "under_review"
    PRE_APPROVED = "pre_approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class DocumentType(str, PyEnum):
    """Document types for mortgage application - inherits from str for SQLAlchemy compatibility"""
    IDENTIFICATION = "identification"
    INCOME_PAYSTUB = "income_paystub"
    INCOME_W2 = "income_w2"
    INCOME_TAX_RETURN = "income_tax_return"
    INCOME_PNL = "income_pnl"
    INCOME_1099 = "income_1099"
    ASSET_BANK_STATEMENT = "asset_bank_statement"
    ASSET_INVESTMENT = "asset_investment"
    ASSET_RETIREMENT = "asset_retirement"
    DEBT_CREDIT_CARD = "debt_credit_card"
    DEBT_STUDENT_LOAN = "debt_student_loan"
    DEBT_AUTO_LOAN = "debt_auto_loan"
    DOWN_PAYMENT_SOURCE = "down_payment_source"
    DOWN_PAYMENT_GIFT_LETTER = "down_payment_gift_letter"


class DocumentStatus(str, PyEnum):
    """Document upload status - inherits from str for SQLAlchemy compatibility"""
    PENDING = "pending"
    UPLOADED = "uploaded"
    VERIFIED = "verified"
    REJECTED = "rejected"


class MortgageApplication(Base):
    """Main mortgage application table"""
    __tablename__ = "mortgage_applications"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users_anthropic.id'), nullable=False, index=True)
    
    # Application status - use native_enum=True to use PostgreSQL enum type
    status = Column(Enum(ApplicationStatus, native_enum=True, create_constraint=True), default=ApplicationStatus.DRAFT, nullable=False, index=True)
    
    # Financial Information (Step 1: Review Finances)
    credit_score = Column(Integer, nullable=True)
    credit_history_years = Column(Integer, nullable=True)
    dti_ratio = Column(Numeric(5, 2), nullable=True)  # Debt-to-income ratio (percentage)
    monthly_income = Column(Numeric(12, 2), nullable=True)
    monthly_debt = Column(Numeric(12, 2), nullable=True)
    down_payment_amount = Column(Numeric(12, 2), nullable=True)
    closing_costs_estimate = Column(Numeric(12, 2), nullable=True)
    total_savings = Column(Numeric(12, 2), nullable=True)
    
    # Loan preferences
    loan_type = Column(String(50), nullable=True)  # conventional, FHA, VA, etc.
    loan_amount = Column(Numeric(12, 2), nullable=True)
    property_value = Column(Numeric(12, 2), nullable=True)
    
    # Progress tracking
    financial_review_completed = Column(Boolean, default=False, nullable=False)
    document_collection_completed = Column(Boolean, default=False, nullable=False)
    document_verification_completed = Column(Boolean, default=False, nullable=False)
    
    # Additional metadata
    app_metadata = Column(JSON, nullable=True)  # Store additional flexible data (renamed from 'metadata' - SQLAlchemy reserved)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    documents = relationship("MortgageDocument", back_populates="application", cascade="all, delete-orphan")
    debts = relationship("MortgageDebt", back_populates="application", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<MortgageApplication(id={self.id}, user_id={self.user_id}, status={self.status})>"
    
    def get_completion_percentage(self) -> float:
        """Calculate application completion percentage"""
        total_steps = 3  # Financial review, document collection, document verification
        completed = sum([
            self.financial_review_completed,
            self.document_collection_completed,
            self.document_verification_completed
        ])
        return (completed / total_steps) * 100 if total_steps > 0 else 0


class MortgageDocument(Base):
    """Documents uploaded for mortgage application"""
    __tablename__ = "mortgage_documents"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(UUID(as_uuid=True), ForeignKey('mortgage_applications.id'), nullable=False, index=True)
    
    # Document information
    document_type = Column(Enum(DocumentType, native_enum=True, create_constraint=True), nullable=False, index=True)
    document_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=True)  # Path to stored file
    file_url = Column(String(500), nullable=True)  # URL if stored externally
    file_size = Column(Integer, nullable=True)  # Size in bytes
    mime_type = Column(String(100), nullable=True)
    
    # Document status
    status = Column(Enum(DocumentStatus, native_enum=True, create_constraint=True), default=DocumentStatus.PENDING, nullable=False, index=True)
    
    # Verification details
    verified_at = Column(DateTime(timezone=True), nullable=True)
    verified_by = Column(UUID(as_uuid=True), nullable=True)  # User ID of verifier
    verification_notes = Column(Text, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    
    # Document metadata
    doc_metadata = Column(JSON, nullable=True)  # Store document-specific metadata (renamed from 'metadata' - SQLAlchemy reserved)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    application = relationship("MortgageApplication", back_populates="documents")
    
    def __repr__(self):
        return f"<MortgageDocument(id={self.id}, type={self.document_type}, status={self.status})>"


class MortgageDebt(Base):
    """Debt information for mortgage application"""
    __tablename__ = "mortgage_debts"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(UUID(as_uuid=True), ForeignKey('mortgage_applications.id'), nullable=False, index=True)
    
    # Debt information
    debt_type = Column(String(50), nullable=False)  # credit_card, student_loan, auto_loan, mortgage, other
    creditor_name = Column(String(255), nullable=True)
    account_number = Column(String(100), nullable=True)  # Last 4 digits or masked
    monthly_payment = Column(Numeric(10, 2), nullable=False)
    outstanding_balance = Column(Numeric(12, 2), nullable=True)
    interest_rate = Column(Numeric(5, 2), nullable=True)  # Annual percentage rate
    
    # Additional details
    description = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Relationships
    application = relationship("MortgageApplication", back_populates="debts")
    
    def __repr__(self):
        return f"<MortgageDebt(id={self.id}, type={self.debt_type}, monthly_payment={self.monthly_payment})>"


class MortgageApplicationNote(Base):
    """Notes and comments on mortgage application"""
    __tablename__ = "mortgage_application_notes"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(UUID(as_uuid=True), ForeignKey('mortgage_applications.id'), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users_anthropic.id'), nullable=False)
    
    # Note content
    note_text = Column(Text, nullable=False)
    note_type = Column(String(50), nullable=True)  # system, user, agent, verification
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    
    def __repr__(self):
        return f"<MortgageApplicationNote(id={self.id}, application_id={self.application_id})>"
