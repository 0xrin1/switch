# Diagrams

Mermaid diagrams in this folder are the single source of truth.

Some markdown files embed these diagrams by copying the Mermaid source between markers:

- `<!-- DIAGRAM:<name> -->`
- `<!-- /DIAGRAM:<name> -->`

Update the `.mermaid` file, then re-sync:

```bash
python3 scripts/sync-diagrams.py
```

This is also enforced by pre-commit (it will fail the commit if it had to update files).
