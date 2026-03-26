# MCP server (restaurant tools)

Implement your **Model Context Protocol** server here (Python `mcp` SDK or Node `@modelcontextprotocol/sdk`) following [Telnyx Voice AI + MCP](https://developers.telnyx.com/) integration requirements.

## Suggested tools

| Tool | Backend call |
|------|----------------|
| `search_availability` | `GET /api/availability?...` (to be added on FastAPI app) |
| `create_reservation` | `POST /api/reservations` |
| `get_reservation` | `GET /api/reservations/{id}` |
| `modify_reservation` | `PATCH /api/reservations/{id}` |
| `cancel_reservation` | `DELETE /api/reservations/{id}` |

Keep business rules in the **REST API**; MCP should validate inputs and forward errors as structured tool results.

## Deployment

- **Same container:** run MCP as a subprocess or second process if Telnyx supports it.
- **Separate Render service:** expose MCP transport URL on another subdomain if required.

Document the final URL or command in the root **README.md** for reviewers.
