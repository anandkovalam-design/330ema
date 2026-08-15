# Aadithya QuantLab

Aadithya QuantLab is a structured R&D repository for intraday index research, beginning with NIFTY market-state taxonomy and expanding into feature engineering, experiment design, validation, and implementation planning.

## Repository Map

- `research/papers/` - version-controlled research papers and appendices.
- `research/mathematical_specifications/` - formal definitions, notation, and derivations.
- `research/knowledge_base/` - reusable notes, assumptions, references, and decisions.
- `research/data_schema/` - canonical data contracts and dataset documentation.
- `research/architecture/` - system design notes for research and production integration.
- `research/implementation_roadmap/` - staged delivery plans and acceptance criteria.
- `features/library/` - feature specifications, lineage, and implementation status.
- `experiments/library/` - experiment protocols, run manifests, and outcomes.
- `src/aadithya_quantlab/` - Python research modules.
- `validation/` - validation framework, reports, and evidence packs.
- `tests/` - automated tests for research code and validation utilities.

## Research Program

The project treats each paper as an executable research unit:

1. Define a market problem and formal vocabulary.
2. Convert concepts into mathematical specifications.
3. Register derived features in the feature library.
4. Register experiments with datasets, metrics, and falsification tests.
5. Promote validated outputs into the architecture and implementation roadmap.

Paper 001 starts the program with a taxonomy for intraday NIFTY market states.

## Quick Commands

Launch the green Zerodha live-trading control dashboard:

```powershell
$env:PYTHONPATH="src"
streamlit run src/aadithya_quantlab/zerodha_live_trading/app.py
```

This dashboard is isolated in `aadithya_quantlab.zerodha_live_trading`. It
starts in PAPER mode; REAL orders require selecting REAL and separately arming
the live-order confirmation.

## Zerodha dashboard: Docker and Azure Container Apps

The container deployment is intentionally PAPER-only. `ZERODHA_CLOUD_MODE=true`
hard-blocks broker order placement even if a browser user selects REAL. Local
Windows sessions retain the existing supervised REAL-mode confirmation, but a
restarted session must reconcile recovered broker activity before it can be
armed again.

### Local Docker

Build from the repository root. Do not add credentials to the image or build
arguments.

```powershell
docker build -t zerodha-dashboard:local .
docker volume create zerodha-dashboard-data
docker run --rm -p 8501:8501 `
  --name zerodha-dashboard `
  --mount source=zerodha-dashboard-data,target=/mnt/zerodha `
  --env ZERODHA_CLOUD_MODE=true `
  --env ZERODHA_API_KEY=$env:ZERODHA_API_KEY `
  --env ZERODHA_API_SECRET=$env:ZERODHA_API_SECRET `
  --env ZERODHA_OWNER_USERNAME=$env:ZERODHA_OWNER_USERNAME `
  --env ZERODHA_OWNER_PASSWORD=$env:ZERODHA_OWNER_PASSWORD `
  zerodha-dashboard:local
```

Open `http://localhost:8501`. The named Docker volume stores today's access
token, `streamlit_dashboard_state.json`, and `quantlab.db`; removing the
container does not remove that volume. The API key, API secret, and owner
bootstrap password remain environment variables.
For a local Windows run without Docker, the dashboard still falls back to
Windows Credential Manager when those environment variables are absent.

### Azure prerequisites and image build

Install Azure CLI, Docker (only needed for the local check), and the Container
Apps extension. The following PowerShell commands use unique Azure resource
names; replace the two marked values first.

```powershell
az login
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights

$Location = "centralindia"
$ResourceGroup = "rg-zerodha-dashboard"
$ContainerEnv = "cae-zerodha-dashboard"
$ContainerApp = "ca-zerodha-dashboard"
$AcrName = "<globally-unique-acr-name>"
$StorageAccount = "<globallyuniquestorageaccount>"
$FileShare = "zerodha-dashboard"
$IdentityName = "id-zerodha-dashboard"
$KeyVaultName = "<globally-unique-key-vault-name>"
$ImageTag = "v1"

az group create --name $ResourceGroup --location $Location
az acr create --resource-group $ResourceGroup --name $AcrName --sku Basic
az acr build --registry $AcrName --image "zerodha-dashboard:$ImageTag" .
az containerapp env create --name $ContainerEnv --resource-group $ResourceGroup --location $Location
```

