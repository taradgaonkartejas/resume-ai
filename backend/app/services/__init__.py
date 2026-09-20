"""Service layer: business rules, orchestration and transaction boundaries.

Services raise domain exceptions (never HTTPException) so they stay callable
from tests, the seed script and LangGraph nodes.
"""
