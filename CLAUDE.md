## Project

This repository contains a data engineering pipeline for integrating environmental and regional datasets.

### Data Sources

The pipeline currently works with:

- TED public procurement data
- EEA air quality data
- Eurostat regional and agricultural data

### Technology

- Python
- `uv` for dependency and environment management

## General Development Rules

- Preserve the existing project architecture unless a structural change is explicitly required.
- Prefer simple, readable solutions over unnecessary abstractions.
- Make small, targeted changes.
- Do not refactor unrelated code while implementing a requested feature.
- Reuse existing common utilities instead of duplicating functionality.
- Keep source-specific logic inside the corresponding source module.
- Move functionality to shared or common modules only when it is genuinely reusable.
- Do not introduce new dependencies unless they are necessary.
- Use existing project dependencies whenever practical.

## Data Pipeline

### Ingestion Modes

Preserve the existing ingestion modes where applicable:

- `test`
- `historical`
- `refresh`

Do not silently change the semantics of existing modes.

### Data Handling

- Preserve existing raw data whenever possible.
- Do not overwrite or delete previously downloaded data unless explicitly required.
- Handle pagination, retries, API limits, empty responses, and partial downloads explicitly.
- Avoid loading unnecessarily large datasets into memory.
- Prefer incremental or batched processing for large downloads.
- Keep deduplication deterministic.
- Preserve state required for incremental ingestion.
- Do not change external API request semantics unless required by the task.

## Logging

Keep logging useful and concise.

### Log

- Request or fetch started
- Page or batch received
- File downloaded
- Number of records processed
- Number of records matched or skipped
- Output file written
- Errors and retries

### Avoid

- Complete request bodies unless required for debugging
- Complete response bodies unless required for debugging
- Repetitive logs that do not help diagnose pipeline behavior

When a long-running operation has measurable progress, show useful progress information when practical.

## Repository Hygiene

### Never Commit

- `.env`
- Secrets or credentials
- `.venv/`
- Logs
- Downloaded raw data
- Generated temporary files
- Local IDE files
- Operating-system-specific files

Always respect `.gitignore`.

Do not modify generated or data files unless the task explicitly requires it.

## Git Workflow

- Never modify `main` directly.
- Work through task or feature branches and pull requests.
- Keep commits small and focused.
- Do not combine unrelated changes into one commit.
- Commit only files related to the current task.
- Write all commit messages in English.
- Keep commit messages short and descriptive.
- Describe the functional change rather than implementation details.
- Prefer imperative commit messages.

### Commit Message Examples

- `Add EEA download progress`
- `Fix station pagination`
- `Simplify ingestion logging`
- `Add Eurostat NUTS mapping`
- `Handle empty API responses`

## Testing

- Run relevant automated tests when available.
- Run lightweight validation or sanity checks when appropriate.
- Do not claim that something works unless it was actually tested.
- If a test cannot be executed in the current environment, state that explicitly.
- Do not modify production behavior merely to make a test pass.
- When fixing a bug, preserve unrelated existing behavior.

## Scope Control

### Before Making Changes

1. Identify which files are relevant to the task.
2. Inspect the existing implementation before adding new functionality.
3. Prefer modifying existing functionality over creating parallel implementations.
4. Do not modify unrelated files.
5. Do not perform broad refactoring unless explicitly requested.

### After Making Changes

1. Review the diff.
2. Check for accidental or unrelated modifications.
3. Run relevant tests and checks.
4. Commit only the intended changes.