### Persistent Azure Files state

Create one read/write Azure Files share and register it with the Container Apps
environment. Keep the app at exactly one replica: the current JSON store is an
atomic single-writer recovery scaffold, not a distributed database.

```powershell
az storage account create --name $StorageAccount --resource-group $ResourceGroup --location $Location --sku Standard_LRS
az storage share-rm create --resource-group $ResourceGroup --storage-account $StorageAccount --name $FileShare --quota 1
$StorageKey = az storage account keys list --resource-group $ResourceGroup --account-name $StorageAccount --query "[0].value" -o tsv
az containerapp env storage set --name $ContainerEnv --resource-group $ResourceGroup `
  --storage-name zerodha-files --storage-type AzureFile --access-mode ReadWrite `
  --azure-file-account-name $StorageAccount --azure-file-account-key $StorageKey `
  --azure-file-share-name $FileShare
Remove-Variable StorageKey
```

The mounted `/mnt/zerodha` directory contains the daily access-token envelope,
risk settings, durable engine state, and `quantlab.db`. Azure Storage encrypts the share at
rest. Restrict Storage Account network access and enable backup/retention to
match your recovery requirements.

### Dashboard owner authentication and persistent history

The dashboard is closed until an OWNER account exists. On the first start only,
set `ZERODHA_OWNER_USERNAME` and `ZERODHA_OWNER_PASSWORD` in the runtime secret
store. The password must be at least 12 characters and is stored only as an
Argon2id hash in SQLite. Once the owner exists, later starts ignore these
bootstrap values; remove the plaintext bootstrap password from the runtime
environment after confirming the first login.

Dashboard sessions are server-side records backed by `quantlab.db`, with a
30-minute idle timeout and 12-hour absolute timeout. Revoked sessions are denied
on the next Streamlit rerun. The Security page labels browser records as
sessions because they do not prove a unique physical device. OWNER can inspect
and revoke sessions, review audit events, change the owner password, and change
the calendar's strong-profit/strong-loss thresholds. Changing the owner
password keeps the current session and revokes all other owner sessions.

OWNER can also create and disable VIEWER accounts from the Security page.
VIEWER credentials are stored as Argon2id hashes and provide read-only access to
Positions, Trade history, and the P&L calendar. Viewers cannot access API login,
trading controls, Security, or Settings. Disabling a viewer immediately revokes
all of that viewer's active sessions.

`quantlab.db` is stored at `/mnt/zerodha/quantlab.db` in the container and
`outputs/zerodha/quantlab.db` locally (or under `ZERODHA_DATA_DIR`). It contains
users, hashed passwords, sessions, security events, idempotent trade history,
daily P&L, and owner settings. Back it up from a stopped container, or use
SQLite's online backup tooling while the app is running; copy the database plus
its `-wal` and `-shm` files together if taking a filesystem-level live copy.
Protect backups as security-sensitive data.

The P&L calendar supports month/year, PAPER/REAL, and underlying filters. Its
default strong colors begin at +₹3,000 and -₹3,000 and are owner-configurable.
Selecting a date shows NIFTY/SENSEX totals and each completed trade. Approximate
location is derived from network IP metadata, not GPS, and may be unavailable or
incorrect. Forwarded IP headers are ignored unless
`ZERODHA_TRUST_PROXY_HEADERS=true`; enable that only behind a trusted proxy that
overwrites client-supplied forwarding headers. No external geolocation provider
is enabled by default, so country/region/city gracefully display as unavailable.

For a local Docker verification, set the four required secrets in the current
PowerShell process, run the build/run commands above, sign in, create only PAPER
activity, restart the container against the same named volume, and confirm that
Trade history, the P&L calendar, and Security sessions persist. Never put these
values in the Dockerfile, Compose files, shell history, or source control.

### API credentials in Key Vault

Create a user-assigned identity for image pulls and Key Vault reads. Enter the
Zerodha credentials and one-time owner bootstrap values only at the prompt; they are never written into this
repository. The request token and daily access token must not be stored in
source control.

```powershell
az identity create --name $IdentityName --resource-group $ResourceGroup --location $Location
$IdentityId = az identity show --name $IdentityName --resource-group $ResourceGroup --query id -o tsv
$IdentityPrincipalId = az identity show --name $IdentityName --resource-group $ResourceGroup --query principalId -o tsv
$AcrId = az acr show --name $AcrName --resource-group $ResourceGroup --query id -o tsv
az role assignment create --assignee-object-id $IdentityPrincipalId --assignee-principal-type ServicePrincipal --role AcrPull --scope $AcrId

