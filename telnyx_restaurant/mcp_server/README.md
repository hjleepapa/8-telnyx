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
| **`update_reservation`** (often bound to `…/status`) | **`PATCH …/by-code/{code}/status`** or **`PATCH …/{id}/status`**: use **`{"status":"cancelled"}`** or **`?cancel=1`** for status-only; the API **also applies** `party_size`, `starts_at`, `preorder`, guest fields **in the same JSON body** when tools cannot be pointed at `/amend` (prefer `/amend` + `confirmation_code` when possible). |
| **`modify_reservation`** / **`update_preorder`** (party, time, pre-order, guest) | Prefer **`PATCH /api/reservations/{id}/amend`** (same path pattern as **`…/{id}/status`** — swap the last segment). Alternatives: **`PATCH /api/reservations/amend/{id}`**, **`PATCH /api/reservations/amend?id={id}`**, or raw **`PATCH /api/reservations/amend`** only if the body has truthy **`confirmation_code`** / **`id`** (Telnyx null placeholders break this). Body: `preorder`, `party_size`, `starts_at`, guest fields, etc. Same JSON works on **`PATCH /{id}`** and **`PATCH /by-code/{code}`**. |
| `cancel_reservation` | Prefer **`PATCH …/status?cancel=1`** or body **`{"status":"cancelled"}`** on **`…/{id}/status`**. `DELETE /api/reservations/{id}` also exists. |

**Telnyx HTTP tool checklist:** If you define **two** tools — e.g. `update_reservation` → **`…/{id}/status`** and `update_preorder` → **`…/amend`** — the assistant often picks **`update_reservation`** for every “change my booking” request because the name sounds general. See **Choosing status vs preorder tools** below.

### Choosing status vs preorder tools (Telnyx Voice AI)

The model does **not** “know” your API; it follows **tool names, descriptions, and the assistant system prompt**.

1. **Rename for intent (recommended)**  
   - Tool A: **`set_reservation_status`** or **`update_reservation_status_only`** — not `update_reservation`.  
   - Tool B: **`update_reservation_details`** or **`change_preorder_or_booking_details`** — not only `update_preorder` if guests also change party size or time.

2. **Descriptions the model actually reads**  
   - **Status tool:** *Use ONLY when the guest changes lifecycle state: pending, confirmed, seated, completed, or cancelled. Do NOT use for food preorder, menu items, party size, reservation time, or special requests.*  
   - **Details / preorder tool:** *Use when the guest adds or changes food preorder, party size, arrival time, special requests, or contact fields. Always use `reservation_id` from GET `/lookup` in the URL path (`…/{reservation_id}/amend`).*

3. **One-tool option (less routing error)**  
   The backend **`PATCH …/{id}/status`** already accepts a full **`ReservationUpdate`** body: you can declare the same optional fields as **`modify_reservation`** (`preorder`, `party_size`, `starts_at`, …) on the status tool’s schema. Then a single “patch reservation” call can change status **or** preorder **or** both. If you keep two tools, the status tool’s schema should list **only** `status` so the model is not encouraged to mix concerns without documentation.

4. **Assistant instructions (copy-paste)**  
   *If the guest wants to change what they are eating or their preorder, use **update_reservation_details** (PATCH `…/{reservation_id}/amend`). If they only want to cancel or change seating state (confirmed/seated/etc.), use **set_reservation_status** (PATCH `…/{reservation_id}/status`).*

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
