# Model Releases

Each model is stored under `models/<family>/<semantic-version>/`.

A release contains:

- one promoted `.traineddata` artifact
- `MODEL.md` with intended use, aggregate metrics, and data-distribution status
- `SHA256SUMS`

Experiment checkpoints and sample-level evaluation reports are not releases.
Never modify an existing version in place; add a new semantic version so
consumers can pin or roll back.
