"""
================================================================================
FILE: app/models/enums.py
MODULE: Module 1 - Data Contracts
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Defines system-wide enumeration types for business verticals, policy statuses,
and execution modes.
================================================================================
"""
from enum import Enum


class BusinessProfileType(str, Enum):
    """The 3 selectable business verticals supported by Kinato."""
    CLOUD_KITCHEN = "CLOUD_KITCHEN"
    TECH_PANTRY = "TECH_PANTRY"
    RETAIL_STORE = "RETAIL_STORE"
    CUSTOM = "CUSTOM"


class ExecutionMode(str, Enum):
    """Transaction execution authorization modes."""
    ONE_CLICK_APPROVAL = "ONE_CLICK_APPROVAL"
    AUTONOMOUS_AUTOPAY = "AUTONOMOUS_AUTOPAY"


class PolicyStatus(str, Enum):
    """Deterministic policy gate verdict statuses."""
    PASSED = "PASSED"
    BLOCKED = "BLOCKED"
    INVALIDATED = "INVALIDATED"
