# Prepared development toolbox

The toolbox is an explicitly built dependency image, separate from the Sandbox
control runtime and from project sources. It supports Python web/API tests
(FastAPI, HTTPX, Pydantic), pytest/coverage/Ruff/mypy, numerical/data processing,
plots, spreadsheets, PDF/Word/PowerPoint processing and OCR, Node/npm/pnpm,
TypeScript/tsx, Git, HTTP/JSON utilities, C/C++ builds, SQLite, Graphviz, Poppler,
Tesseract and FFmpeg. See `docker/requirements.validation-toolbox.txt` for the
pinned Python inventory. Build tools and packages are available offline in jobs.

```sh
docker build -f docker/Dockerfile.validation-toolbox -t sandbox-toolbox:local .
docker image inspect sandbox-toolbox:local --format '{{.Id}}'
```

Building is a separate authorized network operation. Use reviewed immutable
`PYTHON_IMAGE` and `NODE_IMAGE` build arguments for distribution. Execute with
the resulting image ID. Python, npm and system inventories are stored under
`/opt/validation-{python,node,system}.freeze.*` in the image. Debian repository
updates and transitive dependencies can change future builds: the image ID and
recorded inventories, rather than the recipe alone, bind a tested environment.
No source, account or provider credentials are included.

The next Sandbox release adds optional requirements to validation requests:

```json
{
  "workspace": "/absolute/project",
  "image": "sha256:<reviewed-local-image-id>",
  "profile": "mixed",
  "argv": ["python", "-m", "pytest", "-q"],
  "requirements": {
    "python_modules": ["fastapi", "httpx", "pytest", "openpyxl"],
    "node_modules": ["typescript", "tsx"],
    "commands": ["git", "pnpm", "pdftotext", "tesseract", "ffmpeg"]
  }
}
```

`validation gate` and `validation run` check requirements inside the selected
image before the requested command. Python checks import modules; Node checks
resolve installed packages (including ESM packages); command checks find named
executables on PATH. No arbitrary paths, shell snippets or installation requests
are accepted. Imports can execute package initialization, always within the same
bounded network-off Docker isolation. Node resolution does not claim package
execution or browser availability. Broken imports fail readiness too.

Empty requirements preserve existing v1 receipt and operator-choice hashes;
nonempty requirements are bound into both identities. The MCP schema and
TypeScript SDK expose the same optional fields. Older 2.3 clients/runtimes may
reject these fields: upgrade/reload before using them. Omitted requirements
still check only the original runtime profile, not project dependencies.

No finite image includes every project dependency. Use a project-owned derived
image with reviewed lockfiles for Prisma clients, framework-specific packages,
browser binaries, databases/services or specialist ML libraries. No browser,
database daemon, model download or automatic dependency installation is implied
by this toolbox. The small `Dockerfile.validation` base remains available for
minimal environments. Existing REPL backends remain separately configured;
this toolbox does not silently change a running MCP server or its REPL image.
