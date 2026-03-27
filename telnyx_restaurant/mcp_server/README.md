# MCP server (restaurant tools)

Implement your **Model Context Protocol** server here (Python `mcp` SDK or Node `@modelcontextprotocol/sdk`) following [Telnyx Voice AI + MCP](https://developers.telnyx.com/) integration requirements.

## Suggested tools

| Tool | Backend call |
|------|----------------|
| `search_availability` | `GET /api/availability?...` (to be added on FastAPI app) |
| `create_reservation` | `POST /api/reservations` (body may include `preorder: [{menu_item_id, quantity}]`, `source_channel: "voice"`) |
| `list_menu_items` | `GET /api/reservations/menu/items` (prices for pre-order tool UX) |
| `get_reservation` | **`GET /api/reservations/lookup?guest_name=…&phone=…`** or the same with **`guest_phone=…`** instead of `phone` (Telnyx often names the param `guest_phone`). Fallback: `GET /api/reservations/by-code/{code}` (real HNK code in path, not `{{code}}`) |
| `find_reservation_by_phone` | `lookup` as above. Legacy: `GET /api/reservations/lookup-by-phone?phone=…` or `?guest_phone=…` |
| `update_reservation_status` | **`PATCH /api/reservations/by-code/{code}/status`** with body `{"status":"cancelled"}` **or** query **`?cancel=1`** (no JSON). Avoid `PATCH …/{numeric_id}/status` unless the tool binds a real id from the API response (never literal `{{reservation_id}}` in the path). |
| `modify_reservation` | `PATCH /api/reservations/{id}` or `PATCH /api/reservations/by-code/{code}` — body: any of `party_size`, `starts_at`, `preorder` / `items`, `guest_name`, `guest_phone`, `special_requests` (omit unchanged fields). Blocked when status is `cancelled`. |
| `cancel_reservation` | `DELETE /api/reservations/{id}` |

Keep business rules in the **REST API**; MCP should validate inputs and forward errors as structured tool results.

### Dynamic webhook variables (Telnyx assistant templates)

`POST /webhooks/telnyx/variables` enriches responses from the DB when `caller_number` / `from` matches `guest_phone`. Useful keys for demos:

- `reservation_preorder_summary`, `reservation_food_total_display`, `reservation_has_preorder`, `reservation_source_channel`
- `demo_reminder_note` — explains that **each new reservation** schedules an **outbound Telnyx reminder ~5 seconds** later when `TELNYX_API_KEY`, `TELNYX_CONNECTION_ID`, and `TELNYX_FROM_NUMBER` are set on the web service.

Wire the same assistant / connection used for that outbound leg so **inbound** dynamic variables and **MCP tools** stay aligned with the booking record.

## Deployment

- **Same container:** run MCP as a subprocess or second process if Telnyx supports it.
- **Separate Render service:** expose MCP transport URL on another subdomain if required.

Document the final URL or command in the root **README.md** for reviewers.
