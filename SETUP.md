# Local Development Setup

This repository uses one Python virtual environment at the repository root:

```text
BookFlowAI-Platform/
  .venv/
  scripts/
    requirements.txt
```

Do not create separate virtual environments under `scripts/` or other
subdirectories. Keeping one root `.venv` makes local scripts, IDE settings, and
team onboarding consistent.

## Windows PowerShell

From the repository root:

```powershell
.\scripts\setup-dev.ps1
```

Then activate the environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks script execution on your machine, run this once in the
current terminal session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then run the setup command again.

## Recreate The Environment

Use this when packages are corrupted or Python was upgraded:

```powershell
.\scripts\setup-dev.ps1 -Recreate
```

## Clean Old Local Environments

After the root `.venv` works, remove legacy local environments:

```powershell
.\scripts\setup-dev.ps1 -RemoveOldVenvs
```

This removes:

- `venv`
- `scripts\.venv`

## Manual Equivalent

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r .\scripts\requirements.txt
```
