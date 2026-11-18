# Capability validation sandbox

Mia validates untrusted capability source in a dedicated Docker image. The
validation worker is deliberately separate from the assistant's trusted
runtime; it never imports a candidate into the running Mia process.

## Build the sandbox image

From the repository root, after installing Docker Desktop and starting its
Linux container engine. The initial image build needs network access to pull
the base image and download the Python development dependencies from PyPI.
This is a build-time requirement only; candidate execution uses
`--network=none`.

\`\`\`sh
docker build --pull -f sandbox/Dockerfile -t mia-sandbox:py313-v1 .
\`\`\`

The build image contains the repository's Python source, test suite, pytest,
pytest-asyncio, httpx and Ruff. Rebuild it after changing Mia's Python source or
tests so the regression suite corresponds to the current code. The root
\`.dockerignore\` excludes local environment files, databases, data and
generated workspaces from the build context.

## Runtime restrictions

The runner uses the locally built image by immutable image ID and starts one
disposable container per candidate. The container has:

- \`--network=none\`, so candidate and test code cannot reach the internet or LAN
- A read-only root filesystem and a single read-only bind mount containing only
  the candidate file and its test file
- Private IPC and cgroup namespaces, an init process, and no swap beyond the
  configured memory ceiling
- No added Linux capabilities and \`no-new-privileges\`
- A non-root UID/GID, a 64-process ceiling, bounded CPU and memory; staged
  host files are readable to UID 10001 but have read-only file and mount modes
- A small no-exec temporary filesystem for pytest scratch files
- No host environment variables, credentials, Docker socket or writable host
  directories

Candidate tests must be supplied as \`test_source_code\`. They import the module
as \`candidate\`. Missing tests fail the unit-test gate. The worker runs Ruff and
syntax parsing, a conservative AST denylist against both candidate and test
source, the supplied unit tests, and Mia's existing regression suite. Pytest
plugin auto-discovery is disabled; only the required pytest-asyncio plugin is
loaded explicitly. Each check's exit status and bounded output are
stored with the source/test hashes and container image ID.

## Limitations and release gates

The AST denylist is only defense in depth; it is not a proof that code is safe.
Docker isolation also depends on a correctly secured Docker Desktop/Linux
container engine and its kernel boundary. This runner validates candidates; it
does not grant capability permissions, register candidates, import them into
Mia, hot-activate code, or perform rollback. Those operations remain blocked
until a separate permission-separated worker, runtime health checks and a
tested rollback controller exist.

The default image is \`mia-sandbox:py313-v1\`. Override it with
\`MIA_SANDBOX_IMAGE\`; override the executable with \`MIA_DOCKER_EXECUTABLE\`.
No automatic image build or Docker daemon startup occurs. The Docker image ID
is recorded in each validation report so a later image-tag update cannot
silently change the provenance of an earlier result.


## Capability Lab

The Evolution Observatory exposes a Capability Lab form. It stores candidate
source and a dedicated test file as immutable versioned artifacts, shows their
hashes, and loads stored source through a hash-verifying read-only endpoint.
Use a new semantic version for any change; an existing version is never
overwritten. The interface can start sandbox validation and register versions
whose four validation gates all pass. It deliberately has no activation button.

## Capability API integration

Create a candidate with both `source_code` and `test_source_code`. The owner
starts validation with `POST /api/capabilities/{id}/{version}/validate/start`;
the API confirms that the configured image is available before moving the
candidate to `validating`. Calling `POST /api/capabilities/{id}/{version}/validate`
then runs the candidate and its tests in the Docker runner on the Mia host.
There is no client-supplied validation report or four-boolean override. The
stored report is accepted only when its sandbox identity and source/test hashes
match the immutable candidate records. If Docker is unavailable, validation
does not start (or remains retryable if the runtime disappears during a run).
