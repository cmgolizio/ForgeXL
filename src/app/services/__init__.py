"""Generic services shared by every Action.

Storage, parsing, run execution, preview and export land here in Phase 3;
the Run Store, which owns run state, joins them in Phase 6B.
Individual Actions must not reproduce this machinery (build plan section 24).
"""