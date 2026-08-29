# Typed BHYT ontology

Machine-readable contract: [`typed-bhyt-ontology.json`](typed-bhyt-ontology.json).
The JSON and this explanation must be reviewed together before any fact is
marked `accepted`.

The runtime currently stores canonical text in PostgreSQL and document
relationships in Neo4j. This contract defines the next projection without
making graph text authoritative.

Core types: `BeneficiaryGroup`, `ParticipationPeriod`, `CareEvent`,
`HospitalLevel`, `ReferralStatus`, `EmergencyCondition`, `CoverageRate`,
`CopaymentThreshold`, `Exclusion`, `EffectiveInterval`, and
`LegalProvision`. A fact must carry normalized value, effective interval,
jurisdiction, provision/document/unit identity, source span/hash, review
status, and release ID.

Only reviewed facts may drive the calculator. Every Neo4j fact/path is
re-hydrated to a matching PostgreSQL unit before it can become evidence.
