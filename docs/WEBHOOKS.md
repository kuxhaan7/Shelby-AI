# Incoming webhooks

Shelby can react to events from outside the conversation. Register a webhook
bound to one of Shelby's saved skills, and any external service that can send
an HTTP POST (GitHub, a cron host, a form backend, a file-watcher, anything)
can trigger that skill.

## The fastest way: ask Shelby

> **You:** create a webhook called `new-dataset` that runs my `inspect_csv`
> skill when triggered
> **Shelby:** Webhook 'new-dataset' created, wired to skill 'inspect_csv'.
> Trigger it with POST /webhooks/new-dataset on the deployed URL, header
> 'X-Shelby-Secret: <secret>'. Save this secret now, it will not be shown
> again.

The target skill has to exist first (`learn_skill` if it doesn't). Shelby
calls `create_webhook`, and the secret is generated automatically unless you
give it one. Ask it to `list_webhooks` to see what's registered, or "delete
the new-dataset webhook" to remove one.

## Triggering a webhook

```
POST /webhooks/<name>
X-Shelby-Secret: <the secret you were given>
Content-Type: application/json

{ "any": "json body" }
```

The body is parsed as JSON and passed straight to the skill's `run(**kwargs)`
as keyword arguments, so a skill written as `def run(path=None, source=None)`
receives `path` and `source` directly from the webhook payload. If the
sender can't set custom headers (some webhook providers can't), send the
secret as a query parameter instead: `POST /webhooks/<name>?secret=...`.

The endpoint always responds immediately (`{"status": "accepted"}`) and runs
the skill in the background, so it won't time out even if the skill takes a
while.

## Managing webhooks over HTTP

```
GET    /webhooks              # list registered webhooks (no secrets returned)
POST   /webhooks               # {name, skill_name, secret?} -> create one
DELETE /webhooks/{name}        # remove one
```

## Getting notified when a webhook fires

Set `TELEGRAM_NOTIFY_CHAT_ID` to have Shelby message you on Telegram every
time a webhook runs, with the skill's result. To find your chat id, message
the bot `/id` and it replies with the number to use.

```
TELEGRAM_NOTIFY_CHAT_ID=123456789
```

Without this variable set, webhooks still run normally, you just won't get a
push notification, check `/webhooks` or the server logs instead.

## Security

- Every webhook requires a secret, generated automatically if you don't
  supply one, and compared with a constant-time check.
- Payloads over 1MB are rejected before the skill ever runs.
- A malformed (non-JSON) body doesn't crash the trigger; it's wrapped as
  `{"raw": "<the raw text>"}` and handed to the skill.
- Webhook secrets are never returned by `GET /webhooks`, `list_webhooks`, or
  the health endpoint. They're shown exactly once, at creation time.
