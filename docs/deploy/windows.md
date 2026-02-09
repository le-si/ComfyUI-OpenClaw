# Windows Deployment Notes

Running ComfyUI + OpenClaw on Windows, especially with the Portable version.

## Environment Variables

### Portable Version (`run_nvidia_gpu.bat`)

To set OpenClaw security tokens in the portable version, edit your `run_nvidia_gpu.bat` (or create a wrapper `run_openclaw.bat`):

```bat
@echo off
:: Security Tokens
set OPENCLAW_CONNECTOR_ADMIN_TOKEN=my-secret-token
set MOLTBOT_OBSERVABILITY_TOKEN=observability-token

:: Run ComfyUI
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build
pause
```

### PowerShell

```powershell
$env:OPENCLAW_CONNECTOR_ADMIN_TOKEN="my-secret-token"
./python_embeded/python.exe -s ComfyUI/main.py
```

## Service Mode (NSSM)

If you want to run ComfyUI as a background service, use **NSSM** (Non-Sucking Service Manager).

1. Download NSSM.
2. `nssm install ComfyUI`
3. **Application**: Path to python.exe (or bat file).
4. **Environment**: Add tokens here in the Environment tab (Input: `KEY=VALUE` per line).
5. **I/O**: Redirect stdout/stderr to logs so you can debug startup issues.

## Caveats

- **Permissions**: Services run as `SYSTEM` by default. It is safer to create a dedicated user and set the service to Log On as that user.
- **GPU Access**: Ensure the user running the service has access to the GPU driver context (usually fine for logged-in users, tricky for headless services).
