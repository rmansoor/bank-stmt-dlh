# Requirements
- The local pipeline SHALL detect Wells Fargo text credit card statements.
- It SHALL validate all six activity sections and the balance equation before loading.
- Missing summaries, unrecognized activity and unsupported PDFs SHALL fail closed.
- Transaction identity SHALL include an opaque account key and reference, preserving repeated purchases.
- Duplicate statement content SHALL load once.
- USD card payments SHALL be transfers, refunds SHALL offset expenses, and card reports SHALL not present cash runway.
- Tests SHALL contain fictional data only. HDFC sample behavior SHALL remain unchanged.
