Fix all issues identified in the code audit report across DeviantArt token persistence, Pixelfed status handling, ImgBB upload handling, Telegram notification formatting, and crosspost dispatcher documentation.

Working directory: d:\pinterst automation
Integrity mode: development

## Verification Resources
- Run existing automated test suite (pytest) and write new automated unit/mock tests covering all 5 fixes.

## Requirements

### R1. DeviantArt Token Persistence
Persist DeviantArt access and refresh tokens across Render service restarts using existing JSONBin synchronization and/or startup token refresh logic so that authentication state is preserved.

### R2. Pixelfed Status Handling
Update Pixelfed platform status handling to explicitly distinguish between disabled state and 401 unauthenticated state, and hide Pixelfed status reporting when the platform is disabled.

### R3. ImgBB Upload and Error Handling
Ensure ImgBB upload supports URL upload handling with base64 encoding fallback on ephemeral filesystems. Handle ImgBB API maintenance responses (HTTP 400 with "Imgbb is currently down for maintenance.", code 100) gracefully without raising unhandled errors or tripping false failure alerts.

### R4. Telegram Notification Formatting
Format Telegram notifications to display only active platforms with status indicators (✅/❌) and completely omit disabled platforms from the notification message.

### R5. Crosspost Dispatcher Documentation Cleanup
Clean up and update crosspost dispatcher documentation to ensure accuracy and alignment with the current dispatcher implementation.

## Acceptance Criteria

### Test Verification
- [ ] Existing automated test suite (pytest) passes.
- [ ] Automated unit or mock tests verify the functionality of all 5 fixes.

### Functional Guardrails
- [ ] DeviantArt tokens are preserved or restored across restarts via JSONBin / startup refresh without manual re-authentication.
- [ ] Pixelfed disabled state is distinct from 401 unauthenticated errors, and disabled Pixelfed instances are hidden from status output.
- [ ] ImgBB uploads fall back to base64 encoding when local file paths are not available on ephemeral filesystems.
- [ ] ImgBB API maintenance response (HTTP 400, "Imgbb is currently down for maintenance.", code 100) is caught and handled gracefully without breaking execution flow.
- [ ] Telegram notifications include only active platforms with ✅/❌ indicators, hiding all disabled platforms.
- [ ] Crosspost dispatcher documentation is clean, up-to-date, and accurately reflects current codebase behavior.
