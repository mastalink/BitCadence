# BitCadence Bug & Usability Anomaly Inventory

This document details all reproducible bugs, edge-case regressions, and UI anomalies identified during the hands-on review. Per the review instructions, these bugs are documented with exact reproduction steps and impact assessments for resolution in follow-up redesign PRs.

---

### Bug 1: New Job Modal Backdrop Trapping & Escape Key Ineffectiveness

- **Severity:** High
- **Surface:** Web Console (`/console` — Job Board)
- **Component:** `src/mco/console_src/5adac14f-6645-4e02-866b-22c4e571989b.js` (`NewJobForm` / Modal Container)
- **Reproduction Steps:**
  1. Navigate to `http://127.0.0.1:18789/console`.
  2. Select the **Job Board** tab.
  3. Click **+ New job** to open the composer modal dialog.
  4. Press the `Escape` key on your keyboard to dismiss the modal, or click outside the dialog card on the dark backdrop.
  5. Attempt to click on any job row in the underlying table.
- **Expected Behavior:** Pressing `Escape` or clicking the backdrop should dismiss the modal and remove all overlay elements from the DOM.
- **Actual Behavior:** The dialog remains open or invisible backdrop `div` elements continue intercepting all mouse pointer events (`<div> subtree intercepts pointer events`), permanently freezing interaction with the underlying table until the explicit Cancel button is found and clicked.

---

### Bug 2: "Register Agent" Button Inexplicably Hidden in Demo Mode

- **Severity:** Medium
- **Surface:** Web Console (`/console` — Agent Fleet)
- **Component:** `src/mco/console_src/2ed3f6b1-e1c6-43fc-8b29-31917195cbd5.js:215`
- **Code Reference:**
  ```javascript
  {live ? (
    <Btn kind="primary" small onClick={() => setShowRegister((v) => !v)}>
      {showRegister ? "Cancel" : (tone === "plain" ? "Add agent" : "Register agent")}
    </Btn>
  ) : null}
  ```
- **Reproduction Steps:**
  1. Open the console in the default Demo Mode (`http://127.0.0.1:18789/console`).
  2. Click on the **Agent Fleet** tab.
  3. Observe the upper-right area of the screen where actions typically live.
- **Expected Behavior:** Users in Demo Mode should see a disabled or informative "Register agent" button explaining that agent registration is available once connected to a live server, or allow simulated registration.
- **Actual Behavior:** The button is completely omitted (`null`), leading first-time users to conclude that BitCadence has no graphical interface for registering agents.

---

### Bug 3: `Start BitCadence.bat` Blind Browser Launch on Startup Crash

- **Severity:** Medium
- **Surface:** Windows Launch Script (`Start BitCadence.bat:77`)
- **Code Reference:**
  ```bat
  start "" /b cmd /c "timeout /t 5 /nobreak >nul & start http://127.0.0.1:18789/console"
  ".venv\Scripts\python.exe" -m mco.cli serve
  ```
- **Reproduction Steps:**
  1. Introduce a port conflict or syntax error in `.env` so `mco serve` exits immediately on startup.
  2. Double-click `Start BitCadence.bat`.
  3. Observe the command window and browser behavior.
- **Expected Behavior:** The launch script should check if the server started successfully before launching the browser.
- **Actual Behavior:** The script spawns an unconditional background `cmd.exe` sleep timer. Five seconds after the server has already crashed, the browser pops up to `http://127.0.0.1:18789/console` displaying a confusing browser `ERR_CONNECTION_REFUSED` error page.

---

### Bug 4: Desktop Manager Window Hard-Crashes in Headless or Non-GUI Sessions

- **Severity:** Medium
- **Surface:** Desktop Manager (`src/mco/desktop/app.py:33`)
- **Reproduction Steps:**
  1. Invoke `python -m mco.desktop.app` from an SSH remote shell, Windows Service account session (session 0), or a container without an active Windows desktop display.
- **Expected Behavior:** The application should catch `TclError: no display name and no $DISPLAY environment variable` or GDI allocation errors, log an informative warning, and fall back to CLI mode (`mco status`).
- **Actual Behavior:** The process crashes immediately with an unhandled exception trace.

---

### Bug 5: String Escape Sequence Warning on Python 3.12+ for Windows Paths

- **Severity:** Low (Cosmetic Warning)
- **Surface:** Script Generation & Path Helpers
- **Reproduction Steps:**
  1. Run scripts that format Windows file paths like `C:\BitCadence` or `\BitCadence` using standard strings on Python 3.12+.
- **Expected Behavior:** Clean execution without interpreter warnings.
- **Actual Behavior:** Python emits:
  ```text
  SyntaxWarning: "\B" is an invalid escape sequence. Such sequences will not work in the future. Did you mean "\\B"? A raw string is also an option.
  ```

---

### Bug 6: Inconsistent Workflow Definitions Across Dual Flow Builders

- **Severity:** Medium
- **Surface:** `/console#workflows` vs `/flow#design`
- **Reproduction Steps:**
  1. Open `/console` and build a 3-step workflow in the Workflows tab.
  2. Navigate to `/flow` and click **Design workflow**.
  3. Inspect the canvas.
- **Expected Behavior:** Both visual workflow surfaces should share a unified local storage draft or server draft.
- **Actual Behavior:** The two builders operate with completely separate memory models. A user who spent 10 minutes designing a graph in one builder finds the other canvas completely empty.
