"""LangGraph wiring: hierarchical supervisor -> ReAct researcher -> analyst -> verifier (reflection loop) -> writer.

    recall_memory -> supervisor -(in scope)-> researcher -> analyst -> verifier -+-> writer -> END
                              \\-(out of scope)-> discovery -> END              +-> researcher (retry gaps)
                                                                               +-> discovery -> END
"""
from __future__ import annotations

from functools import lru_cache

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .. import db
from ..config import settings
from . import nodes
from .state import VerityState


def build_graph(checkpointer=None):
    g = StateGraph(VerityState)
    g.add_node("recall_memory", nodes.recall_memory)
    g.add_node("supervisor", nodes.supervisor)
    g.add_node("researcher", nodes.researcher)
    g.add_node("analyst", nodes.analyst)
    g.add_node("verifier", nodes.verifier)
    g.add_node("writer", nodes.writer)
    g.add_node("discovery", nodes.discovery)

    g.add_edge(START, "recall_memory")
    g.add_edge("recall_memory", "supervisor")
    g.add_conditional_edges("supervisor", nodes.route_after_supervisor,
                            {"researcher": "researcher", "discovery": "discovery"})
    g.add_edge("researcher", "analyst")
    g.add_edge("analyst", "verifier")
    g.add_conditional_edges("verifier", nodes.route_after_verify,
                            {"research_more": "researcher", "write": "writer", "discovery": "discovery"})
    g.add_edge("writer", END)
    g.add_edge("discovery", END)
    return g.compile(checkpointer=checkpointer or default_checkpointer())


def default_checkpointer():
    """MongoDB checkpointer in prod (state survives restarts, inspectable per thread_id = run_id);
    in-memory for mongomock/offline runs."""
    if db.is_mock() or settings.checkpointer == "memory":
        return InMemorySaver()
    from langgraph.checkpoint.mongodb import MongoDBSaver

    # TTL keeps checkpoint storage bounded (same 30-day retention as the runs collection).
    return MongoDBSaver(db.client(), db_name=settings.mongodb_db, ttl=30 * 86400)


@lru_cache(maxsize=1)
def graph():
    return build_graph()
