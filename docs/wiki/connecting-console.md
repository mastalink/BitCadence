# Connecting the Console & Demo Mode

## Goal
Connect the BitCadence web Control Panel to your local gateway server using an authenticated access token, or use simulated Demo Mode to explore all features safely before running live tasks.

---

## Step-by-Step Instructions

### 1. Open the Web Console
Open your web browser and navigate to:
```text
http://127.0.0.1:18789/console
```
When first opened, the console loads in **Demo mode — simulated data**. A banner appears explaining that all displayed jobs, agents, and events are mock simulations so you can explore without executing real work.

### 2. Open Connection Settings
Click the **Settings** tab in the left-hand navigation sidebar (or click the Settings icon in the top header).

![Console Settings Connection Panel](img/02-console-settings.png)

### 3. Enter Gateway URL and Access Token
In the **Connection** section:
1. **Gateway URL:** Ensure it is set to `http://127.0.0.1:18789` (already populated by default).
2. **Agent Token:** Paste the operator access token generated during installation (`Ctrl+V`). The token begins with `mco_tok_`.
   - *Where to find your token:* Look at the server terminal window, or open `~/.mco/.env` in Notepad and copy the value of `MCO_LOCAL_TOKEN`.

### 4. Click Connect
Click the **Connect** button.
- The status indicator switches immediately from yellow (`Demo mode`) to green (`Connected`).
- The connection parameters are stored safely in your browser's `localStorage` (`bitcadence_conn`), meaning you will not have to re-enter them on subsequent visits.

### 5. Returning to Demo Mode (Optional)
If you ever want to return to sandbox exploration with mock data:
1. In **Settings → Connection**, click **Disconnect / Switch to Demo mode**.
2. The UI instantly reverts to simulated state, allowing you to click all controls safely.

---

## The CLI Equivalent

To verify connectivity and token authorization directly from your shell:

```powershell
# Set token in environment
$env:MCO_AGENT_TOKEN = (Get-Content "$HOME\.mco\.env" | Select-String "MCO_LOCAL_TOKEN").Line.Split("=")[1]
$env:MCO_GATEWAY_URL = "http://127.0.0.1:18789"

# Test health check and auth
mco status

# List online agents
mco agents
```

---

## What You'll See

- **Header Status:** The top right shows a glowing green indicator stating:
  ```text
  Live • 127.0.0.1:18789 • local-operator (admin)
  ```
- **Real Data Sync:** The Job Board, Projects Dashboard, and Agent Fleet now display the actual rows from your local SQLite database (`~/.mco/local.db`).
- **Live WebSocket:** A real-time WebSocket connection is established to `/ws/broadcast`. Any job created by CLI or another agent immediately animates onto your screen without refreshing.

---

## If It Goes Wrong

### 1. "401 Unauthorized" or "Invalid token"
- **Cause:** Incomplete token pasted or mismatched local token.
- **Fix:** Copy the exact token from `~/.mco/.env`. Verify that there are no leading or trailing whitespace characters. Tokens always start with `mco_tok_`.

### 2. "Network Error: Failed to fetch"
- **Cause:** The BitCadence gateway server is stopped or running on a different port.
- **Fix:** Start the server using `Start BitCadence.bat` or `mco serve`. Confirm that `http://127.0.0.1:18789/healthz` returns `{"status":"ok"}`.

### 3. "Console reverts to Demo mode after page reload"
- **Cause:** Browser privacy settings or an incognito window blocked `localStorage`.
- **Fix:** Ensure cookies and local site storage are allowed for `127.0.0.1`. If using Private Browsing, re-enter the token.
