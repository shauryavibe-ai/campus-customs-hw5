"""Campus Customs multi-agent operations team (Boss, Inventory, Accounting, Facilities,
Customer Service), fully connected, each agent on its own MCP server."""

__all__ = ["AGENT_NAMES", "DayReview", "Team", "TicketOutcome"]


def __getattr__(name: str):
    # Imported lazily so `python -m agent_team.audit verify` runs without loading the team.
    if name in __all__:
        from agent_team import team
        return getattr(team, name)
    raise AttributeError(name)
