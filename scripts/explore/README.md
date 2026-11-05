# How the Switch API was worked out

One-off scripts used while building the room, kept so the steps can be checked. They talk to the local Switch
server from `scripts/switch_stack.py` and write what they find to `logs/` (git-ignored). Nothing here is needed
to run the room; `scripts/room.py` and `scripts/switch_room.py` are what came out of it.

| Script | What it looked at |
|---|---|
| `ghcr_tags.py` | which versions of Switch's standalone compose file are published on ghcr.io |
| `switch_probe.py` | the server's OpenAPI documents, filtered to agents, rooms, templates and policies |
| `switch_explore.py` | registering the agents, their default addressing policy, the operation registry and the template schema |
| `switch_events_probe.py` | the event long-poll and which operations need an open connection (HTTP 409 without one) |
| `switch_sse_probe.py` | the Server-Sent Events stream, `connect_to_room`, `list_participants` and `read_context` over it |
| `switch_rooms_list.py` | the rooms on the server, through the dashboard API |
| `switch_post_probe.py` | what the log watcher can do without a connection: list its rooms, post, read history |
| `openapi_paths.py`, `openapi_show.py` | the paths and request and response schemas in a saved OpenAPI file |
