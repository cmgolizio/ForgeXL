"""Actions: reusable, deterministic data-processing recipes.

`base` defines the contract every Action implements, `registry` is the single
place Actions are registered and looked up, and each remaining module in this
package implements exactly one Action.

This package deliberately contains no plugin loader and never executes code
read from disk at runtime. Actions are trusted application code that is
imported normally (build plan Phase 2.3).
"""