az keyvault create --name $KeyVaultName --resource-group $ResourceGroup --location $Location --enable-rbac-authorization true
$VaultId = az keyvault show --name $KeyVaultName --resource-group $ResourceGroup --query id -o tsv
az role assignment create --assignee-object-id $IdentityPrincipalId --assignee-principal-type ServicePrincipal --role "Key Vault Secrets User" --scope $VaultId
$SignedInObjectId = az ad signed-in-user show --query id -o tsv
az role assignment create --assignee-object-id $SignedInObjectId --role "Key Vault Secrets Officer" --scope $VaultId

$ZerodhaApiKey = Read-Host "ZERODHA_API_KEY"
$ZerodhaApiSecret = Read-Host "ZERODHA_API_SECRET"
az keyvault secret set --vault-name $KeyVaultName --name zerodha-api-key --value $ZerodhaApiKey --output none
az keyvault secret set --vault-name $KeyVaultName --name zerodha-api-secret --value $ZerodhaApiSecret --output none
$OwnerUsername = Read-Host "ZERODHA_OWNER_USERNAME"
$OwnerPassword = Read-Host "ZERODHA_OWNER_PASSWORD" -AsSecureString
$OwnerPasswordPlain = [System.Net.NetworkCredential]::new('', $OwnerPassword).Password
az keyvault secret set --vault-name $KeyVaultName --name zerodha-owner-username --value $OwnerUsername --output none
az keyvault secret set --vault-name $KeyVaultName --name zerodha-owner-password --value $OwnerPasswordPlain --output none
Remove-Variable ZerodhaApiKey, ZerodhaApiSecret, OwnerUsername, OwnerPassword, OwnerPasswordPlain
```

### Deploy the Container App

The checked-in template starts with internal ingress and PAPER-only cloud mode.
Render its placeholders to a temporary file, then create the app:

```powershell
$ManagedEnvironmentId = az containerapp env show --name $ContainerEnv --resource-group $ResourceGroup --query id -o tsv
$AcrLoginServer = az acr show --name $AcrName --resource-group $ResourceGroup --query loginServer -o tsv
$Template = Get-Content -Raw deploy/azure/containerapp.template.yaml
$Template = $Template.Replace("<MANAGED_ENVIRONMENT_RESOURCE_ID>", $ManagedEnvironmentId)
$Template = $Template.Replace("<USER_ASSIGNED_IDENTITY_RESOURCE_ID>", $IdentityId)
$Template = $Template.Replace("<ACR_LOGIN_SERVER>", $AcrLoginServer)
$Template = $Template.Replace("<IMAGE_TAG>", $ImageTag)
$Template = $Template.Replace("<KEY_VAULT_NAME>", $KeyVaultName)
$DeployFile = Join-Path $env:TEMP "zerodha-containerapp.yaml"
Set-Content -Path $DeployFile -Value $Template
az containerapp create --name $ContainerApp --resource-group $ResourceGroup --yaml $DeployFile
Remove-Item $DeployFile
```

Keep ingress internal, or configure Microsoft Entra authentication before
changing ingress to external. This dashboard exposes trading controls and must
never be anonymously reachable. Follow the official Container Apps Easy Auth
flow, set unauthenticated access to `RedirectToLoginPage` (or `Return401`), and
only then run:

```powershell
az containerapp ingress update --name $ContainerApp --resource-group $ResourceGroup --type external --target-port 8501
```

Set the Zerodha app redirect URL to the authenticated Container App URL. Each
trading day, use the dashboard's Zerodha login flow and paste the short-lived
request token. The resulting access token is written with restricted file
permissions to the mounted share and is accepted only for the current IST date.

### Restart and recovery behavior

- PAPER remains the default after every browser/server restart.
- Open trade, last signal, trades taken, realized P&L, stop/trailing values, and
  broker order IDs are saved atomically outside `st.session_state`.
- Recovered REAL broker activity disables the affected engine and requires the
  read-only **Reconcile recovered state with Zerodha** action.
- Reconciliation reads `orders()` and `positions()`; it never places, modifies,
  or cancels an order.
- Cloud mode cannot place REAL orders. Moving to autonomous REAL execution needs
  a separate design review, distributed transactional storage, fill-aware order
  lifecycle handling, authenticated operator controls, and production testing.

Azure references: [storage mounts](https://learn.microsoft.com/azure/container-apps/storage-mounts),
[Key Vault-backed Container Apps secrets](https://learn.microsoft.com/azure/container-apps/manage-secrets),
and [Container Apps authentication](https://learn.microsoft.com/azure/container-apps/authentication).

Run daily Zerodha login setup once per trading day:

```powershell
py -m aadithya_quantlab.trading.zerodha_daily_login
```

Run EXP-001 on a canonical intraday CSV:

```powershell
py -m aadithya_quantlab.experiments.run_exp001 --input data/raw/nifty_intraday.csv --output validation/reports/EXP-001_real_nifty_report.md --labeled-output validation/reports/EXP-001_real_nifty_labeled.csv
```

Record a Zerodha-shaped paper order:

```powershell
py -m aadithya_quantlab.trading.run_paper_order --symbol NIFTY26JUL24000CE --side BUY --qty 65
```

Paper-order commands write to a local ledger only; they do not place live broker orders. NIFTY paper orders are currently locked to quantity `65`.

## Groww Cloud Trading Engine (Safe Default)

This repository now includes a separate non-Streamlit trading engine for cloud
execution:

- `groww_cloud_main.py` - cloud entrypoint for scheduled runs.
- `src/groww_engine/config.py` - central mode/risk/cost configuration.
- `src/groww_engine/engine.py` - orchestration loop and safety checks.
- `src/groww_engine/paper_broker.py` - persistent simulated execution only.
- `src/groww_engine/live_broker.py` - isolated real-order API surface.
- `src/groww_engine/groww_client.py` - market-data adapter.

The Zerodha Streamlit dashboard remains separate and does not need to stay open
for the engine to run.

### 1. Install dependencies

```powershell
py -m pip install -r requirements.txt
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and set values. Keep real credentials only in
environment variables or Groww Cloud secrets:

