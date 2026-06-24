# Future improvements

A short list of things worth building next, with the reasoning for each.

## 1. Entra ID-based PII visibility

Today the entitlement guardrail (ADR-0005) is wired but `can_view_pii` is hardcoded
`False`. The goal is to make PII visibility depend on **who is asking**: an entitled
caller sees customer names/email/phone, everyone else gets the aggregate-only refusal.

The shape: authenticate the caller with **Entra ID**, validate their token at a trusted
boundary (the FastAPI `/ask` layer), and resolve `can_view_pii` from a **token claim**
(an App Role like `Pii.Read`) — never from anything the client sends in the request body.
The agent then enforces it, and the database connection is a least-privilege backstop.

## 2. Deploy the code to Azure App Service

Package and host the API so it's reachable rather than run locally. 

Azure **Container Apps** is also worth a look as an alternative — scale-to-zero and a
container-native model suit an agentic workload, and the app is already containerizable.

## 3. Develop a frontend (possibly using Azure Bot Service so that it's accessible via teams, etc.)

A real UI so non-technical users can ask questions and see the answer, the route taken, and
the SQL.
- The agent is capable of **token streaming**, and
  the Bot Framework / most channels don't stream token-by-token — I'd fall back to typing
  indicators and a single final message.
→ React on the existing API might be a better option.

## 4. Add a true research agent

A third agent (beyond Data Retrieval and Analysis) that handles open-ended questions by
planning a few steps, gathering evidence across the warehouse, and reasoning over the
combined result — rather than answering from a single query.

- First, I'll need to define what "research" means here. Few options:

1. multi-step querying over the existing warehouse
2. pulling in **external/web sources**

## 5. Bicep IaC for provisioning

Replace the manual `infra/setup.md` checklist with declarative, idempotent **Bicep** so the
whole dev environment (SQL server + serverless DB + firewall, Foundry/App Insights wiring,
any role assignments) stands up and tears down with a single `az deployment` command,
parameterized rather than clicked through the portal. The payoff is repeatability: a
reviewer or CI job can build the stack from scratch identically, and teardown is one command
instead of hunting resources by hand.