# Design
Dispatch in extract.parse using extracted text; keep HDFC as the existing implementation.
Wells Fargo sections determine direction and activity type. Dates use posting dates relative
to statement period to resolve December/January. Hash account identifiers; do not store raw
account identifiers. Use reference-based identity for card rows; nullable balance since no
per-row balance is printed. Reconcile section detail to summary using Decimal, plus the
opening/activity/closing equation. Missing mandatory fields are errors. Fail extraction before
writing output if no valid transactions remain. Separate local USD card reports from HDFC
cash and recurring reports; reject mixed account types/currencies. Databricks remains HDFC-only.
