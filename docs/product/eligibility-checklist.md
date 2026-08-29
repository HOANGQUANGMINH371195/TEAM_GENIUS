# Eligibility Checklist contract

The checklist collects user circumstances; it never stores legal rates or
decides eligibility.

- Supported topics are benefit, five-year participation, referral, emergency
  and student contribution.
- The server owns a bounded field catalog and rejects unknown fact names.
- Conditional questions are activated only by supplied facts. For example, a
  non-emergency care event asks for referral status, and a confirmed referral
  asks for the document date.
- Raw fact values are not echoed in the response. The response contains only
  accepted field names and the remaining questions.
- When a valid conversation UUID is supplied, facts are stored in the
  owner-scoped `conversations.facts` JSONB object and the private cache is
  invalidated. A rolling deploy without the additive migration remains
  stateless and reports `facts_persisted=false`.
- `legal_retrieval_required` is always true: the active release must be queried
  after the checklist is complete.

Rollout flag: `FEATURE_ELIGIBILITY_ENABLED`. No checklist result may be used as
a citation or deterministic payment result.
