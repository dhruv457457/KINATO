"""
Agent runtime (Day 5 of the rebuild plan). Each agent is a small, bounded
LangGraph reasoning loop (see runtime.py); the event bus and DB sweeper
remain the durable, cross-service orchestration layer around it (see
app/gateway/). Nothing in here trusts a model argument with money or
identity - see state.py's AgentContext and audit.py's forbidden-arg gate.
"""
