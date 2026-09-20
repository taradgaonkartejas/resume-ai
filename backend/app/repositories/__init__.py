"""Repository layer.

Every method that reads user-owned data takes ``user_id`` as an argument.
There is deliberately no unscoped accessor for those tables, so a caller
cannot forget to scope a query.
"""
