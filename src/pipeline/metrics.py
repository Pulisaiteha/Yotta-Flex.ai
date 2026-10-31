def summarize_counts(input_count, accepted, rejected, skipped):
    """Order reconciliation: every input order row must be accepted, rejected or skipped."""
    accounted_for = accepted + rejected + skipped
    return {
        "input_orders": input_count,
        "accepted_orders": accepted,
        "rejected_orders": rejected,
        "skipped_orders": skipped,
        "accounted_for_orders": accounted_for,
        "reconciliation_passed": input_count == accounted_for,
    }
