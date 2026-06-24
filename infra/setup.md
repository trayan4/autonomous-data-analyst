# Azure setup

This project runs against Azure resources provisioned once through the Portal. There is
no IaC to run — create the resources below, then put their connection details in `.env`
(template: `.env.example`). The application reads everything from environment variables
at runtime, so the code is fully decoupled from how the infrastructure is created.

> **The names below are the actual resources I've used in this project**, as they appear in the
> Azure Portal — not placeholders. If you are reproducing the project from scratch,
> substitute your own globally-unique names (SQL server and storage account names must
> be globally unique) and update `.env` to match.

## Resources at a glance

All resources live in the resource group **`Trayans_AI_Resource_group`**.

| Resource | Name (actual) | Type / region | `.env` keys |
|---|---|---|---|
| SQL server | `ada-sql-td` | SQL server, West US 2 | `AZURE_SQL_SERVER` |
| SQL database | `ada` | SQL database, West US 2 | `AZURE_SQL_DATABASE` |
| AI Foundry resource | `Trayans-Foundry` | Foundry, East US | `AZURE_OPENAI_BASE_URL`, `AZURE_OPENAI_API_KEY` |
| AI Foundry project | `proj-default` | Foundry project, East US | — |
| — `gpt-4o-mini` deployment | cheap tier (default) | Global Standard | `AZURE_OPENAI_DEPLOYMENT` |
| — `gpt-4.1` deployment | strong tier | Global Standard | `AZURE_OPENAI_DEPLOYMENT_STRONG` |
| Application Insights | `Trayans-Foundry-App-Insights` | App Insights (workspace-based), East US | `APPLICATIONINSIGHTS_CONNECTION_STRING` |
| Log Analytics workspace | `Trayans-Log-Analytics` | Log Analytics, East US | — (backs App Insights) |
| Storage account | `trayansaistorageaccount` | Storage, East US | — (Foundry dependency) |

SQL is in West US 2 and Foundry/observability in East US — they need not share a region;
cross-region adds only negligible dev latency.

> The Foundry project auto-created three of the resources above — the storage account
> (`trayansaistorageaccount`), the Application Insights (`Trayans-Foundry-App-Insights`),
> and the Log Analytics workspace (`Trayans-Log-Analytics`), plus a smart-detection
> action group. The app reuses that same App Insights for its own traces, so there is no
> separate observability resource to create.

## 1. Resource group

- **Portal:** Resource groups → Create → name `Trayans_AI_Resource_group`.
- **CLI:** `az group create -n Trayans_AI_Resource_group -l westus2`

## 2. Azure SQL — server `ada-sql-td`, database `ada`

- **Portal:** Create a resource → *SQL Database*.
  - Server: `ada-sql-td` (globally unique), **SQL authentication**, admin login `adaadmin` + a strong password.
  - Database name: `ada`.
  - Compute + storage → **Serverless**, General Purpose, Gen5, 1 vCore max, min 0.5 vCore, **auto-pause after 60 min** (idle cost ≈ zero).
  - Networking → add **your client IP**, and set **Allow Azure services and resources to access this server = Yes**.
- **CLI equivalent:**
  ```sh
  az sql server create -n ada-sql-td -g Trayans_AI_Resource_group -l westus2 -u adaadmin -p '<password>'
  az sql db create -g Trayans_AI_Resource_group -s ada-sql-td -n ada \
    --edition GeneralPurpose --compute-model Serverless --family Gen5 \
    --capacity 1 --min-capacity 0.5 --auto-pause-delay 60 --backup-storage-redundancy Local
  az sql server firewall-rule create -g Trayans_AI_Resource_group -s ada-sql-td -n my-ip \
    --start-ip-address "$(curl -s ifconfig.me)" --end-ip-address "$(curl -s ifconfig.me)"
  az sql server firewall-rule create -g Trayans_AI_Resource_group -s ada-sql-td -n azure-services \
    --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0
  ```

→ `.env`: `AZURE_SQL_SERVER=ada-sql-td.database.windows.net`, `AZURE_SQL_DATABASE=ada`,
`AZURE_SQL_USER=adaadmin`, `AZURE_SQL_PASSWORD=<password>`.

You also need the **ODBC Driver 18 for SQL Server** installed locally for `pyodbc`.

## 3. Azure AI Foundry — resource `Trayans-Foundry`, project `proj-default`

- **Portal:** Azure AI Foundry → create the resource `Trayans-Foundry` and project `proj-default` (East US).
  - Under *Models + endpoints*, deploy **two** models: `gpt-4o-mini` and `gpt-4.1`, both **Global Standard**.
  - The OpenAI-compatible **v1 base URL** is on the resource Overview / Endpoints page:
    `https://trayans-foundry.services.ai.azure.com/openai/v1/`
  - Get an **API key** from *Keys and Endpoint*.

→ `.env`: `AZURE_OPENAI_BASE_URL=https://trayans-foundry.services.ai.azure.com/openai/v1/`,
`AZURE_OPENAI_API_KEY=<key>`, `AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini`,
`AZURE_OPENAI_DEPLOYMENT_STRONG=gpt-4.1`.

The deployment **names** in `.env` must match what you named the deployments in Foundry.

## 4. Observability — App Insights `Trayans-Foundry-App-Insights` (only for `ADA_TRACE=azure`)

Foundry already created the **workspace-based** Application Insights
`Trayans-Foundry-App-Insights`, backed by the Log Analytics workspace
`Trayans-Log-Analytics`. The app reuses it for its spans.

- **Portal:** `Trayans-Foundry-App-Insights` → Overview → **Connection String** (the full
  string, not just the instrumentation key).

→ `.env`: `ADA_TRACE=azure`, `APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=...;IngestionEndpoint=https://...`

It must be **workspace-based** (classic App Insights is retired). Full enable/verify steps
and the KQL queries are in `docs/observability-azure-monitor.md` and
`docs/observability-queries.md`.

## Secrets

This setup keeps connection strings and keys in `.env` (git-ignored) — fine for local
development; there is no Key Vault in this environment. For production, hold them in
**Azure Key Vault** (or app settings / a managed identity) and never in a file. The app
reads from environment variables either way, so only the *source* of the values changes,
not the code.

## Put it together

Copy the template and fill in the values from the steps above:

```sh
cp .env.example .env      # then edit .env

python -m ada.data.generate_data      # writes ./data/raw/*.csv
python -m ada.data.load_to_azuresql   # applies schema + bulk-loads into Azure SQL
ada-eval                              # should pass 5/5
ada-ask "Why did sales drop last month?"
```
