# Library Integration Scripts

A collection of independent applications and scripts used for integrations between FOLIO and different external services, such as Libris XL, and for automating different behaviours in FOLIO, such as automatic loan renewals and data deletions.

Each integration is maintained as a standalone project, with its own dependencies, configuration and command-line interface.

## Project structure

```text
library-integration-scripts/
│
├── packages/
│   ├── folio-client/
│   ├── libris-client/
│   └──  ...
│
├── integrations/
│   ├── circulation-renewals/
│   └── libris-import/
│   └── ...
│
├── workflows/
│   ├── circulation/
│   └── ...
│
└── README.md
```

## Packages

Reusable libraries shared by multiple integrations.

## Integrations

Independent applications that implement specific library workflows.

- **loan-renewals** – Automates eligible FOLIO loan renewals.
- **libris-import** – Fetches, validates and transforms Libris records before importing them into FOLIO.

Each integration has its own `pyproject.toml`, dependencies, virtual environment and CLI.

## Workflows

Kestra workflow definitions used to schedule, execute and monitor integrations.

Workflows are responsible for orchestration, including:

- Scheduling and triggering integrations.
- Executing individual processing stages.
- Passing files and results between tasks.
- Monitoring execution status and logs.
- Handling failures and retries.

Business logic remains in each integration projects.
