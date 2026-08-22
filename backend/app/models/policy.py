"""
================================================================================
FILE: app/models/policy.py
MODULE: Module 1 - Data Contracts
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Defines schemas for deterministic policy checks, evaluation results, and suggestions.
================================================================================
"""
from typing import List, Optional
from pydantic import BaseModel
from app.models.enums import PolicyStatus


class PolicyCheckResult(BaseModel):
    """Individual assertion check in policy evaluation."""
    check_name: str
    passed: bool
    details: str


class PolicyEvaluation(BaseModel):
    """Complete policy evaluation verdict with actionable suggestions."""
    proposal_id: str
    status: PolicyStatus
    allowed_execution: bool
    summary_reason: str
    checks: List[PolicyCheckResult]
    actionable_suggestion: Optional[str] = None