- `GROWW_API_KEY`
- `GROWW_API_SECRET`
- `GROWW_ACCESS_TOKEN`

Do not commit credentials, tokens, or account secrets to Git.

### 3. Run locally in PAPER mode (default)

```powershell
set TRADING_MODE=PAPER
set ENABLE_LIVE_ORDERS=false
set I_UNDERSTAND_LIVE_ORDER_RISK=NO
py groww_cloud_main.py --max-cycles 3
```

### 4. Verify no real orders are sent

- In PAPER mode, the engine writes only to local storage:
	- `outputs/groww/paper_trades.db`
	- `outputs/groww/paper_trades.csv`
- No `create_order`, `modify_order`, or `cancel_order` calls are made.
- Run safety tests:

```powershell
py -m pytest tests/test_groww_engine_safety.py -q
```

### 5. Upload trading engine code to Groww Cloud

Upload this repository (or engine subset) with at least:

- `groww_cloud_main.py`
- `src/groww_engine/`
- `src/quantlab/strategy_ema3_30_staged.py`
- `requirements.txt`

### 6. Configure Groww Cloud schedule

Set scheduler frequency to your desired poll cadence (for example every
1-5 minutes), then let `groww_cloud_main.py` run the cycle logic.

### 7. Daily Groww authentication/approval

Groww access tokens expire daily at **06:00 AM**. Refresh token/approval before
market hours and update `GROWW_ACCESS_TOKEN` in secret storage.

### 8. Review logs and paper-trade results

- Runtime logs: `outputs/groww/logs/engine.log`
- Paper trade DB: `outputs/groww/paper_trades.db`
- Paper trade CSV: `outputs/groww/paper_trades.csv`

### 9. Emergency kill switch

Set `EMERGENCY_KILL_SWITCH=true` to halt new entries immediately.

### 10. LIVE mode warning

LIVE mode can cause real financial loss.

LIVE is enabled only if all 3 checks pass simultaneously:

1. `TRADING_MODE=LIVE`
2. `ENABLE_LIVE_ORDERS=true`
3. `I_UNDERSTAND_LIVE_ORDER_RISK=YES`

If any one is missing, the engine automatically falls back to PAPER mode.
