# Shelby-AI

An autonomous agent built on the Claude API with persistent memory, recurring heartbeats, and conversational interfaces via Telegram and WhatsApp.

**Status:** Active personal project. Source kept private for security and privacy reasons — Shelby integrates personal accounts and runs on personal infrastructure. Reach out via [LinkedIn](https://linkedin.com/in/kushaankaushik) for a walkthrough or live demo.

## What Shelby Does

Shelby is a long-running agent that:

- **Stays alive.** A heartbeat loop keeps Shelby responsive between conversations rather than spinning up fresh per request, so it carries context across days.
- **Talks naturally.** Conversational access through Telegram and WhatsApp — no terminal, no UI to learn.
- **Acts, not just answers.** Tool use lets Shelby do real work: file operations, browser automation, scheduled tasks, plugin invocation.
- **Carries memory.** A persistent memory layer separates short-term conversation context from long-term knowledge, so Shelby gets to know you over time.

## Architecture

Core components:

- **Agent loop** — orchestrates tool use, retry, and error recovery
- **Memory** — short-term conversation buffer plus long-term semantic store
- **Plugin skills** — modular capabilities (browser automation, scheduled tasks, etc.)
- **Channel adapters** — Telegram and WhatsApp messaging bridges
- **Identity layer** — per-user context isolation

## Why It's Private

Shelby integrates personal accounts (Telegram, WhatsApp), runs continuously on personal infrastructure, and has access to private data. Open-sourcing it would create real security and privacy risk. Happy to walk through the architecture and code with anyone interested — just reach out.

## Tech

Python · Claude API · Tool Use · FastAPI · Telegram Bot API · WhatsApp Business API

---

Built by [Kushaan Kaushik](https://github.com/kuxhaan7) · [Portfolio](https://kuxhaan7.github.io) · [LinkedIn](https://linkedin.com/in/kushaankaushik)
