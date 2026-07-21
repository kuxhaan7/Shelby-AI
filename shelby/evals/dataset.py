"""Evaluation dataset for Shelby — question / ground-truth pairs."""

EVAL_DATASET = [
    {
        "question": "What is Shelby?",
        "context": (
            "Shelby is an autonomous AI assistant built on the Claude API. "
            "It features persistent memory, recurring heartbeats, tool use, "
            "and conversational interfaces via Telegram and WhatsApp."
        ),
        "ground_truth": (
            "Shelby is an autonomous Claude-powered AI agent with persistent memory, "
            "heartbeat loops, tool use, and Telegram/WhatsApp integrations."
        ),
    },
    {
        "question": "How does Shelby remember things across sessions?",
        "context": (
            "Shelby uses a persistent memory layer that separates short-term "
            "conversation context from long-term semantic storage backed by ChromaDB."
        ),
        "ground_truth": (
            "Shelby stores long-term knowledge in a ChromaDB vector store and keeps "
            "short-term context in the active conversation buffer."
        ),
    },
    {
        "question": "What tools can Shelby use?",
        "context": (
            "Shelby's tool suite includes: get_current_time, calculate, "
            "search_knowledge_base, and remember. Tool definitions follow the "
            "Anthropic function-calling schema."
        ),
        "ground_truth": (
            "Shelby can call tools including get_current_time, calculate, "
            "search_knowledge_base, and remember using Claude's function-calling API."
        ),
    },
    {
        "question": "What API does Shelby expose?",
        "context": (
            "Shelby runs a FastAPI server with endpoints: POST /chat, "
            "POST /chat/stream, POST /rag/ingest, POST /rag/search, GET /health."
        ),
        "ground_truth": (
            "Shelby exposes a FastAPI REST API with chat (including streaming), "
            "RAG ingest and search, and a health endpoint."
        ),
    },
]
