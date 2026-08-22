"""
================================================================================
FILE: app/mcp/server.py
MODULE: Module 3 - Model Context Protocol (MCP) Server
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Implements the Model Context Protocol (MCP) endpoint (`/mcp`) for Kinato.
Allows external AI agents (Claude Code, Cursor, Codex, Antigravity) to discover
merchants, trigger autonomous A2A negotiations, and create Razorpay checkouts.

EXPOSED MCP TOOLS:
  1. `kinato_get_inventory(profile_type)`: Check on-hand stock and DIR days.
  2. `kinato_negotiate_restock(profile_type, target_sku)`: Run A2A multi-agent bidding.
  3. `kinato_evaluate_policy(proposal_id)`: Check deterministic floor & budget bounds.
  4. `kinato_create_checkout(proposal_id, mode)`: Mint Razorpay Order for approved deals.
================================================================================
"""
from typing import Dict, Any, List
from app.models.enums import BusinessProfileType, ExecutionMode
from app.knowledge.inventory import inventory_repo
from app.knowledge.suppliers import supplier_repo
from app.agents.service import agent_service
from app.payments.razorpay_client import razorpay_rails
from app.db.database import get_db
import json


class KinatoMCPServer:
    """
    Model Context Protocol (MCP) Tool Suite for External AI Agents.
    """
    @classmethod
    def get_tool_definitions(cls) -> List[Dict[str, Any]]:
        """Returns standard MCP tool definitions."""
        return [
            {
                "name": "kinato_get_inventory",
                "description": "Inspect live buyer inventory, daily burn rates, and Days of Inventory Remaining (DIR).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "profile_type": {
                            "type": "string",
                            "enum": ["CLOUD_KITCHEN", "TECH_PANTRY", "RETAIL_STORE"],
                            "description": "Business vertical profile"
                        }
                    },
                    "required": ["profile_type"]
                }
            },
            {
                "name": "kinato_negotiate_restock",
                "description": "Trigger autonomous A2A multi-agent reverse bidding with competing wholesale suppliers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "profile_type": {
                            "type": "string",
                            "enum": ["CLOUD_KITCHEN", "TECH_PANTRY", "RETAIL_STORE"]
                        },
                        "target_sku": {
                            "type": "string",
                            "description": "Optional specific SKU to replenish"
                        }
                    },
                    "required": ["profile_type"]
                }
            },
            {
                "name": "kinato_create_razorpay_checkout",
                "description": "Create an immutable Razorpay Order for a verified, policy-approved proposal.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "proposal_id": {
                            "type": "string",
                            "description": "Approved proposal ID"
                        },
                        "amount_inr": {
                            "type": "number",
                            "description": "Agreed final total in INR"
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["ONE_CLICK_APPROVAL", "AUTONOMOUS_AUTOPAY"],
                            "default": "ONE_CLICK_APPROVAL"
                        }
                    },
                    "required": ["proposal_id", "amount_inr"]
                }
            }
        ]

    @classmethod
    async def call_tool(cls, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches an incoming MCP tool execution call."""
        if name == "kinato_get_inventory":
            profile_type = BusinessProfileType(arguments.get("profile_type", "CLOUD_KITCHEN"))
            ctx = inventory_repo.get_context(profile_type)
            return ctx.model_dump()

        elif name == "kinato_negotiate_restock":
            profile_type = BusinessProfileType(arguments.get("profile_type", "CLOUD_KITCHEN"))
            target_sku = arguments.get("target_sku")
            res = await agent_service.execute_negotiation(profile_type, target_sku=target_sku)
            return {
                "winning_supplier": res["winning_quote"].model_dump() if res["winning_quote"] else None,
                "final_offer": res["final_offer"].model_dump() if res["final_offer"] else None,
                "policy_evaluation": res["policy_evaluation"].model_dump() if res["policy_evaluation"] else None
            }

        elif name == "kinato_create_razorpay_checkout":
            proposal_id = arguments["proposal_id"]
            amount_inr = float(arguments["amount_inr"])
            mode = ExecutionMode(arguments.get("mode", "ONE_CLICK_APPROVAL"))

            # Query proposal to verify integrity
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,))
                row = cursor.fetchone()
                if not row:
                    return {"error": f"Proposal '{proposal_id}' not found in database."}

                res = razorpay_rails.create_order(
                    proposal_id=proposal_id,
                    amount_inr=amount_inr,
                    business_id="buyer_mcp_client",
                    supplier_id=row["winning_supplier_id"],
                    mode=mode,
                    proposal_hash=row["proposal_hash"]
                )
                return res.model_dump()

        return {"error": f"Unknown MCP tool: {name}"}


mcp_server = KinatoMCPServer()
