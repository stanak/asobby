# Windows code signing (optional)

GitHub Actions can Authenticode-sign the one-file `asobby.exe` when these repository secrets are set:

- `WINDOWS_CODESIGN_PFX_BASE64` — base64-encoded `.pfx` certificate
- `WINDOWS_CODESIGN_PFX_PASSWORD` — PFX password

Without them, releases are still built but remain unsigned. Signing is the most reliable way to reduce SmartScreen / Defender warnings.

Commercial code-signing certificates typically cost around USD 300–400 per year (DigiCert, Sectigo, SSL.com, etc.).

After each release you can also report false positives to Microsoft:
https://www.microsoft.com/en-us/wdsi/filesubmission
