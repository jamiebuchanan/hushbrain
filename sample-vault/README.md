# Sample vault

Thirteen invented notes about an invented garden centre. Nothing here is real: the names,
companies and the neighbour dispute are made up for the test suite and the demo.

| Notes | Label | Why it is here |
|---|---|---|
| 7 | `normal` | Visible by default. Most share the `garden-centre` project, which is what `project_context` returns. Their `edges` are parsed but not stored or queried yet. |
| 2 | `personal` | Hidden by default, shown with `include_sensitive`. |
| 1 | `legal` | Same, in a project the policy floors at `legal`. |
| 1 | none | An unlabelled note. It must be stored at the policy's fail-closed tier. |
| 1 | `persnal` | A misspelt label. It must fail closed too. |
| 1 | `normal` | Filed under a project the policy floors at `personal`, so the label is overridden upward. |

Each hidden note carries a `canary-...` marker in its text. The tests assert that no default
response contains a marker.

Use it with `examples/policy.example.toml`.
